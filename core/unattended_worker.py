from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .atomic_files import atomic_write_text
from .json_validation import loads_strict_json
from .storage import (
    ConfigCommitError,
    ConfigConflictError,
    ConfigLoadError,
    default_config_path,
    load_config,
    save_config,
)


SCHEMA_VERSION = 1
TASK_ALERTS = "alerts-refresh"
TASK_WALLETS = "wallets-poll"
TASKS = (TASK_ALERTS, TASK_WALLETS)
SERVICE_UNIT_BY_TASK = {
    TASK_ALERTS: "market-sentinel-alerts-refresh.service",
    TASK_WALLETS: "market-sentinel-wallets-poll.service",
}
SOURCE_REVISION_PATTERN = re.compile(r"^[0-9a-f]{40}$")
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
MAX_STATE_BYTES = 64 * 1024
MAX_CAPTURE_BYTES = 16 * 1024
ATTEMPT_SHUTDOWN_BUDGET_SECONDS = 6.0
FUTURE_CLOCK_TOLERANCE_SECONDS = 5.0

EXIT_OK = 0
EXIT_USAGE = 64
EXIT_SOFTWARE = 70
EXIT_IOERR = 74
EXIT_TEMPFAIL = 75

_FORBIDDEN_WORKER_ENVIRONMENT_KEYS = (
    "MARKET_SENTINEL_API_TOKEN",
    "MARKET_SENTINEL_OBSERVABILITY_TOKEN",
    "PRIVATE_KEY",
    "POLYMARKET_PRIVATE_KEY",
    "POLY_API_SECRET",
    "POLY_SECRET",
    "POLY_PASSPHRASE",
    "POLY_SIGNATURE",
    "POLY_BUILDER_API_KEY",
    "POLY_BUILDER_SECRET",
    "POLY_BUILDER_PASSPHRASE",
    "POLY_BUILDER_SIGNATURE",
    "RELAYER_API_KEY",
    "RELAYER_API_KEY_ADDRESS",
    "KALSHI_PRIVATE_KEY_PATH",
    "KALSHI_PRIVATE_KEY_PEM",
    "KALSHI_PRIVATE_KEY_PASSWORD",
    "OPINION_PRIVATE_KEY",
    "SX_BET_PRIVATE_KEY",
    "GEMINI_API_SECRET",
    "XO_API_SECRET",
    "PROB_API_SECRET",
    "PROBABLE_API_SECRET",
    "PROB_PASSPHRASE",
    "PROBABLE_API_PASSPHRASE",
    "LIMITLESS_TOKEN_SECRET",
    "PROPHET_EXCHANGE_SECRET_KEY",
    "SSLKEYLOGFILE",
    "PYTHONHOME",
    "PYTHONINSPECT",
    "PYTHONSTARTUP",
    "PYTHONBREAKPOINT",
)
_ALLOWED_WORKER_ENVIRONMENT_KEYS = frozenset(
    {
        # Minimum process, locale, temporary-file, proxy, and trust-store
        # settings needed by the isolated Python child on supported hosts.
        "ALL_PROXY",
        "COMSPEC",
        "CURL_CA_BUNDLE",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "NO_PROXY",
        "PATH",
        "PATHEXT",
        "REQUESTS_CA_BUNDLE",
        "SSL_CERT_DIR",
        "SSL_CERT_FILE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "TMPDIR",
        "TZ",
        "WINDIR",
        # Only credential names documented by the dedicated worker environment
        # are admitted. Signing, trading, session, and admin secrets never pass
        # through merely because a future name was omitted from a deny-list.
        "CONTEXT_API_KEY",
        "CRYPTO_COM_PREDICTIONS_API_KEY",
        "DFLOW_API_KEY",
        "DRAFTKINGS_PREDICTIONS_API_KEY",
        "FANDUEL_PREDICTS_API_KEY",
        "GJOPEN_API_TOKEN",
        "MANIFOLD_API_KEY",
        "METACULUS_API_TOKEN",
        "NADEX_PREDICTIONS_API_KEY",
        "OPINION_API_KEY",
        "PREDICT_FUN_API_KEY",
        "SCICAST_API_KEY",
        "XMARKET_API_KEY",
    }
)


