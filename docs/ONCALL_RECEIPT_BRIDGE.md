# Independent on-call receipt bridge

The production alert-delivery check sends a short-lived, challenge-bound alert
from Prometheus through Alertmanager. The separate bridge in
`scripts/oncall_receipt_bridge.py` accepts only that alert, sends an email through
an authenticated TLS SMTP relay, and records the relay's acceptance. An operator
then explicitly acknowledges the message with a separate credential. Until
that acknowledgement, the bridge returns no score-eligible receipt. The live
collector and GitHub-hosted reviewer independently check the exact receipt
schema, challenge, Alertmanager fingerprint, webhook digest, and event digest.

This bridge is a trusted receipt authority. SMTP acceptance proves a provider
accepted the message; it does not prove final inbox delivery. The distinct
operator credential and audit record are the basis for the human acknowledgement
claim. Protect the bridge host, database, and operator credential accordingly.

## Deploy on a separate host

Use a host with a real public HTTPS DNS name **separate from the Market Sentinel
production host**. Create a dedicated `market-sentinel-oncall` system user and a
private `/var/lib/market-sentinel-oncall` directory owned by that user, mode
`0700`. Install this repository and its locked Python dependencies into
`/opt/market-sentinel-oncall/current/.venv`. The example service is
`deploy/systemd/market-sentinel-oncall-bridge.service`; the example HTTPS proxy
is `deploy/caddy/oncall-receipts.Caddyfile.example`. The bridge binds only
`127.0.0.1:19095`; expose it only through the public HTTPS proxy. Restrict
network access to the bridge host and retain its SQLite database and audit
events under your incident retention policy. Keep the host clock synchronized.

Create `/etc/market-sentinel-oncall` owned by root, grouped to
`market-sentinel-oncall`, mode `0710`. Provision three distinct files inside it,
each owned by `market-sentinel-oncall` and mode `0600`, with **no trailing
newline**:

- Webhook token: the same random value that Alertmanager uses in
  `/etc/market-sentinel/alertmanager-oncall-bearer-token` on the production host
  and that the protected production workflow stores as
  `MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN`.
- A separate operator acknowledgement token. Keep this out of Alertmanager,
  CI, the email, and the production host. Supply a copy only to the human
  on-call operator's trusted workstation.
- SMTP password or provider app password, scoped to sending from the configured
  mailbox.

Use long random RFC 6750 bearer tokens (at least 32 characters). Store only
absolute paths to these files in the root-owned mode-`0600`
`/etc/market-sentinel-oncall/bridge.env`:

```ini
MARKET_SENTINEL_BRIDGE_DATABASE=/var/lib/market-sentinel-oncall/receipts.sqlite
MARKET_SENTINEL_BRIDGE_WEBHOOK_TOKEN_FILE=/etc/market-sentinel-oncall/webhook-token
MARKET_SENTINEL_BRIDGE_ACK_TOKEN_FILE=/etc/market-sentinel-oncall/operator-ack-token
MARKET_SENTINEL_BRIDGE_ACK_IDENTITY=primary-oncall-operator
MARKET_SENTINEL_BRIDGE_SMTP_PASSWORD_FILE=/etc/market-sentinel-oncall/smtp-password
MARKET_SENTINEL_BRIDGE_SMTP_HOST=smtp.provider.tld
MARKET_SENTINEL_BRIDGE_SMTP_PORT=587
MARKET_SENTINEL_BRIDGE_SMTP_MODE=starttls
MARKET_SENTINEL_BRIDGE_SMTP_USERNAME=service@your-domain.tld
MARKET_SENTINEL_BRIDGE_SMTP_FROM=service@your-domain.tld
MARKET_SENTINEL_BRIDGE_SMTP_TO=operator@your-domain.tld
MARKET_SENTINEL_BRIDGE_LISTEN_PORT=19095
```

Use `MARKET_SENTINEL_BRIDGE_SMTP_MODE=tls` for implicit TLS on port 465.
The bridge requires SMTP authentication and CA-verified TLS in either mode.
It resolves the SMTP hostname to public global addresses and connects only to
those addresses, while validating the certificate against the configured
hostname; a local SMTP sink cannot qualify as an external provider.
Use the actual provider and mailbox; example names must be replaced. The service
file's `EnvironmentFile` must be readable by systemd, while secret files must be
owned by the service user and mode `0600` with no symbolic-link path components.
Restrict Caddy and service logs so they do not record `Authorization` headers.

On the production host, set the protected origin
`MARKET_SENTINEL_ONCALL_RECEIPT_ORIGIN` to the bridge's canonical public HTTPS
origin. The exact webhook URL file must contain
`https://<origin>/v1/market-sentinel/alertmanager`. Configure Alertmanager's
dedicated route and credentials as described in `PRODUCTION_OPERATIONS.md`.
The same bearer token authenticates the bridge's receipt GET at
`/v1/market-sentinel/alert-receipts/<binding>`. The collector pins public DNS
answers and verifies the TLS hostname. Restrict Alertmanager's outbound traffic
to the approved public bridge addresses or ranges. A change of bridge address,
SMTP provider, mailbox, or token is an operational change requiring review.

## Acknowledge a real notification

The email contains a challenge binding and delivery ID. After a person reviews
the alert, on their trusted workstation run:

```bash
python -m scripts.oncall_receipt_bridge acknowledge \
  --origin https://receipts.your-domain.tld \
  --binding '<64-hex binding from the email>' \
  --delivery-id '<delivery ID from the email>' \
  --token-file /path/to/private/operator-ack-token
```

The CLI requires an interactive terminal and an exact confirmation phrase. It
sends the operator token only over certificate-verified HTTPS. The service
refuses acknowledgement before the SMTP relay has accepted the message and
records the operator identity hash and acknowledgement time in SQLite. The
receipt endpoint becomes available only after this step. Do not automate this
command or place the acknowledgement token in CI. The collector's default
challenge deadline is 120 seconds, so the on-call operator needs to respond
within that window during a production evidence run.

Alertmanager retries a webhook if SMTP dispatch fails. The bridge retains a
pending record and reuses one Message-ID and delivery ID on retry. If the relay
accepted the email but the connection failed before its response was durably
recorded, retry can produce a duplicate email; the operator must still
acknowledge only the matching challenge and delivery ID. Replayed or altered
webhook bodies for an existing challenge are rejected. The single-process
service owns its SQLite database exclusively on POSIX hosts; run only one
instance against a database.

This implementation does not create a production host, SMTP account, public
DNS name, or a human acknowledgement. Those inputs must exist before the
production operations score can increase.
