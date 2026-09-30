"""Independent Alertmanager-to-SMTP on-call receipt bridge.

Run on a separate host behind a public HTTPS reverse proxy. The bridge records
SMTP acceptance, then waits for a separately authenticated human acknowledgement.
It never emits a score-eligible receipt before both events have occurred.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import http.client
import json
import os
import re
import smtplib
import socket
import sqlite3
import ssl
import stat
import sys
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from email.utils import parseaddr
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any, Callable

from scripts.collect_prometheus_delivery_evidence import (
    ALERT_FINGERPRINT,
    DEFAULT_ONCALL_RECEIPT_PATH_PREFIX,
    DEFAULT_ONCALL_RECEIVER_NAME,
    DEFAULT_ONCALL_WEBHOOK_PATH,
    MAX_WEBHOOK_BODY_BYTES,
    ONCALL_RECEIPT_TYPE,
    SCHEMA_VERSION,
    SHA256_HEX,
    _resolve_public_https_origin,
    alert_name,
    expected_labels,
    oncall_webhook_event_sha256,
    strict_json,
    utc_now,
)


ACK_PATH_PREFIX = "/v1/market-sentinel/alert-acknowledgements"
COMMIT_SHA = re.compile(r"^[0-9a-f]{40}$")
RUN_PAIR = re.compile(r"^([1-9][0-9]*)\.([1-9][0-9]*)$")
TOKEN = re.compile(r"^[A-Za-z0-9\-._~+/]{32,4096}=*$")
DELIVERY_ID = re.compile(r"^[0-9a-f]{32}$")
MAX_ACK_BODY_BYTES = 512
MAX_ALERT_AGE = timedelta(minutes=10)


class BridgeError(ValueError):
    """Request failed validation or cannot advance its durable state."""


class DispatchError(BridgeError):
    """SMTP dispatch failed and Alertmanager should retry."""


def _sha256(value: bytes | str) -> str:
    if isinstance(value, str):
        value = value.encode("utf-8")
    return hashlib.sha256(value).hexdigest()


def _utc(value: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise BridgeError("startsAt must be a UTC timestamp ending in Z")
    try:
        parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError as exc:
        raise BridgeError("startsAt is not a valid timestamp") from exc
    return parsed.astimezone(timezone.utc)


def _private_file(path: Path, *, max_bytes: int = 4096) -> str:
    if not path.is_absolute() or any(parent.is_symlink() for parent in (path, *path.parents)):
        raise BridgeError("secret file must be an absolute path without symlinks")
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags)
    try:
        metadata = os.fstat(descriptor)
        if not stat.S_ISREG(metadata.st_mode):
            raise BridgeError("secret path must be a regular file")
        if os.name == "posix" and (
            metadata.st_uid != os.geteuid() or stat.S_IMODE(metadata.st_mode) != 0o600
        ):
            raise BridgeError("secret file must be owned by the service user with mode 0600")
        raw = os.read(descriptor, max_bytes + 1)
    finally:
        os.close(descriptor)
    if not 0 < len(raw) <= max_bytes:
        raise BridgeError("secret file is empty or too large")
    try:
        return raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise BridgeError("secret file is not UTF-8") from exc


def _mailbox(value: str) -> str:
    if not isinstance(value, str) or len(value) > 254 or any(char in value for char in "\r\n\x00"):
        raise BridgeError("SMTP mailbox is invalid")
    display, address = parseaddr(value)
    if display or address != value or "@" not in value:
        raise BridgeError("SMTP mailbox must be a bare email address")
    local, domain = value.rsplit("@", 1)
    if not local or not domain or "." not in domain or any(char.isspace() for char in value):
        raise BridgeError("SMTP mailbox must have a valid domain")
    return value


@dataclass(frozen=True)
class BridgeConfig:
    database: Path
    webhook_token: str
    acknowledgement_token: str
    acknowledger_identity: str
    smtp_host: str
    smtp_port: int
    smtp_username: str
    smtp_password: str
    smtp_from: str
    smtp_to: str
    smtp_mode: str = "starttls"
    listen_port: int = 19095

    def validate(self) -> None:
        if any(TOKEN.fullmatch(token) is None for token in (self.webhook_token, self.acknowledgement_token)):
            raise BridgeError("webhook and acknowledgement tokens must be distinct long bearer tokens")
        if hmac.compare_digest(self.webhook_token, self.acknowledgement_token):
            raise BridgeError("webhook and acknowledgement tokens must be different")
        if not self.acknowledger_identity or len(self.acknowledger_identity) > 256:
            raise BridgeError("acknowledger identity is missing")
        if self.smtp_mode not in {"starttls", "tls"} or not 1 <= self.smtp_port <= 65535:
            raise BridgeError("SMTP must use STARTTLS or implicit TLS on a valid port")
        if not self.smtp_host or any(char in self.smtp_host for char in "/:@ "):
            raise BridgeError("SMTP host is invalid")
        if not self.smtp_username or not self.smtp_password:
            raise BridgeError("SMTP authentication is required")
        _mailbox(self.smtp_from)
        _mailbox(self.smtp_to)
        if not 1 <= self.listen_port <= 65535:
            raise BridgeError("listen port is invalid")


def config_from_environment() -> BridgeConfig:
    names = {
        "database": "MARKET_SENTINEL_BRIDGE_DATABASE",
        "webhook_token": "MARKET_SENTINEL_BRIDGE_WEBHOOK_TOKEN_FILE",
        "acknowledgement_token": "MARKET_SENTINEL_BRIDGE_ACK_TOKEN_FILE",
        "smtp_password": "MARKET_SENTINEL_BRIDGE_SMTP_PASSWORD_FILE",
    }
    required = [*names.values(), "MARKET_SENTINEL_BRIDGE_ACK_IDENTITY", "MARKET_SENTINEL_BRIDGE_SMTP_HOST",
                "MARKET_SENTINEL_BRIDGE_SMTP_USERNAME", "MARKET_SENTINEL_BRIDGE_SMTP_FROM",
                "MARKET_SENTINEL_BRIDGE_SMTP_TO"]
    if any(not os.environ.get(name) for name in required):
        raise BridgeError("bridge configuration is incomplete")
    config = BridgeConfig(
        database=Path(os.environ[names["database"]]),
        webhook_token=_private_file(Path(os.environ[names["webhook_token"]])),
        acknowledgement_token=_private_file(Path(os.environ[names["acknowledgement_token"]])),
        acknowledger_identity=os.environ["MARKET_SENTINEL_BRIDGE_ACK_IDENTITY"],
        smtp_host=os.environ["MARKET_SENTINEL_BRIDGE_SMTP_HOST"],
        smtp_port=int(os.environ.get("MARKET_SENTINEL_BRIDGE_SMTP_PORT", "587")),
        smtp_username=os.environ["MARKET_SENTINEL_BRIDGE_SMTP_USERNAME"],
        smtp_password=_private_file(Path(os.environ[names["smtp_password"]])),
        smtp_from=os.environ["MARKET_SENTINEL_BRIDGE_SMTP_FROM"],
        smtp_to=os.environ["MARKET_SENTINEL_BRIDGE_SMTP_TO"],
        smtp_mode=os.environ.get("MARKET_SENTINEL_BRIDGE_SMTP_MODE", "starttls"),
        listen_port=int(os.environ.get("MARKET_SENTINEL_BRIDGE_LISTEN_PORT", "19095")),
    )
    config.validate()
    return config


def _parse_webhook(raw: bytes, *, now: datetime) -> dict[str, Any]:
    try:
        payload = strict_json(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise BridgeError("webhook body must be strict UTF-8 JSON") from exc
    if not isinstance(payload, dict) or payload.get("receiver") != DEFAULT_ONCALL_RECEIVER_NAME:
        raise BridgeError("webhook receiver does not match the dedicated on-call route")
    if payload.get("status") != "firing" or payload.get("version") != "4":
        raise BridgeError("webhook must be a firing Alertmanager v4 notification")
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or len(alerts) != 1 or not isinstance(alerts[0], dict):
        raise BridgeError("webhook must contain exactly one alert")
    alert = alerts[0]
    labels = alert.get("labels")
    annotations = alert.get("annotations")
    if not isinstance(labels, dict) or not isinstance(annotations, dict) or alert.get("status") != "firing":
        raise BridgeError("webhook alert fields are invalid")
    binding = labels.get("market_sentinel_binding")
    revision = labels.get("market_sentinel_revision")
    deployment = labels.get("market_sentinel_deployment")
    run_pair = labels.get("market_sentinel_run")
    fingerprint = alert.get("fingerprint")
    starts_at = alert.get("startsAt")
    if (
        not isinstance(binding, str) or SHA256_HEX.fullmatch(binding) is None
        or not isinstance(revision, str) or COMMIT_SHA.fullmatch(revision) is None
        or not isinstance(deployment, str) or SHA256_HEX.fullmatch(deployment) is None
        or not isinstance(run_pair, str) or RUN_PAIR.fullmatch(run_pair) is None
        or not isinstance(fingerprint, str) or ALERT_FINGERPRINT.fullmatch(fingerprint) is None
        or labels.get("market_sentinel_attestation") != "true"
        or labels.get("alertname") != alert_name(binding)
        or labels.get("severity") != "none"
        or annotations.get("attestation_binding") != binding
    ):
        raise BridgeError("webhook is not a well-formed Market Sentinel challenge alert")
    active_at = _utc(starts_at)
    if active_at > now + timedelta(seconds=60) or now - active_at > MAX_ALERT_AGE:
        raise BridgeError("webhook alert is outside the live challenge window")
    run_match = RUN_PAIR.fullmatch(run_pair)
    assert run_match is not None
    if labels != expected_labels(
        binding=binding,
        source_revision=revision,
        deployment_identity_sha256=deployment,
        run_id=int(run_match.group(1)),
        run_attempt=int(run_match.group(2)),
    ):
        raise BridgeError("webhook labels differ from the exact collector challenge")
    return {
        "binding_sha256": binding,
        "source_revision": revision,
        "deployment_identity_sha256": deployment,
        "run_id": int(run_match.group(1)),
        "run_attempt": int(run_match.group(2)),
        "alert_fingerprint": fingerprint,
        "webhook_receiver": DEFAULT_ONCALL_RECEIVER_NAME,
        "webhook_body_sha256": _sha256(raw),
        "webhook_event_sha256": oncall_webhook_event_sha256(
            labels=labels, alert_fingerprint=fingerprint, starts_at=starts_at
        ),
    }


class _PinnedSMTP(smtplib.SMTP):
    def __init__(self, host: str, port: int, *, pinned_addresses: tuple[str, ...]):
        self._pinned_addresses = pinned_addresses
        super().__init__(host, port, timeout=10)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        last_error: OSError | None = None
        for address in self._pinned_addresses:
            try:
                return socket.create_connection((address, port), timeout)
            except OSError as exc:
                last_error = exc
        raise last_error or OSError("SMTP relay has no reachable public address")


class _PinnedSMTPSSL(smtplib.SMTP_SSL):
    def __init__(self, host: str, port: int, *, pinned_addresses: tuple[str, ...], context: ssl.SSLContext):
        self._pinned_addresses = pinned_addresses
        super().__init__(host, port, timeout=10, context=context)

    def _get_socket(self, host: str, port: int, timeout: float) -> socket.socket:
        last_error: OSError | None = None
        for address in self._pinned_addresses:
            try:
                plain = socket.create_connection((address, port), timeout)
                try:
                    return self.context.wrap_socket(plain, server_hostname=host)
                except BaseException:
                    plain.close()
                    raise
            except OSError as exc:
                last_error = exc
        raise last_error or OSError("SMTP relay has no reachable public address")


def smtp_dispatch(config: BridgeConfig, record: dict[str, Any]) -> None:
    """A successful return means the authenticated SMTP relay accepted the email."""
    relay = _resolve_public_https_origin(
        f"https://{config.smtp_host}:{config.smtp_port}",
        "SMTP relay",
        resolver=socket.getaddrinfo,
    )
    message = EmailMessage()
    message["From"] = config.smtp_from
    message["To"] = config.smtp_to
    message["Subject"] = f"Market Sentinel production alert {record['binding_sha256'][:12]}"
    message["Message-ID"] = f"<{record['delivery_id']}@{config.smtp_from.rsplit('@', 1)[1]}>"
    message.set_content(
        "A Market Sentinel production alert reached the on-call bridge.\n\n"
        f"Challenge binding: {record['binding_sha256']}\n"
        f"Alert fingerprint: {record['alert_fingerprint']}\n"
        f"Delivery ID: {record['delivery_id']}\n\n"
        "After reviewing the incident, acknowledge it with the separate operator token "
        "using scripts.oncall_receipt_bridge acknowledge. This email contains no acknowledgement credential.\n"
    )
    context = ssl.create_default_context()
    if config.smtp_mode == "tls":
        client = _PinnedSMTPSSL(
            config.smtp_host, config.smtp_port, pinned_addresses=relay.pinned_addresses, context=context
        )
    else:
        client = _PinnedSMTP(config.smtp_host, config.smtp_port, pinned_addresses=relay.pinned_addresses)
    with client:
        client.ehlo()
        if config.smtp_mode == "starttls":
            if not client.has_extn("starttls"):
                raise BridgeError("SMTP relay does not offer STARTTLS")
            client.starttls(context=context)
            client.ehlo()
        client.login(config.smtp_username, config.smtp_password)
        refused = client.send_message(message)
        if refused:
            raise BridgeError("SMTP relay refused the on-call recipient")


class ReceiptBridge:
    def __init__(self, config: BridgeConfig, *, dispatch: Callable[[BridgeConfig, dict[str, Any]], None] = smtp_dispatch):
        config.validate()
        self.config = config
        self.dispatch = dispatch
        if not config.database.is_absolute() or any(
            item.is_symlink() for item in (config.database, *config.database.parents)
        ):
            raise BridgeError("database path must be absolute and contain no symlinks")
        if os.name == "posix":
            parent = config.database.parent
            mode = stat.S_IMODE(parent.stat().st_mode)
            if parent.stat().st_uid != os.geteuid() or mode & 0o077:
                raise BridgeError("database directory must be service-owned and private")
            try:
                descriptor = os.open(
                    config.database, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600
                )
            except FileExistsError:
                metadata = config.database.stat()
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != os.geteuid()
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise BridgeError("database file must be service-owned and mode 0600") from None
            else:
                os.close(descriptor)
        self._lock = None
        if os.name == "posix":
            import fcntl

            lock_path = config.database.with_suffix(config.database.suffix + ".lock")
            lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            try:
                fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                os.close(lock_descriptor)
                raise BridgeError("another on-call bridge owns this database") from None
            self._lock = lock_descriptor
        self.db = sqlite3.connect(config.database, isolation_level="IMMEDIATE", check_same_thread=False)
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.execute("""CREATE TABLE IF NOT EXISTS deliveries (
            binding TEXT PRIMARY KEY, event_sha256 TEXT NOT NULL, body_sha256 TEXT NOT NULL,
            record_json TEXT NOT NULL, state TEXT NOT NULL, received_at TEXT NOT NULL,
            dispatched_at TEXT, channel_sha256 TEXT, acknowledged_at TEXT, acknowledger_sha256 TEXT
        )""")
        self.db.execute("""CREATE TABLE IF NOT EXISTS audit_events (
            id INTEGER PRIMARY KEY, binding TEXT NOT NULL, event TEXT NOT NULL,
            occurred_at TEXT NOT NULL
        )""")

    def close(self) -> None:
        self.db.close()
        if self._lock is not None:
            os.close(self._lock)
            self._lock = None

    def accept_webhook(self, raw: bytes) -> dict[str, str]:
        if not raw or len(raw) > MAX_WEBHOOK_BODY_BYTES:
            raise BridgeError("webhook body is empty or too large")
        received = utc_now()
        identity = _parse_webhook(raw, now=_utc(received))
        binding = identity["binding_sha256"]
        row = self.db.execute(
            "SELECT event_sha256, record_json, state FROM deliveries WHERE binding=?", (binding,)
        ).fetchone()
        if row is None:
            record = {**identity, "delivery_id": uuid.uuid4().hex}
            with self.db:
                self.db.execute(
                    "INSERT INTO deliveries(binding,event_sha256,body_sha256,record_json,state,received_at) "
                    "VALUES (?, ?, ?, ?, 'pending', ?)",
                    (binding, identity["webhook_event_sha256"], identity["webhook_body_sha256"],
                     json.dumps(record, sort_keys=True, separators=(",", ":")), received),
                )
                self.db.execute(
                    "INSERT INTO audit_events(binding,event,occurred_at) VALUES (?, 'received', ?)",
                    (binding, received),
                )
        else:
            event_sha, serialized, state = row
            if event_sha != identity["webhook_event_sha256"]:
                raise BridgeError("challenge binding was already used by a different webhook")
            record = json.loads(serialized)
            if state in {"dispatched", "acknowledged"}:
                return {"delivery_id": record["delivery_id"], "status": state}
        dispatch_config = self.config
        try:
            self.dispatch(dispatch_config, record)
        except (OSError, smtplib.SMTPException, ValueError, socket.timeout) as exc:
            # The durable pending row permits a retry with the same Message-ID.
            raise DispatchError("on-call SMTP dispatch failed; Alertmanager should retry") from exc
        dispatched = utc_now()
        with self.db:
            self.db.execute(
                "UPDATE deliveries SET state='dispatched', dispatched_at=?, channel_sha256=? "
                "WHERE binding=? AND state='pending'",
                (dispatched, _sha256(dispatch_config.smtp_to.lower()), binding),
            )
            self.db.execute(
                "INSERT INTO audit_events(binding,event,occurred_at) VALUES (?, 'dispatched', ?)",
                (binding, dispatched),
            )
        return {"delivery_id": record["delivery_id"], "status": "dispatched"}

    def acknowledge(self, binding: str, delivery_id: str) -> None:
        if SHA256_HEX.fullmatch(binding) is None or DELIVERY_ID.fullmatch(delivery_id) is None:
            raise BridgeError("acknowledgement identity is invalid")
        row = self.db.execute(
            "SELECT record_json, state, dispatched_at FROM deliveries WHERE binding=?", (binding,)
        ).fetchone()
        if row is None:
            raise BridgeError("challenge has not been delivered")
        record, state, dispatched = json.loads(row[0]), row[1], row[2]
        if record["delivery_id"] != delivery_id:
            raise BridgeError("delivery ID does not match the challenge")
        if state == "acknowledged":
            return
        if state != "dispatched" or dispatched is None:
            raise BridgeError("challenge has not been accepted by SMTP")
        acknowledged = utc_now()
        if _utc(acknowledged) <= _utc(dispatched):
            raise BridgeError("acknowledgement must follow dispatch")
        with self.db:
            self.db.execute(
                "UPDATE deliveries SET state='acknowledged', acknowledged_at=?, acknowledger_sha256=? "
                "WHERE binding=? AND state='dispatched'",
                (acknowledged, _sha256(self.config.acknowledger_identity), binding),
            )
            self.db.execute(
                "INSERT INTO audit_events(binding,event,occurred_at) VALUES (?, 'human_acknowledged', ?)",
                (binding, acknowledged),
            )

    def receipt(self, binding: str) -> dict[str, Any] | None:
        if SHA256_HEX.fullmatch(binding) is None:
            raise BridgeError("receipt binding is invalid")
        row = self.db.execute(
            "SELECT record_json, state, received_at, dispatched_at, acknowledged_at, acknowledger_sha256, "
            "channel_sha256 "
            "FROM deliveries WHERE binding=?", (binding,)
        ).fetchone()
        if row is None or row[1] != "acknowledged":
            return None
        record = json.loads(row[0])
        return {
            **record,
            "schema_version": SCHEMA_VERSION,
            "receipt_type": ONCALL_RECEIPT_TYPE,
            "status": "acknowledged",
            "acknowledgement_kind": "human",
            "oncall_provider": "smtp",
            "oncall_channel_sha256": row[6],
            "received_at": row[2],
            "dispatched_at": row[3],
            "acknowledged_at": row[4],
            "acknowledger_sha256": row[5],
        }


def make_handler(bridge: ReceiptBridge) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def setup(self) -> None:
            super().setup()
            self.connection.settimeout(10)

        def log_message(self, format: str, *args: Any) -> None:
            # No request headers, body, URL, channel, or credentials in service logs.
            pass

        def _reply(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self, token: str) -> bool:
            return hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}")

        def _body(self, limit: int) -> bytes:
            if self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json":
                raise BridgeError("Content-Type must be application/json")
            content_length = self.headers.get("Content-Length", "")
            if not content_length.isdecimal() or not 0 < int(content_length) <= limit:
                raise BridgeError("request body length is invalid")
            return self.rfile.read(int(content_length))

        def do_POST(self) -> None:
            if self.path == DEFAULT_ONCALL_WEBHOOK_PATH:
                if not self._authorized(bridge.config.webhook_token):
                    self._reply(401, {"error": "unauthorized"})
                    return
                try:
                    result = bridge.accept_webhook(self._body(MAX_WEBHOOK_BODY_BYTES))
                except BridgeError as exc:
                    status = 503 if isinstance(exc, DispatchError) else 400
                    self._reply(status, {"error": str(exc)})
                    return
                self._reply(202, result)
                return
            binding = self.path.removeprefix(ACK_PATH_PREFIX + "/")
            if self.path.startswith(ACK_PATH_PREFIX + "/") and SHA256_HEX.fullmatch(binding):
                if not self._authorized(bridge.config.acknowledgement_token):
                    self._reply(401, {"error": "unauthorized"})
                    return
                try:
                    payload = strict_json(self._body(MAX_ACK_BODY_BYTES).decode("utf-8"))
                    if not isinstance(payload, dict) or set(payload) != {"delivery_id", "acknowledgement"}:
                        raise BridgeError("acknowledgement body fields are invalid")
                    if payload["acknowledgement"] != "human":
                        raise BridgeError("human acknowledgement is required")
                    bridge.acknowledge(binding, payload["delivery_id"])
                except (BridgeError, UnicodeDecodeError, ValueError, json.JSONDecodeError) as exc:
                    self._reply(400, {"error": str(exc)})
                    return
                self._reply(200, {"status": "acknowledged"})
                return
            self._reply(404, {"error": "not found"})

        def do_GET(self) -> None:
            binding = self.path.removeprefix(DEFAULT_ONCALL_RECEIPT_PATH_PREFIX + "/")
            if not self.path.startswith(DEFAULT_ONCALL_RECEIPT_PATH_PREFIX + "/") or SHA256_HEX.fullmatch(binding) is None:
                self._reply(404, {"error": "not found"})
                return
            if not self._authorized(bridge.config.webhook_token):
                self._reply(401, {"error": "unauthorized"})
                return
            receipt = bridge.receipt(binding)
            self._reply(200, receipt) if receipt is not None else self._reply(404, {"error": "not acknowledged"})

    return Handler


def _acknowledge_cli(args: argparse.Namespace) -> int:
    from scripts.collect_prometheus_delivery_evidence import canonical_public_https_origin

    origin = canonical_public_https_origin(args.origin, "on-call receipt origin")
    if SHA256_HEX.fullmatch(args.binding) is None or DELIVERY_ID.fullmatch(args.delivery_id) is None:
        raise BridgeError("binding and delivery ID are invalid")
    if not sys.stdin.isatty():
        raise BridgeError("acknowledgement requires an interactive operator terminal")
    confirmation = f"ACK {args.binding[:12]} {args.delivery_id}"
    if input(f"Type {confirmation} after reviewing the incident: ") != confirmation:
        raise BridgeError("operator did not confirm the acknowledgement")
    token = _private_file(args.token_file)
    if TOKEN.fullmatch(token) is None:
        raise BridgeError("acknowledgement token is invalid")
    hostname = origin.removeprefix("https://")
    body = json.dumps({"delivery_id": args.delivery_id, "acknowledgement": "human"}).encode("utf-8")
    connection = http.client.HTTPSConnection(hostname, timeout=10, context=ssl.create_default_context())
    try:
        connection.request(
            "POST", f"{ACK_PATH_PREFIX}/{args.binding}", body=body,
            headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        )
        response = connection.getresponse()
        response.read(1024)
        if response.status != 200:
            raise BridgeError(f"acknowledgement endpoint returned HTTP {response.status}")
    finally:
        connection.close()
    print("On-call alert acknowledged.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("serve", help="run loopback HTTP service behind HTTPS reverse proxy")
    ack = subparsers.add_parser("acknowledge", help="human operator acknowledgement")
    ack.add_argument("--origin", required=True)
    ack.add_argument("--binding", required=True)
    ack.add_argument("--delivery-id", required=True)
    ack.add_argument("--token-file", required=True, type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == "acknowledge":
            return _acknowledge_cli(args)
        bridge = ReceiptBridge(config_from_environment())
        try:
            with HTTPServer(("127.0.0.1", bridge.config.listen_port), make_handler(bridge)) as server:
                server.serve_forever()
        finally:
            bridge.close()
        return 0
    except (BridgeError, OSError, ValueError) as exc:
        parser.exit(2, f"on-call bridge: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