class WorkerStateError(RuntimeError):
    """The durable worker status cannot be read or written safely."""


class WorkerBusyError(RuntimeError):
    """Another unattended worker owns the shared serialization lock."""


class WorkerInterrupted(RuntimeError):
    """The worker was asked to stop before completing its operation."""


@dataclass(frozen=True)
class AttemptResult:
    task: str
    outcome: str
    retryable: bool
    processed: int = 0
    problems: int = 0
    emitted: int = 0
    exit_code: int = EXIT_OK

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": SCHEMA_VERSION,
            "task": self.task,
            "outcome": self.outcome,
            "retryable": self.retryable,
            "processed": self.processed,
            "problems": self.problems,
            "emitted": self.emitted,
            "exit_code": self.exit_code,
        }


class ShutdownRequest:
    def __init__(self) -> None:
        self.event = threading.Event()
        self.signal_number: int | None = None

    def request(self, signal_number: int) -> None:
        if self.signal_number is None:
            self.signal_number = int(signal_number)
        self.event.set()

    @property
    def requested(self) -> bool:
        return self.event.is_set()


def _utc_timestamp(timestamp: float) -> str:
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def worker_invocation_sha256(
    *,
    task: str,
    service_unit: str,
    source_revision: str,
    unit_contract_sha256: str,
) -> str:
    """Bind durable telemetry to one reviewed unit contract and release revision."""

    payload = {
        "schema_version": 1,
        "service_unit": service_unit,
        "source_revision": source_revision,
        "task": task,
        "unit_contract_sha256": unit_contract_sha256,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(b"market-sentinel-worker-invocation-v1\0" + canonical.encode("ascii")).hexdigest()


def _journal_event(event: str, **fields: Any) -> None:
    payload = {"component": "market-sentinel-unattended-worker", "event": event, **fields}
    print(json.dumps(payload, separators=(",", ":"), sort_keys=True), file=sys.stderr, flush=True)


def _regular_file_bytes(path: Path, *, maximum: int) -> bytes | None:
    try:
        before = path.lstat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(before.st_mode):
        raise WorkerStateError(f"Worker state must be a regular file: {path}")
    if before.st_size > maximum:
        raise WorkerStateError(f"Worker state exceeds the {maximum}-byte safety limit: {path}")
    try:
        with path.open("rb") as handle:
            opened = os.fstat(handle.fileno())
            if not stat.S_ISREG(opened.st_mode):
                raise WorkerStateError(f"Worker state must be a regular file: {path}")
            if (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino):
                raise WorkerStateError(f"Worker state changed identity while opening it: {path}")
            raw = handle.read(maximum + 1)
            after = os.fstat(handle.fileno())
    except OSError as exc:
        raise WorkerStateError(f"Worker state could not be read safely: {path}") from exc
    if len(raw) > maximum:
        raise WorkerStateError(f"Worker state exceeds the {maximum}-byte safety limit: {path}")
    if (
        (opened.st_dev, opened.st_ino) != (after.st_dev, after.st_ino)
        or opened.st_size != after.st_size
        or opened.st_mtime_ns != after.st_mtime_ns
    ):
        raise WorkerStateError(f"Worker state changed while it was being read: {path}")
    return raw


def read_worker_state(path: Path) -> dict[str, Any]:
    raw = _regular_file_bytes(path, maximum=MAX_STATE_BYTES)
    if raw is None:
        return {"schema_version": SCHEMA_VERSION, "tasks": {}}
    try:
        decoded = loads_strict_json(raw.decode("utf-8"))
    except Exception as exc:
        raise WorkerStateError(f"Worker state is not valid strict JSON: {path}") from exc
    if not isinstance(decoded, dict):
        raise WorkerStateError(f"Worker state root must be a JSON object: {path}")
    if decoded.get("schema_version") != SCHEMA_VERSION:
        raise WorkerStateError(f"Worker state schema is unsupported: {path}")
    tasks = decoded.get("tasks")
    if not isinstance(tasks, dict):
        raise WorkerStateError(f"Worker state tasks must be a JSON object: {path}")
    for task, entry in tasks.items():
        if task not in TASKS or not isinstance(entry, dict):
            raise WorkerStateError(f"Worker state contains an invalid task entry: {path}")
    return decoded


def write_worker_state(path: Path, state: Mapping[str, Any]) -> None:
    existing = _regular_file_bytes(path, maximum=MAX_STATE_BYTES)
    del existing  # The safety check intentionally happens immediately before replacement.
    payload = json.dumps(dict(state), indent=2, sort_keys=True, allow_nan=False) + "\n"
    encoded = payload.encode("utf-8")
    if len(encoded) > MAX_STATE_BYTES:
        raise WorkerStateError(f"Worker state exceeds the {MAX_STATE_BYTES}-byte safety limit: {path}")
    try:
        atomic_write_text(path, payload)
    except OSError as exc:
        raise WorkerStateError(f"Worker state could not be committed durably: {path}") from exc


def _update_task_state(path: Path, task: str, entry: Mapping[str, Any], now: float) -> None:
    state = read_worker_state(path)
    tasks = dict(state["tasks"])
    tasks[task] = dict(entry)
    state.update(
        {
            "schema_version": SCHEMA_VERSION,
            "updated_at": _utc_timestamp(now),
            "updated_at_unix": now,
            "tasks": tasks,
        }
    )
    write_worker_state(path, state)


def _number(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _integer(value: Any, default: int = 0) -> int:
    if isinstance(value, bool):
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _initial_running_entry(
    previous: Mapping[str, Any],
    *,
    task: str,
    run_id: str,
    now: float,
    deadline_seconds: float,
    max_attempts: int,
    source_revision: str,
    service_unit: str,
    unit_contract_sha256: str,
) -> dict[str, Any]:
    abandoned = previous.get("state") in {"running", "retrying"}
    consecutive_failures = max(0, _integer(previous.get("consecutive_failures")))
    abandoned_runs = max(0, _integer(previous.get("abandoned_runs")))
    entry: dict[str, Any] = {
        "state": "running",
        "run_id": run_id,
        "pid": os.getpid(),
        "last_started_at": _utc_timestamp(now),
        "last_started_at_unix": now,
        "deadline_seconds": deadline_seconds,
        "max_attempts": max_attempts,
        "source_revision": source_revision,
        "service_unit": service_unit,
        "unit_contract_sha256": unit_contract_sha256,
        "invocation_sha256": worker_invocation_sha256(
            task=task,
            service_unit=service_unit,
            source_revision=source_revision,
            unit_contract_sha256=unit_contract_sha256,
        ),
        "attempts_completed": 0,
        "consecutive_failures": consecutive_failures + (1 if abandoned else 0),
        "abandoned_runs": abandoned_runs + (1 if abandoned else 0),
    }
    for key in (
        "last_success_at",
        "last_success_at_unix",
        "last_failure_at",
        "last_failure_at_unix",
        "total_runs",
        "total_successes",
        "total_failures",
    ):
        if key in previous:
            entry[key] = previous[key]
    if abandoned:
        entry["previous_run_outcome"] = "abandoned_without_terminal_telemetry"
        entry["last_failure_at"] = _utc_timestamp(now)
        entry["last_failure_at_unix"] = now
    return entry


def _attempt_state_entry(
    running: Mapping[str, Any],
    result: AttemptResult,
    *,
    attempts_completed: int,
    now: float,
    next_retry_at: float | None,
) -> dict[str, Any]:
    entry = dict(running)
    entry.update(
        {
            "state": "retrying" if next_retry_at is not None else "running",
            "attempts_completed": attempts_completed,
            "last_attempt_at": _utc_timestamp(now),
            "last_attempt_at_unix": now,
            "last_attempt_outcome": result.outcome,
            "last_attempt_processed": result.processed,
            "last_attempt_problems": result.problems,
            "last_attempt_emitted": result.emitted,
        }
    )
    if next_retry_at is not None:
        entry["next_retry_at"] = _utc_timestamp(next_retry_at)
        entry["next_retry_at_unix"] = next_retry_at
    else:
        entry.pop("next_retry_at", None)
        entry.pop("next_retry_at_unix", None)
    return entry


def _terminal_state_entry(
    running: Mapping[str, Any],
    result: AttemptResult,
    *,
    attempts_completed: int,
    started: float,
    now: float,
) -> dict[str, Any]:
    entry = dict(running)
    success = result.outcome == "succeeded"
    interrupted = result.outcome == "interrupted"
    total_runs = max(0, _integer(entry.get("total_runs"))) + 1
    total_successes = max(0, _integer(entry.get("total_successes"))) + (1 if success else 0)
    total_failures = max(0, _integer(entry.get("total_failures"))) + (0 if success else 1)
    entry.update(
        {
            "state": "succeeded" if success else ("interrupted" if interrupted else "failed"),
            "attempts_completed": attempts_completed,
            "last_finished_at": _utc_timestamp(now),
            "last_finished_at_unix": now,
            "last_duration_seconds": round(max(0.0, now - started), 6),
            "last_outcome": result.outcome,
            "last_processed": result.processed,
            "last_problems": result.problems,
            "last_emitted": result.emitted,
            "total_runs": total_runs,
            "total_successes": total_successes,
            "total_failures": total_failures,
            "consecutive_failures": 0 if success else max(0, _integer(entry.get("consecutive_failures"))) + 1,
        }
    )
    entry.pop("next_retry_at", None)
    entry.pop("next_retry_at_unix", None)
    if success:
        entry["last_success_at"] = _utc_timestamp(now)
        entry["last_success_at_unix"] = now
    else:
        entry["last_failure_at"] = _utc_timestamp(now)
        entry["last_failure_at_unix"] = now
    return entry


def _lock_descriptor(path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise WorkerStateError(f"Worker lock could not be opened safely: {path}") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise WorkerStateError(f"Worker lock must be a regular file: {path}")
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        if os.name == "nt" and details.st_size < 1:
            os.write(descriptor, b"0")
            os.fsync(descriptor)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def exclusive_worker_lock(
    path: Path,
    *,
    timeout_seconds: float,
    shutdown: ShutdownRequest,
) -> Iterator[None]:
    descriptor = _lock_descriptor(path)
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    acquired = False
    try:
        while not acquired:
            if shutdown.requested:
                raise WorkerInterrupted("Worker lock acquisition was interrupted.")
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(descriptor, 0, os.SEEK_SET)
                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except (BlockingIOError, OSError) as exc:
                if time.monotonic() >= deadline:
                    raise WorkerBusyError("Another unattended worker still owns the shared lock.") from exc
                shutdown.event.wait(min(0.05, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        if acquired:
            if os.name == "nt":
                import msvcrt

                os.lseek(descriptor, 0, os.SEEK_SET)
                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


@contextmanager
def _shutdown_handlers(shutdown: ShutdownRequest) -> Iterator[None]:
    if threading.current_thread() is not threading.main_thread():
        yield
        return
    handled = [signal.SIGINT, signal.SIGTERM]
    previous: dict[int, Any] = {}

    def request_stop(signum: int, _frame: Any) -> None:
        shutdown.request(signum)

    try:
        for signum in handled:
            previous[signum] = signal.getsignal(signum)
            signal.signal(signum, request_stop)
        yield
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def _attempt_child_environment() -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in _ALLOWED_WORKER_ENVIRONMENT_KEYS
    }
    # The unattended tasks are read-only apart from their local CAS state. A
    # dedicated unit environment should not contain mutation credentials, and
    # this second boundary prevents accidental inheritance from the service
    # manager or an interactive invocation.
    for key in _FORBIDDEN_WORKER_ENVIRONMENT_KEYS:
        environment.pop(key, None)
    environment["PYTHONUNBUFFERED"] = "1"
    return environment


def _attempt_command(task: str, config_path: Path, wallet_limit: int) -> list[str]:
    return [
        sys.executable,
        "-m",
        "core.unattended_worker",
        "_attempt",
        "--task",
        task,
        "--config",
        str(config_path),
        "--wallet-limit",
        str(wallet_limit),
    ]


def _terminate_process(process: subprocess.Popen[bytes], grace_seconds: float = 5.0) -> None:
    if process.poll() is not None:
        return
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGTERM)
        else:
            process.terminate()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=max(0.0, grace_seconds))
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        if os.name == "posix":
            os.killpg(process.pid, signal.SIGKILL)
        else:
            process.kill()
    except (OSError, ProcessLookupError):
        pass
    try:
        process.wait(timeout=1.0)
    except subprocess.TimeoutExpired:
        pass


def _decode_attempt_result(task: str, output: bytes, return_code: int) -> AttemptResult:
    if len(output) > MAX_CAPTURE_BYTES:
        return AttemptResult(task, "invalid_attempt_result", True, exit_code=EXIT_TEMPFAIL)
    try:
        payload = loads_strict_json(output.decode("utf-8"))
    except Exception:
        return AttemptResult(task, "invalid_attempt_result", True, exit_code=EXIT_TEMPFAIL)
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION or payload.get("task") != task:
        return AttemptResult(task, "invalid_attempt_result", True, exit_code=EXIT_TEMPFAIL)
    outcome = payload.get("outcome")
    retryable = payload.get("retryable")
    if not isinstance(outcome, str) or not outcome or not isinstance(retryable, bool):
        return AttemptResult(task, "invalid_attempt_result", True, exit_code=EXIT_TEMPFAIL)
    exit_code = _integer(payload.get("exit_code"), return_code)
    if return_code != exit_code:
        return AttemptResult(task, "invalid_attempt_result", True, exit_code=EXIT_TEMPFAIL)
    return AttemptResult(
        task=task,
        outcome=outcome,
        retryable=retryable,
        processed=max(0, _integer(payload.get("processed"))),
        problems=max(0, _integer(payload.get("problems"))),
        emitted=max(0, _integer(payload.get("emitted"))),
        exit_code=exit_code,
    )


def run_attempt_process(
    *,
    task: str,
    config_path: Path,
    wallet_limit: int,
    timeout_seconds: float,
    shutdown: ShutdownRequest,
) -> AttemptResult:
    creationflags = 0
    if os.name == "nt":
        creationflags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    try:
        process = subprocess.Popen(
            _attempt_command(task, config_path, wallet_limit),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_attempt_child_environment(),
            start_new_session=os.name == "posix",
            creationflags=creationflags,
        )
    except OSError:
        return AttemptResult(task, "attempt_start_failed", True, exit_code=EXIT_TEMPFAIL)
    deadline = time.monotonic() + max(0.0, timeout_seconds)
    while True:
        if shutdown.requested:
            _terminate_process(process)
            return AttemptResult(task, "interrupted", False, exit_code=128 + int(shutdown.signal_number or 0))
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process(process)
            return AttemptResult(task, "attempt_timeout", True, exit_code=EXIT_TEMPFAIL)
        try:
            output, _stderr = process.communicate(timeout=min(0.2, remaining))
            break
        except subprocess.TimeoutExpired:
            continue
    return _decode_attempt_result(task, output, int(process.returncode or 0))


def execute_task_once(task: str, config_path: Path, wallet_limit: int) -> AttemptResult:
    try:
        cfg = load_config(config_path)
    except ConfigLoadError:
        return AttemptResult(task, "config_load_failed", False, exit_code=EXIT_SOFTWARE)
    try:
        from market_adapters import build_default_registry
        from web_api import poll_wallet_activity, refresh_all_alert_prices

        registry = build_default_registry()
        if task == TASK_ALERTS:
            result = refresh_all_alert_prices(cfg, registry, {})
            processed = len(result.get("refreshed") or [])
            problems = len(result.get("problems") or [])
            emitted = sum(len(item.get("messages") or []) for item in result.get("refreshed") or [])
        elif task == TASK_WALLETS:
            result = poll_wallet_activity(cfg, registry, [], limit=wallet_limit)
            processed = max(0, _integer(result.get("polled_wallets")))
            problems = len(result.get("problems") or [])
            emitted = len(result.get("activity") or [])
        else:
            return AttemptResult(task, "unsupported_task", False, exit_code=EXIT_USAGE)
        save_config(cfg, config_path)
        if problems:
            return AttemptResult(
                task,
                "partial_feed_failure",
                True,
                processed=processed,
                problems=problems,
                emitted=emitted,
                exit_code=EXIT_TEMPFAIL,
            )
        return AttemptResult(task, "succeeded", False, processed, problems, emitted, EXIT_OK)
    except ConfigConflictError:
        return AttemptResult(task, "config_conflict", True, exit_code=EXIT_TEMPFAIL)
    except ConfigCommitError:
        return AttemptResult(task, "config_durability_uncertain", False, exit_code=EXIT_IOERR)
    except (KeyboardInterrupt, WorkerInterrupted):
        return AttemptResult(task, "interrupted", False, exit_code=130)
    except Exception:
        return AttemptResult(task, "operation_failed", True, exit_code=EXIT_TEMPFAIL)


def run_attempt_cli(args: argparse.Namespace) -> int:
    result = execute_task_once(args.task, args.config, args.wallet_limit)
    print(json.dumps(result.to_dict(), separators=(",", ":"), sort_keys=True), flush=True)
    if result.outcome != "succeeded":
        _journal_event(
            "attempt_failed",
            task=args.task,
            outcome=result.outcome,
            retryable=result.retryable,
            problems=result.problems,
        )
    return result.exit_code


def _validate_run_arguments(args: argparse.Namespace) -> None:
    limits = (
        (
            "deadline-seconds",
            args.deadline_seconds,
            ATTEMPT_SHUTDOWN_BUDGET_SECONDS + 1.0,
            3600.0,
        ),
        ("attempt-timeout-seconds", args.attempt_timeout_seconds, 1.0, 900.0),
        ("lock-timeout-seconds", args.lock_timeout_seconds, 0.0, 60.0),
        ("initial-backoff-seconds", args.initial_backoff_seconds, 0.0, 60.0),
        ("max-backoff-seconds", args.max_backoff_seconds, 0.0, 300.0),
    )
    for name, value, minimum, maximum in limits:
        if not minimum <= value <= maximum:
            raise ValueError(f"--{name} must be between {minimum:g} and {maximum:g}.")
    if not 1 <= args.max_attempts <= 10:
        raise ValueError("--max-attempts must be between 1 and 10.")
    if not 1 <= args.wallet_limit <= 100:
        raise ValueError("--wallet-limit must be between 1 and 100.")
    if args.attempt_timeout_seconds > args.deadline_seconds:
        raise ValueError("--attempt-timeout-seconds cannot exceed --deadline-seconds.")
    if args.max_backoff_seconds < args.initial_backoff_seconds:
        raise ValueError("--max-backoff-seconds cannot be less than --initial-backoff-seconds.")
    if args.state_file == args.lock_file:
        raise ValueError("--state-file and --lock-file must be different paths.")
    if not SOURCE_REVISION_PATTERN.fullmatch(args.source_revision):
        raise ValueError("--source-revision must be a lowercase 40-character Git commit.")
    if args.service_unit != SERVICE_UNIT_BY_TASK[args.task]:
        raise ValueError("--service-unit does not match --task.")
    if not SHA256_PATTERN.fullmatch(args.unit_contract_sha256):
        raise ValueError("--unit-contract-sha256 must be a lowercase SHA-256 digest.")


def run_worker(args: argparse.Namespace) -> int:
    try:
        _validate_run_arguments(args)
    except ValueError as exc:
        _journal_event("invalid_configuration", task=args.task, reason=str(exc))
        return EXIT_USAGE

    shutdown = ShutdownRequest()
    started_wall = time.time()
    started_monotonic = time.monotonic()
    overall_deadline = started_monotonic + args.deadline_seconds
    run_id = str(uuid.uuid4())
    attempts_completed = 0
    last_result = AttemptResult(args.task, "not_started", True, exit_code=EXIT_TEMPFAIL)

    try:
        with _shutdown_handlers(shutdown), exclusive_worker_lock(
            args.lock_file,
            timeout_seconds=min(args.lock_timeout_seconds, args.deadline_seconds),
            shutdown=shutdown,
        ):
            state = read_worker_state(args.state_file)
            previous = state["tasks"].get(args.task, {})
            running = _initial_running_entry(
                previous,
                task=args.task,
                run_id=run_id,
                now=started_wall,
                deadline_seconds=args.deadline_seconds,
                max_attempts=args.max_attempts,
                source_revision=args.source_revision,
                service_unit=args.service_unit,
                unit_contract_sha256=args.unit_contract_sha256,
            )
            _update_task_state(args.state_file, args.task, running, started_wall)
            _journal_event("run_started", task=args.task, run_id=run_id)

            for attempt_number in range(1, args.max_attempts + 1):
                if shutdown.requested:
                    last_result = AttemptResult(
                        args.task,
                        "interrupted",
                        False,
                        exit_code=128 + int(shutdown.signal_number or 0),
                    )
                    break
                remaining = overall_deadline - time.monotonic()
                if remaining <= ATTEMPT_SHUTDOWN_BUDGET_SECONDS:
                    last_result = AttemptResult(args.task, "run_deadline_exceeded", False, exit_code=EXIT_TEMPFAIL)
                    break
                attempt_timeout = min(
                    args.attempt_timeout_seconds,
                    remaining - ATTEMPT_SHUTDOWN_BUDGET_SECONDS,
                )
                last_result = run_attempt_process(
                    task=args.task,
                    config_path=args.config,
                    wallet_limit=args.wallet_limit,
                    timeout_seconds=attempt_timeout,
                    shutdown=shutdown,
                )
                attempts_completed = attempt_number
                attempt_finished = time.time()
                retry = (
                    last_result.retryable
                    and attempt_number < args.max_attempts
                    and not shutdown.requested
                )
                delay = min(
                    args.initial_backoff_seconds * (2 ** (attempt_number - 1)),
                    args.max_backoff_seconds,
                )
                remaining = overall_deadline - time.monotonic()
                retry = retry and remaining > delay + 0.001
                next_retry_at = attempt_finished + delay if retry else None
                running = _attempt_state_entry(
                    running,
                    last_result,
                    attempts_completed=attempts_completed,
                    now=attempt_finished,
                    next_retry_at=next_retry_at,
                )
                _update_task_state(args.state_file, args.task, running, attempt_finished)
                _journal_event(
                    "attempt_completed",
                    task=args.task,
                    run_id=run_id,
                    attempt=attempt_number,
                    outcome=last_result.outcome,
                    retrying=retry,
                    problems=last_result.problems,
                )
                if last_result.outcome == "succeeded" or not retry:
                    break
                if shutdown.event.wait(delay):
                    last_result = AttemptResult(
                        args.task,
                        "interrupted",
                        False,
                        exit_code=128 + int(shutdown.signal_number or 0),
                    )
                    break

            finished_wall = time.time()
            terminal = _terminal_state_entry(
                running,
                last_result,
                attempts_completed=attempts_completed,
                started=started_wall,
                now=finished_wall,
            )
            _update_task_state(args.state_file, args.task, terminal, finished_wall)
            _journal_event(
                "run_completed",
                task=args.task,
                run_id=run_id,
                outcome=last_result.outcome,
                attempts=attempts_completed,
                duration_seconds=terminal["last_duration_seconds"],
            )
            return last_result.exit_code
    except WorkerBusyError:
        _journal_event("lock_busy", task=args.task)
        return EXIT_TEMPFAIL
    except WorkerInterrupted:
        _journal_event("interrupted_before_start", task=args.task)
        return 128 + int(shutdown.signal_number or 0)
    except WorkerStateError:
        _journal_event("telemetry_failure", task=args.task)
        return EXIT_IOERR
    except Exception:
        _journal_event("supervisor_failure", task=args.task)
        return EXIT_SOFTWARE


def run_status(args: argparse.Namespace) -> int:
    try:
        state = read_worker_state(args.state_file)
    except WorkerStateError:
        _journal_event("status_read_failed", task=args.task)
        return EXIT_IOERR
    now = time.time()
    selected = TASKS if args.task == "all" else (args.task,)
    statuses: dict[str, Any] = {}
    healthy = True
    for task in selected:
        entry = state["tasks"].get(task)
        last_success = _number(entry.get("last_success_at_unix")) if isinstance(entry, dict) else 0.0
        timestamp_valid = 0 < last_success <= now + FUTURE_CLOCK_TOLERANCE_SECONDS
        age = max(0.0, now - last_success) if timestamp_valid else None
        task_healthy = (
            isinstance(entry, dict)
            and entry.get("state") == "succeeded"
            and age is not None
            and age <= args.max_age_seconds
        )
        healthy = healthy and task_healthy
        statuses[task] = {
            "healthy": task_healthy,
            "state": entry.get("state", "missing") if isinstance(entry, dict) else "missing",
            "last_success_at": entry.get("last_success_at") if isinstance(entry, dict) else None,
            "last_success_age_seconds": round(age, 6) if age is not None else None,
            "consecutive_failures": max(0, _integer(entry.get("consecutive_failures")))
            if isinstance(entry, dict)
            else 0,
        }
    print(
        json.dumps(
            {
                "schema_version": SCHEMA_VERSION,
                "healthy": healthy,
                "checked_at": _utc_timestamp(now),
                "max_age_seconds": args.max_age_seconds,
                "tasks": statuses,
            },
            separators=(",", ":") if args.compact else None,
            indent=None if args.compact else 2,
            sort_keys=True,
        )
    )
    return EXIT_OK if healthy else 1


def _default_state_path() -> Path:
    configured = os.environ.get("MARKET_SENTINEL_WORKER_STATE_PATH")
    if configured:
        return Path(configured).expanduser()
    return default_config_path().with_name("unattended-worker-state.json")


def _default_lock_path() -> Path:
    configured = os.environ.get("MARKET_SENTINEL_WORKER_LOCK_PATH")
    if configured:
        return Path(configured).expanduser()
    return default_config_path().with_name(".unattended-worker.lock")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="market-sentinel-worker",
        description="Bounded, serialized supervisor for unattended MarketSentinel refresh jobs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)

    run = commands.add_parser("run", help="Run one serialized task with bounded retries and durable telemetry.")
    run.add_argument("--task", choices=TASKS, required=True)
    run.add_argument("--source-revision", required=True)
    run.add_argument("--service-unit", choices=tuple(SERVICE_UNIT_BY_TASK.values()), required=True)
    run.add_argument("--unit-contract-sha256", required=True)
    run.add_argument("--config", type=Path, default=default_config_path())
    run.add_argument("--state-file", type=Path, default=_default_state_path())
    run.add_argument("--lock-file", type=Path, default=_default_lock_path())
    run.add_argument("--deadline-seconds", type=float, default=90.0)
    run.add_argument("--attempt-timeout-seconds", type=float, default=40.0)
    run.add_argument("--lock-timeout-seconds", type=float, default=5.0)
    run.add_argument("--max-attempts", type=int, default=3)
    run.add_argument("--initial-backoff-seconds", type=float, default=2.0)
    run.add_argument("--max-backoff-seconds", type=float, default=8.0)
    run.add_argument("--wallet-limit", type=int, default=25)
    run.set_defaults(func=run_worker)

    status_parser = commands.add_parser("status", help="Check durable last-success freshness for one or both tasks.")
    status_parser.add_argument("--task", choices=("all", *TASKS), default="all")
    status_parser.add_argument("--state-file", type=Path, default=_default_state_path())
    status_parser.add_argument("--max-age-seconds", type=float, required=True)
    status_parser.add_argument("--compact", action="store_true")
    status_parser.set_defaults(func=run_status)

    attempt = commands.add_parser("_attempt", help=argparse.SUPPRESS)
    attempt.add_argument("--task", choices=TASKS, required=True)
    attempt.add_argument("--config", type=Path, required=True)
    attempt.add_argument("--wallet-limit", type=int, default=25)
    attempt.set_defaults(func=run_attempt_cli)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "status" and not 1.0 <= args.max_age_seconds <= 86400.0 * 30:
        parser.error("--max-age-seconds must be between 1 and 2592000.")
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
