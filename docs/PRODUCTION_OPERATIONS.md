# Production Operations

MarketSentinel is a local desktop/CLI application with a guarded optional web
interface. This guide covers a single-operator Linux deployment for analytics,
alerts, paper trading, and explicitly approved live-trading workflows. It does
not make a funded strategy autonomous or remove exchange eligibility, KYC, or
regional restrictions.

## Deployment boundary

- Keep `web_api.py` bound to `127.0.0.1`; do not publish port `8765` in a
  firewall, Docker mapping, or cloud security group.
- Serve browser access through a TLS reverse proxy with authentication. The
  provided Caddy example supplies Basic Auth, TLS, a restrictive browser
  content-security policy, cross-origin and permissions headers, and the
  upstream API token.
- The application also emits a conservative browser-security baseline on every
  local response (including errors and CORS preflights): CSP, anti-framing,
  no-sniff, no-referrer, restricted browser permissions, and opener isolation.
  Caddy remains responsible for public HTTPS-only HSTS, resource isolation, and
  removing the `Server` header at the internet-facing boundary.
- The threaded loopback server gives each connection 15 seconds to make
  progress, admits at most 32 concurrent request workers, rejects overload with
  `503` and `Retry-After`, and does not wait on stalled daemon workers during
  shutdown. It rejects ambiguous or incomplete request framing and caps every
  JSON, text, and static response at 16 MiB before sending response headers.
  The systemd unit retains its 30-second stop deadline as a final process-level
  safeguard.
- A broken pipe, reset or abort while writing a response is recorded once as
  `outcome=client_disconnected`, with local log/metric status `499` and the
  attempted HTTP status in `response_status`. No 499 or replacement 500 response
  is sent to the closed connection. The request still releases its worker and
  mutation admission; a committed mutation is not undone. Retry supported
  durable creates with the same idempotency key to reconcile uncertain delivery.
  Upstream connection failures remain backend errors, not client disconnects.
- When an API token is configured, the server permits ten failed token attempts
  per client per minute, then returns `429` with `Retry-After`. A valid token
  immediately clears that client record. This is a backstop for the proxy's
  authentication controls, not a replacement for Caddy Basic Auth or firewall
  policy.
- Run under the dedicated `market-sentinel` user. Use `/var/lib/market-sentinel`
  for state and a root-owned `/etc/market-sentinel/market-sentinel.env` for
  credentials and tokens.
- Never put credential values in `config.json`. Config load, save, the HTTP API,
  and the CLI reject persisted passwords, private keys, cookies, bearer tokens,
  and venue API secrets. Persist only validated environment-variable names or
  protected credential-file paths, then supply the values through the
  root-owned service environment or secret manager.
- Configuration writes use an advisory sibling lock, an on-disk revision
  check, `fsync`, and atomic replacement. A process that loaded an older
  revision fails closed instead of overwriting a newer writer; the web API
  returns `409 config_conflict`. Reload state before retrying. Keep the lock
  file on the same local filesystem as `config.json`, and do not run multiple
  active instances against network filesystems with unreliable advisory locks.
- Desktop settings, market selection, wallet/follow changes, and manual
  alert/history edits are persisted as detached field replacements before
  publication to the shared runtime configuration. Failed pre-commit saves do
  not install their settings or report successful actions. The config root and
  unchanged journal objects retain their identities for background workers.
- The installed systemd web service runs only the HTTP API. Separate reviewed
  timers invoke `core.unattended_worker` for price-alert refreshes and
  read-only wallet-feed observation. Both tasks share one lock and one atomic status file,
  reload state before every bounded attempt, and treat partial feed failures as failures rather than fresh success.
  The alert task uses compare-and-swap configuration commits
  for its refreshed state; the wallet observer
  deliberately does not advance the durable wallet-delivery cursor because it
  has no activity consumer. Desktop/API polling owns cursor advancement and
  delivery, so an unattended observation cannot discard an event before that
  consumer sees it. Aggregate and per-attempt deadlines, bounded exponential
  backoff, process-group termination, and durable last-success telemetry are
  part of the unit contract. These workers do not execute copy trading or place orders.
  Do not add ad hoc concurrent polling timers or reuse the privileged web environment.
- Configuration replacement is the commit point. `ConfigCommitError` means the
  replacement completed but subsequent synchronization or cleanup failed; the
  saved revision remains attached to the candidate. Do not treat that error as
  proof that nothing was written, or blindly restore the previous snapshot.
- After a desktop persistence failure or stale-writer conflict, configuration
  writes, alert evaluation and copy activity are paused for that process. A
  post-commit candidate remains reflected in memory, but execution stays paused;
  committed copy checkpoints/dispatch intent are not rolled back in memory.
  Fix permissions, capacity or writer conflicts, inspect the durable settings
  and any ambiguous order journal, then restart to reload verified state. Do
  not reset journals or assume restarting by itself resolves the underlying
  error. A later unrelated desktop action cannot save a previously rejected
  setting or automatically clear the pause.
- Live-validation report, decision, and promotion-snapshot stores use the same
  single-writer/atomic-replace discipline. Existing malformed JSON is preserved
  and rejected rather than silently replaced. Restore a reviewed backup before
  resuming evidence collection after a store-read error.
- Atomic publication of config, analytics cache, live evidence and text/CSV
  exports retries Windows access/sharing/lock replacement errors (5, 32, 33)
  at most ten times, with less than 1.5 seconds of total retry sleep. A held
  reader or file scanner can temporarily deny replacement. Persistent denial
  is still reported, and the previous file is not deleted to force success;
  resolve access/ownership before retrying. Other errors are not retried.
- Cache read failures are reported without treating an unreadable store as
  missing or corrupt. Restore access and retry; do not delete a valid cache
  to clear a read error. Malformed UTF-8/JSON or invalid entry containers are
  quarantined with their original bytes before an empty cache can be created.
  Quarantine rename or directory-sync failures are also reported, not hidden
  as successful recovery. Inspect the active and `.corrupt-*` files before
  retrying after such a failure.
- Adapter egress defaults to reviewed HTTPS/WSS endpoints, refuses redirects,
  and rejects private, loopback, link-local, reserved, or mixed-public/private
  DNS destinations. If a reviewed local integration genuinely needs a private
  origin, list its exact scheme, host, and port in
  `MARKET_SENTINEL_OUTBOUND_PRIVATE_ORIGINS`; never use wildcards, CIDRs, or a
  public deployment's browser/API settings endpoint to change it.
- Managed direct Polymarket WebSocket connections pin the current validated
  DNS addresses, retain the origin hostname for TLS/SNI and HTTP Host, and
  require a verified TLS connection and a completed 101 upgrade. DNS, TCP,
  TLS and HTTP upgrade share one connection deadline. Worker stop events
  cancel pending direct connections; subscriptions are not sent before upgrade.
  Successful connections disarm their setup deadline so a later timeout cannot
  close an established stream. Configured proxies remain honored and require
  their own connect-time egress and deadline enforcement.
- Managed Polymarket WebSocket connections reject any frame or complete
  fragmented message larger than 1 MiB. The frame-length check runs before
  `websocket-client` reads the declared payload, and the continuation check
  runs before fragments are concatenated. A deliberately injected custom
  WebSocket connection factory is outside that managed transport boundary and
  must provide an equivalent receive limit.
- Do not enable funded trading or live copy execution in a service until the
  evidence gates in `README.md` and `polymarket/live_verification.py` pass.
- Copy activity is checkpointed durably before any live handler is allowed to
  run, providing at-most-once crash behavior. If checkpoint persistence fails,
  execution is skipped. Review the error and reload/restart the operator process
  after resolving a configuration conflict; do not replay an uncertain funded
  action automatically.
- Polymarket price alerts retain a bounded REST polling path when a WebSocket is
  disconnected. WebSocket workers are restartable and stop with bounded joins;
  alert freshness and worker health still require monitoring through the
  application state and service logs.

### Required connect-time egress enforcement

URL validation resolves a configured hostname immediately before a managed
HTTP or WebSocket request and rejects every non-global answer unless the exact
origin is explicitly allowed. The shared adapter runtime pins those addresses
into direct HTTP connections while preserving the origin hostname for TLS.
The Polymarket HTTP client uses the same managed direct transport, with one
owned session spanning each bounded retry/body-read operation. Pools include
the validated address set in their identity, so a DNS change cannot reuse a
pool pinned to retired addresses. Redirect responses are closed before
Requests can eagerly consume their bodies while preparing a next request.
Managed direct WebSockets likewise resolve inside their connection deadline
and connect only to that validated address set, without a second hostname lookup.
Configured proxy and injected SDK/factory paths still need their own connect-time
enforcement; do not infer uniform pinning across all transports from direct
HTTP/WebSocket coverage.
TLS hostname verification, disabled redirects, and immutable endpoint settings
reduce exposure but do not remove every validation-to-connect race.

A production host must therefore enforce the destination again at connect time.
Use a service/cgroup-aware egress firewall or a forward proxy that meets all of
these requirements:

- deny loopback, RFC1918/unique-local, link-local, carrier-grade NAT,
  documentation, benchmark, multicast, unspecified, reserved, and cloud
  metadata destinations for service-originated outbound connections;
- resolve each requested hostname through a trusted resolver, reject the whole
  request if any answer is non-global, and connect to one of those already
  approved addresses without a second DNS lookup;
- preserve the original hostname for HTTP `Host`, TLS SNI, and certificate
  verification; permit only the required TCP ports (normally 443); and keep
  redirects disabled or repeat the complete policy for every redirect target;
- express an intentional private integration as both an exact
  `MARKET_SENTINEL_OUTBOUND_PRIVATE_ORIGINS` entry and an equally narrow network
  exception. The environment variable alone is not a firewall rule.

Because the web process also accepts legitimate loopback traffic from Caddy and
the health probe, a broad host rule that blocks loopback in both directions is
incorrect. Scope the outbound rule by service cgroup, process owner, proxy, or
connection direction. Before approving deployment evidence, exercise the rule
with controlled hostnames that return public, mixed public/private, and rebound
private answers; all but the stable public case must fail without reaching the
destination.

### HTTP deadlines and scan cancellation

Managed HTTP requests use a monotonic budget shared by DNS admission,
rate-limiter waits, response headers/body reads, and internal retry backoff.
The configured `timeout` is the budget for one HTTP operation, including its
internal retries; it is not the duration limit for an entire unlimited scan.
Response byte limits remain independent. Socket ownership lasts through
HTTP/1.0 and `Connection: close` bodies, and a completed request disarms its
pooled socket before another request can borrow it.

Leaderboard CLI runs handle SIGINT/SIGTERM by requesting cancellation. During
page/MDD work, cancelled requests are not retried or recorded as valid empty
history. Committed contiguous SQLite pages and completed MDD remain resumable;
an interrupted fetch or calculation does not overwrite the previous export.
Exit status is 130 for SIGINT and 143 for SIGTERM. Resume the same state database
with `--resume`, or inspect it with `polymarket-leaderboard-status`. Windows
service termination and forced process kills are not equivalent to POSIX
SIGTERM; abrupt-exit durability remains a separate contract.

An OS DNS lookup cannot be forcibly interrupted by Python. At most 16 daemon
DNS helpers may remain outstanding; expired lookups cannot initiate managed
direct HTTP/WebSocket work.
Cancellation callbacks must be fast, side-effect-free and thread-safe. Raw
connect/TLS setup can take up to the remaining transport timeout to unwind;
body reads and retry/rate-limiter waits are actively interrupted. Custom
injected transports, SOCKS transports and venue SDKs do not acquire these
guarantees merely by accepting a timeout argument. Configured WebSocket proxies
retain the library's connection route and do not inherit direct socket guards;
proxy deadline/egress enforcement and host acceptance above remain required.

### Durable state boundary

Leaderboard scan writers require SQLite WAL with `synchronous=FULL`. Each
page/MDD transaction requests a storage sync before reporting success, rather
than deferring it until a checkpoint. `fullfsync=ON` additionally requests
macOS F_FULLFSYNC where supported. Every writer connection reapplies and checks
these settings before schema migration or scan writes; an unavailable setting
fails closed. Read-only status/export connections do not change database mode.
This can reduce write throughput compared with the previous NORMAL setting.
Use a local filesystem with working locks and reliable storage sync; SQLite
settings and process-crash tests cannot certify a VPS provider's power-loss
behavior. Keep backups and perform real-host recovery drills. See the
[SQLite sync contract](https://www.sqlite.org/pragma.html#pragma_synchronous).

The bundled production environment pins every non-configuration durable store
below `/var/lib/market-sentinel`:

- `POLYMARKET_ANALYTICS_CACHE_PATH` writes
  `polymarket_analytics_cache.json`.
- `POLYMARKET_LIVE_VALIDATION_REPORTS_PATH` writes
  `polymarket_live_validation_reports.json`.
- `POLYMARKET_LIVE_VALIDATION_DECISIONS_PATH` writes
  `polymarket_live_validation_decisions.json`.
- `POLYMARKET_LIVE_VALIDATION_PROMOTION_PROPOSAL_SNAPSHOTS_PATH` writes
  `polymarket_live_validation_promotion_proposal_snapshots.json`.

These assignments are required, not optional deployment suggestions. The web
unit requires `/etc/market-sentinel/market-sentinel.env` and runs an exact-value
preflight for all four variables before `doctor` or the server starts. This
keeps writes inside the unit's sole `ReadWritePaths` state root while
`ProtectSystem=strict` makes the release checkout read-only. The backup unit
recursively captures that same state root, so every existing regular store file
is included in the next successful snapshot. Do not relocate one store without
also reviewing the sandbox, backup source, restore procedure, and production
deployment verifier; the verifier inspects the running process environment and
fails if its effective paths or backup source differ from this boundary.

### Durable-mutation idempotency window

Configuration loading rejects duplicate JSON keys, non-finite numbers,
malformed record collections and market/safety-setting containers. Existing
journals above the supported capacity are rejected unchanged, not trimmed on
startup: sorting away an older pending live operation would erase its replay
protection. Normal append-time retention still removes completed/rejected
records while preserving unresolved ones. Valid older configurations may omit
the newer journal collections. Saving validates the snapshot before publication
so malformed or non-finite state cannot replace a valid configuration.
An inaccessible, unreadable, symlinked or special-file configuration is not an
empty store. Both loading and the save-time revision guard distinguish a truly
missing directory entry from an inspection/read error. This avoids relying on
[`Path.exists()`](https://docs.python.org/3/library/pathlib.html#pathlib.Path.exists),
which can return false for inaccessible files.

Operational flags in stored configuration must be JSON booleans (`true` or
`false`), not quoted strings, numbers or nulls. Copy percentage/scale, slippage,
positive per-trade caps and integer conflict windows are validated on load and
before save. Valid finite legacy numeric strings remain accepted, but invalid
values are not clamped, truncated or replaced with a 100% copy allocation.
When both percentage and scale are present, they must agree within serialization
rounding. Existing invalid settings need explicit operator correction.

The HTTP API and CLI validate the original mutation values with the same model
rules before changing settings. Send actual JSON booleans, not `"true"` or
`"false"`; ordinary CLI `--enabled`/`--no-live` flags remain supported. Integer
conflict windows must not contain fractional values. If both percentage and
scale (or multiple percentage aliases) are supplied, they must agree; likewise
top-level and nested market safety controls must not contradict each other.
An empty wallet string or empty wallet list intentionally clears the follows;
nulls and non-string members are invalid. If both follow aliases are supplied,
the single identity must match the first normalized list identity.

Shared market safety flags and positive caps are also checked when loading and
saving configuration. A blank string or null explicitly unsets an optional
market cap; a supplied nonblank cap must be positive and finite. Numeric
booleans, NaN and infinity are rejected. Desktop copy settings are validated
before being installed in memory. HTTP/CLI JSON objects, including `--json
@file` and structured `--setting` values, reject duplicate keys and non-finite
JSON numbers rather than silently taking the last value. Invalid settings must
be corrected explicitly; the application does not rewrite or reset damaged
configuration to make it load.

Mutation journals require stable record IDs, SHA-256 key/request hashes,
mutation method/path and an explicit boolean live classification. Copy outboxes
require stable record, market, watch and activity identities. Duplicate record
IDs, duplicate journal client keys or duplicate outbox signal identities fail
closed. Malformed replay metadata and completed records without a response
status are rejected. Missing/unknown dispatch states remain ambiguous, never
automatically pending. Valid legacy configurations can omit journal collections,
but incomplete records inside a present journal are not reconstructed with new
identities. Restore acceptance uses these same configuration rules.

On a configuration-integrity error, preserve the original bytes and reconcile
against a verified backup and venue history. Do not replace the configuration
with empty defaults or delete its trading journals to get the process started.

The live-report, decision, and promotion-snapshot create routes use an opaque
`Idempotency-Key` (1-128 visible ASCII characters, with no whitespace). Only a
SHA-256-derived binding is persisted. A retry with the same key and canonical
request reconciles a committed file replacement, including a prior uncertain
parent-directory sync, without adding a second record or duplicate-import audit.
Reusing a key for different inputs fails closed.

Idempotency retention follows the evidence record: report and promotion
snapshot keys remain protected while their records remain inside the configured
bounded store, while decision keys remain with the decision ledger. After an
operator explicitly purges a record or bounded retention prunes it, that old key
is no longer reserved and clients must not reuse it. Duplicate-import bindings
on one retained report are capped at 256 to keep authenticated retry metadata
bounded. Browser clients retain one generated key across transport-uncertain
retries and discard it only after a terminal HTTP response or success.

## Install on systemd 247+ (RHEL/Rocky 9+ or supported Ubuntu)

The production unit profile requires systemd 247 or newer. In particular, its
effective sandbox contract uses `ProtectProc=` and `ProcSubset=`, which are not
available on the systemd 239 shipped by RHEL/Rocky 8. RHEL/Rocky 9+ is the
supported enterprise baseline; use an Ubuntu release whose packaged systemd is
also at least 247. Source-level compatibility checks on an older distribution
do not certify these installed production units. Install Git, Python 3.10 or
newer with `venv` and `pip`, and Node.js 24 with npm before cloning the release.
Node.js 24 matches the reviewed CI frontend build lane; an older distro Node
package may not satisfy the locked Vite dependency's engine requirement.
Verify the host toolchain before cloning the release:

```bash
SYSTEMD_VERSION="$(systemctl --version | awk 'NR == 1 {print $2}')"
case "${SYSTEMD_VERSION}" in
  ''|*[!0-9]*) echo "unsupported systemd version: ${SYSTEMD_VERSION:-missing}" >&2; exit 1 ;;
esac
test "${SYSTEMD_VERSION}" -ge 247
git --version >/dev/null
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10+ required")'
python3 -m venv --help >/dev/null
test "$(node -p 'process.versions.node.split(".")[0]')" -eq 24
npm --version >/dev/null
```

```bash
sudo useradd --system --user-group --home /var/lib/market-sentinel --shell /sbin/nologin market-sentinel
sudo useradd --system --user-group --home /nonexistent --shell /sbin/nologin market-sentinel-health
sudo install -d -o market-sentinel -g market-sentinel -m 0700 /var/lib/market-sentinel
sudo install -d -o root -g market-sentinel -m 0750 /etc/market-sentinel
sudo install -m 0600 deploy/systemd/market-sentinel.env.example /etc/market-sentinel/market-sentinel.env
sudo install -m 0600 deploy/systemd/market-sentinel-health.env.example /etc/market-sentinel/market-sentinel-health.env
sudo install -m 0600 deploy/systemd/market-sentinel-worker.env.example /etc/market-sentinel/market-sentinel-worker.env
sudo install -m 0644 deploy/systemd/market-sentinel.conf /etc/tmpfiles.d/market-sentinel.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/market-sentinel.conf

sudo mkdir -p /opt/market-sentinel
sudo chown "$USER" /opt/market-sentinel
RELEASE_VERSION="<RELEASE_VERSION>"
git clone https://github.com/Yunushan/market-sentinel.git /opt/market-sentinel
cd /opt/market-sentinel
git fetch --tags --force origin
git switch --detach "v${RELEASE_VERSION}"
EXPECTED_SOURCE_REVISION="$(git rev-parse --verify HEAD^{commit})"
test -z "$(git status --porcelain=v1 --untracked-files=all)"

# Install the exact locked frontend tree before the strict verifier builds it.
npm --prefix frontend ci --ignore-scripts --no-audit --no-fund

# Validate the checked-out source with the test dependency set before deployment.
python3 -m venv .verify-venv
.verify-venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-bootstrap.lock
.verify-venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-test.lock
.verify-venv/bin/python -m pip install --no-build-isolation --check-build-dependencies --no-deps .
.verify-venv/bin/python verify.py --frontend-build --frontend-live-smoke
rm -rf .verify-venv

# Install the lean runtime dependency set used by the systemd service.
python3 -m venv .venv
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-bootstrap.lock
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-build-isolation --check-build-dependencies --no-deps .
```

The bootstrap lock installs the exact setuptools version declared in
`pyproject.toml` before either source install. Keep build isolation disabled and
the build-dependency check enabled: permitting an isolated PEP 517 build would
allow pip to fetch and execute a backend that is outside the reviewed lock.

Initialize the private state file before enabling either worker timer or taking
the first backup. Run this as the service account from the installed release:

```bash
sudo -u market-sentinel -g market-sentinel -- \
  /opt/market-sentinel/.venv/bin/python \
  /opt/market-sentinel/scripts/initialize_production_config.py
```

The initializer requires the state directory to be owned by
`market-sentinel:market-sentinel` with mode `0700`. It creates a new default
`config.json` through the application's atomic store, or validates an existing
service-owned mode-`0600` file without rewriting it. An unsafe directory, link,
malformed file, or competing first write fails closed. Review the output before
starting services. A missing config is accepted by the read-only `doctor`
command as defaults, but both bundled worker units require a regular config
file and the restore drill requires that file in a recent backup.

Before either unattended-worker timer is enabled, replace
`MARKET_SENTINEL_SOURCE_REVISION` in
`/etc/market-sentinel/market-sentinel-worker.env` with the exact value printed
by `EXPECTED_SOURCE_REVISION`. The 40-hex value is passed by each reviewed
systemd unit and written into its durable worker state; deployment collection
fails if it differs from the installed revision. Do not derive or overwrite it
during evidence collection.

Before enabling either service, replace the blank credential values. Generate
one admin token for `market-sentinel.env`, then generate a different observer
token and place that same observer value in both environment files. Do not add
the admin token or any venue credential to `market-sentinel-health.env`.

An authenticated Polymarket CLOB SDK is intentionally excluded from the
baseline runtime. Install it only for an explicitly approved signed-trading
workflow:

```bash
.venv/bin/python -m pip install --only-binary=:all: --require-hashes -r requirements-live.lock
```

The strict verifier above built the React frontend. Capture its reviewed
fingerprint before starting the service:

```bash
cd /opt/market-sentinel

# Capture this from the reviewed, clean release build before the service starts.
EXPECTED_FRONTEND_SHA256="$(.venv/bin/python -c 'from pathlib import Path; from core.deployment_identity import frontend_tree_sha256; print(frontend_tree_sha256(Path("frontend/dist")))')"
printf '%s\n' "${EXPECTED_FRONTEND_SHA256}" | sudo tee /etc/market-sentinel/frontend-dist.sha256 >/dev/null
sudo chmod 0600 /etc/market-sentinel/frontend-dist.sha256
```

Treat `/etc/market-sentinel/frontend-dist.sha256` as deployment evidence, not
as a value to regenerate immediately before verification. It must be captured
from the reviewed clean release build before the service starts and kept under
root ownership. Rebuilding or changing `frontend/dist` requires recording a new
reviewed fingerprint and restarting the service.

Install the systemd unit and validate it:

```bash
sudo install -m 0644 deploy/systemd/market-sentinel-web.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-health.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-health.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-alerts-refresh.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-alerts-refresh.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-wallets-poll.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-wallets-poll.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now market-sentinel-web
sudo systemctl enable --now market-sentinel-health.timer
sudo systemctl enable --now market-sentinel-alerts-refresh.timer
sudo systemctl enable --now market-sentinel-wallets-poll.timer
sudo systemctl enable --now market-sentinel-backup.timer
sudo systemctl start market-sentinel-backup.service
sudo systemctl status market-sentinel-web
sudo systemctl status market-sentinel-health.timer
sudo systemctl status market-sentinel-alerts-refresh.timer
sudo systemctl status market-sentinel-wallets-poll.timer
sudo systemctl status market-sentinel-backup.timer
sudo systemctl start market-sentinel-health.service
sudo systemctl status market-sentinel-health.service
sudo journalctl -u market-sentinel-web -n 50 --no-pager
/opt/market-sentinel/.venv/bin/market-sentinel doctor --strict --config /var/lib/market-sentinel/config.json --frontend-dir /opt/market-sentinel/frontend/dist
/opt/market-sentinel/.venv/bin/market-sentinel-worker status --task all \
  --state-file /var/lib/market-sentinel/unattended-worker-state.json \
  --max-age-seconds 600 --compact
```

The CLI `serve --frontend-dir` option is supported for deployment-relative
builds and is validated before the socket is opened. The resolved directory
must remain beneath the release/resource root, so a typo or an unsafe path
fails closed instead of changing the HTTP static-file root.
The static catalog is built once from that canonical root; each candidate is
resolved and checked relative to the root before it is read, so request URLs
never construct filesystem paths.

The web and health units use distinct Unix accounts, strict systemd sandboxes,
private user/device and hostname/clock namespaces, restricted process views and
network address families, and separate
root-owned mode-`0600` environment files. Put the independently generated
`MARKET_SENTINEL_OBSERVABILITY_TOKEN` in both files, but keep
`MARKET_SENTINEL_API_TOKEN` and all venue credentials exclusively in
`market-sentinel.env`. The health unit explicitly removes the admin variable,
rejects command-line token overrides, and fails before probing if its dedicated
observability value is absent. Its `market-sentinel-health` account cannot read
the web process environment or writable state. Missing either file or changing a required
durable path prevents the associated production check from starting. After
those path assertions, the web unit has a strict read-only `doctor` preflight.
It does not run a post-start probe in the web process environment, because that
would expose the admin and venue credentials to the probe. The deployment
procedure starts the isolated health service immediately after the web unit;
its timer then runs the same loopback health check every minute. Deployment
evidence independently repeats the authenticated health and metrics checks.
Both units limit start failures to
five attempts in five minutes, and the health unit times out after 30 seconds. A
start-limit hit is an operator action item rather than a signal to retry
continuously; inspect
`journalctl -u market-sentinel-web` or `journalctl -u market-sentinel-health`
and use `systemctl reset-failed` only after correcting the cause. Review
`systemd-analyze security market-sentinel-web` and
`systemd-analyze security market-sentinel-health` after installation and tighten
any setting that does not prevent normal operation on the chosen distribution.
The web unit manages `/var/lib/market-sentinel` with `StateDirectory` and mode
`0700`, so a normal service start does not depend on a pre-existing writable
state directory. The initial install command remains useful for inspecting
ownership before the first start.

The two unattended one-shot services run as `market-sentinel` but load only
`/etc/market-sentinel/market-sentinel-worker.env`. Keep that root-owned file at
mode `0600` and add only read-scoped venue credentials explicitly listed in its
example; never copy `market-sentinel.env`. Its required
`MARKET_SENTINEL_SOURCE_REVISION` must be the exact clean release commit and is
not a credential. The units remove admin, observer,
order-signing, relayer, TLS-keylog, and Python-injection variables again before
starting their child attempt. They share
`/var/lib/market-sentinel/.unattended-worker.lock` and
`/var/lib/market-sentinel/unattended-worker-state.json`, so their schedules can
never mutate the shared config concurrently. A lock timeout, partial venue
failure, stale compare-and-swap write, deadline, signal, or durability error is
recorded as non-success and must not advance freshness. Inspect both service
journals and the status command above before relying on alert or wallet data.

The backup timer runs a local, network-isolated state backup each day with a
14-pair retention limit. It writes archives and SHA-256 manifests only to
`/var/lib/market-sentinel-backups`, owned by the service account and separate
from the live state directory. Place `/var/lib` on encrypted storage or change
the backup destination to an encrypted mounted volume before using this in
production. The archive and then its manifest are each published atomically,
with their directory changes synced on POSIX filesystems. Retention counts only
cryptographically verified, restorable archive/manifest pairs. An orphan left
by an interrupted publication and an invalid pair are preserved for operator
inspection, do not consume a retention slot, and cannot evict a valid backup.
SQLite state databases are captured with SQLite's online backup API instead of
copying WAL, shared-memory, or rollback-journal sidecar files. A read transaction
pins each database snapshot before its page-count/size preflight, so concurrent
WAL commits cannot expand the copy beyond its accepted payload budget. Oversized
databases are rejected before a staged database is created. `--sqlite-timeout`
sets the per-database lock/copy budget (30 seconds by default). Copying checks
that budget after each 64-page step; this is not an interrupt for a blocked
kernel/filesystem call. The systemd unit's five-minute process timeout remains
the final service-level bound. Failed or timed-out copies publish no new pair
and do not prune prior valid backups. Other regular
files are copied to a private stable snapshot and rejected if they change while
being copied. Creation enforces the same member, payload, compressed-archive,
and bounded tar-overhead limits as verification; it verifies the staged pair
before publishing the archive and then the manifest. The archive intentionally excludes
`/etc/market-sentinel` and its credentials; protect and back up that root-owned
configuration through the host's secret-management and configuration process.

## TLS and browser access

Install Caddy from its official package repository, copy
`deploy/caddy/Caddyfile.example` to `/etc/caddy/Caddyfile`, and replace the
example hostname. The packaged `caddy.service` does not inherit variables set
in an operator's shell. Install the reviewed systemd drop-in so both startup
and reload receive the proxy credentials:

```bash
sudo install -d -o root -g root -m 0755 /etc/systemd/system/caddy.service.d
sudo install -o root -g root -m 0644 \
  deploy/caddy/market-sentinel-env.conf \
  /etc/systemd/system/caddy.service.d/market-sentinel-env.conf
sudo test ! -L /etc/caddy/market-sentinel.env
if ! sudo test -e /etc/caddy/market-sentinel.env; then
  sudo install -o root -g root -m 0600 /dev/null /etc/caddy/market-sentinel.env
fi
caddy hash-password
sudoedit /etc/caddy/market-sentinel.env
```

The `caddy hash-password` command prompts for a password without echoing it;
copy its hash into the root-owned environment file. Add exactly these two
assignments, with no shell quotes or `export` prefix:

```text
MARKET_SENTINEL_API_TOKEN=<same admin token as market-sentinel.env>
MARKET_SENTINEL_CADDY_PASSWORD_HASH=<hash printed by caddy hash-password>
```

Generate the admin token once with `openssl rand -hex 32` and put the exact
same value in both this Caddy file and
`/etc/market-sentinel/market-sentinel.env`; do not generate a second proxy
token. Set `MARKET_SENTINEL_ALLOWED_ORIGINS=https://<public-hostname>` in the
web service environment file, using the hostname in the Caddyfile. Generate
another value with `openssl rand -hex 32` and place the result in that file as
`MARKET_SENTINEL_OBSERVABILITY_TOKEN=<generated-value>`. Never reuse the admin
token for this least-privilege credential. It is accepted only for
`GET /api/health` and `GET /metrics`; it cannot read state or invoke mutations.
Configure the same value in the separate root-owned mode-`0600`
`/etc/market-sentinel/market-sentinel-health.env` file. That file must contain
only `MARKET_SENTINEL_OBSERVABILITY_TOKEN`; the health unit does not load the
admin environment and fails closed if the observer credential is absent. The
standalone probe retains admin-token fallback only for local backward
compatibility when observability-only mode is not requested.

Check the protected Caddy file and service binding before allowing public
traffic. Validation must use the same file that systemd loads; plain `caddy
validate` in an interactive shell can expand different values. Restart Caddy
after changing either the Caddyfile or its environment file, then run the
public authentication probes described under Deployment evidence:

```bash
test "$(sudo stat -c '%U:%G:%a' /etc/caddy/market-sentinel.env)" = 'root:root:600'
sudo systemctl daemon-reload
sudo systemctl cat caddy.service
sudo caddy validate --config /etc/caddy/Caddyfile --envfile /etc/caddy/market-sentinel.env
sudo systemctl enable --now caddy.service
sudo systemctl restart caddy.service
sudo systemctl is-active caddy.service
```

Confirm the drop-in appears in `systemctl cat` with the exact non-optional
`EnvironmentFile` path. A successful Caddy parse alone does not prove the two
tokens agree; the authenticated public deployment probe checks that behavior.

The bundled Prometheus scrape configuration reads a third copy from
`/etc/prometheus/market-sentinel-observability-token`. Unlike the two systemd
environment files, this `credentials_file` must contain only the raw token and
one trailing newline -- never a `MARKET_SENTINEL_OBSERVABILITY_TOKEN=`
assignment. After installing Prometheus so its service group exists, provision
the file without ever putting the bearer value in a command-line argument:

```bash
# Enter the exact observer value already stored in both systemd environment files.
IFS= read -r -s -p "Observability token: " OBSERVABILITY_TOKEN; printf '\n'
sudo install -o root -g prometheus -m 0640 /dev/null /etc/prometheus/market-sentinel-observability-token
printf '%s\n' "${OBSERVABILITY_TOKEN}" | sudo tee /etc/prometheus/market-sentinel-observability-token >/dev/null
sudo chown root:prometheus /etc/prometheus/market-sentinel-observability-token
sudo chmod 0640 /etc/prometheus/market-sentinel-observability-token
test "$(sudo stat -c '%U:%G:%a' /etc/prometheus/market-sentinel-observability-token)" = "root:prometheus:640"
sudo -u prometheus test -r /etc/prometheus/market-sentinel-observability-token
unset OBSERVABILITY_TOKEN
```

Install and validate the alert rules, then merge both top-level mappings from
`deploy/prometheus/market-sentinel-scrape.yml` into the host's main
`/etc/prometheus/prometheus.yml`; the checked-in file is a reviewed fragment,
not a standalone include file:

```bash
sudo install -d -o root -g prometheus -m 0750 /etc/prometheus/rules
sudo install -o root -g prometheus -m 0640 \
  deploy/prometheus/market-sentinel-alerts.yml \
  /etc/prometheus/rules/market-sentinel-alerts.yml
# Merge scrape_configs and rule_files from the reviewed fragment, preserving
# any existing top-level entries, then validate the complete deployed config.
sudo promtool check rules /etc/prometheus/rules/market-sentinel-alerts.yml
sudo promtool check config /etc/prometheus/prometheus.yml
sudo systemctl reload prometheus
sudo systemctl is-active prometheus
```

For score-eligible delivery evidence, also merge the reviewed top-level
`rule_files` and `alerting` mappings from
`deploy/prometheus/market-sentinel-attestation-prometheus.yml.example` into the
complete Prometheus configuration. Merge the child route and receiver from
`deploy/prometheus/market-sentinel-attestation-alertmanager.yml.example` into
the complete Alertmanager configuration. They are merge fragments, not
standalone configuration files. Keep Prometheus and Alertmanager API listeners
on loopback, enable Prometheus lifecycle reload only on that loopback listener,
and provision the run-unique rule directory for root-written,
Prometheus-readable files. Before reloading Alertmanager, create the two
regular, non-symlink files named by the fragment:

- `/etc/market-sentinel/alertmanager-oncall-webhook-url` contains exactly
  `https://<public-receipt-origin>/v1/market-sentinel/alertmanager`, without a
  trailing newline.
- `/etc/market-sentinel/alertmanager-oncall-bearer-token` contains exactly the
  protected production bearer token, without a trailing newline.

Both files must be root-owned and either mode `0600` for root-only access, or
mode `0640` with a numeric group owner exactly equal to the protected production
environment variable `MARKET_SENTINEL_ALERTMANAGER_GID`. Resolve the service
group with `getent group alertmanager` on the production evidence host, record
its positive numeric GID in that variable, and update the variable whenever the
host's service-group identity changes. The collector rejects a `0640` file with
any other group owner. The receipt origin must be a canonical public HTTPS
origin whose DNS answers are exclusively public; redirects, credentials in the
URL, private addresses, and reserved example names are rejected. Configure the
same origin as the protected production environment variable
`MARKET_SENTINEL_ONCALL_RECEIPT_ORIGIN` and the same token as the protected
secret `MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN`.

The independent SMTP-backed bridge, its separate-host service configuration,
and the human acknowledgement procedure are in
[`ONCALL_RECEIPT_BRIDGE.md`](ONCALL_RECEIPT_BRIDGE.md).

```bash
sudo install -d -o root -g prometheus -m 0750 /var/lib/prometheus/market-sentinel-attestation
sudo promtool check config /etc/prometheus/prometheus.yml
sudo amtool check-config /etc/alertmanager/alertmanager.yml
sudo systemctl reload prometheus
sudo systemctl reload alertmanager
```

The production evidence workflow supplies a GitHub-hosted random challenge and
runs the checked-in collector on the production host. The collector creates one
controlled test alert through a challenge-bound `vector(1)` rule, proves the
exact file was loaded and firing in
Prometheus, proves the same fingerprint reached Alertmanager's scoped receiver
and the controlled loopback webhook, validates Alertmanager's live loaded
configuration for both exact receivers, and polls the separate public receipt
bridge. The bridge receipt must bind the exact revision, deployment identity,
workflow run, challenge, Alertmanager fingerprint, canonical webhook event and
raw webhook digest; it must show dispatch followed by a later human
acknowledgement with hashed channel and acknowledger identities. The collector
then removes the rule, reloads, and proves the bound rule is absent. A
GitHub-hosted reviewer recomputes every binding, configuration/body/event hash,
and timestamp before the canonical deployment report is attested. A loopback
delivery, reload response, rule evaluation, bridge-authored success boolean, or
unacknowledged provider delivery alone earns no alert-delivery point.

The receipt authority must report `received_at` causally after the delivered
alert's `startsAt`, followed by `dispatched_at` and a strictly later human
`acknowledged_at`. Because `startsAt` and the receipt timestamps can come from
different hosts, the collector and reviewer tolerate at most 60 seconds of
clock skew at that boundary. Collector-host observations are ordered only where
the collector caused them. Receipt-authority timestamps use the same bounded
skew against the collector's challenge window; they are not ordered against
independent Prometheus or Alertmanager HTTP observation completion timestamps.

The collector ignores ambient proxy settings and pins its authenticated receipt
GET to the public DNS answers it validates before sending the bearer token,
while preserving TLS hostname verification. Alertmanager resolves the separate
webhook POST itself, so also restrict the production host's outbound firewall
to the bridge's reviewed public addresses or address ranges; treat any approved
DNS/address change as a controlled configuration update. The bridge is an
explicitly trusted human-acknowledgement authority, not provider-signed
cryptographic proof, and therefore needs equivalent access controls, audit
retention, and change review.

On rotation, generate one replacement value,
write it to both systemd environment files and the raw Prometheus file, then
restart `market-sentinel-web`, run `market-sentinel-health.service` to verify
the isolated credential, and reload Prometheus. Updating only one copy causes
the health timer or metrics scrapes to fail closed with `401`.

Configure DNS and
permit only ports 80/443 to Caddy.
Keep 8765 private. Test the public hostname, the TLS renewal path, and
authenticated browser flow before enabling any live feature. Set
`MARKET_SENTINEL_ALLOWED_ORIGINS` in that protected environment file to the exact
public Caddy origin; it must match the replaced Caddy hostname, omit any path,
and must not use a wildcard. Multiple separately trusted origins are
comma-separated.

## Deployment evidence

After a deployment, collect a read-only verification record from the VPS. It
checks the systemd web service, health timer, both unattended worker timers and
their exact one-shot service contracts, validates the loopback health
endpoint, authenticated Prometheus metrics endpoint, and release version, and, when given a public URL, proves that an
unauthenticated request receives `401` before validating the authenticated HTTPS
proxy response, cache policy, the required browser-security header directives,
and removal of the public `Server` header.
It also verifies the root-owned, private service environment file and private
health credential file, proves the health unit removes the admin variable and
runs in mandatory observability-only mode, rejects extra assignments in that
file, and checks the private state/backup directories used by the bundled
systemd units. It independently parses the bounded, regular-file-only worker
status and requires recent successful alert-refresh and wallet-poll entries,
zero current problem counts, no consecutive failures, exact task/run identity,
and timestamps consistent with successful systemd completions. The canonical
hosted report additionally includes the independently reviewed synthetic alert
delivery transcript. Either missing capability fails the two corresponding
operations-readiness points closed.
It extracts only the four non-secret durable-path values from the running web
process environment for evidence, proves they are the exact paths beneath the
sandbox-writable state directory, and checks the effective backup command
captures that directory. A
missing variable, a release-tree path, an unbacked path, or a stale installed
unit makes the evidence fail rather than silently accepting partial backups.
It also requires a successful backup service completion and independently opens
at least one archive/manifest pair from the trusted private backup directory,
verifies its SHA-256 digest and bounded archive structure, and requires its
manifest timestamp to be within the last 26 hours. Enable the timer and run the
service once before collecting deployment evidence. The bundled directory is
the safe default; use `--backup-directory` only when the systemd backup
destination was intentionally changed to another absolute, private,
service-owned path with no symbolic-link components.
`--expected-version` is required: it prevents a healthy but stale deployment
from being accepted as release evidence.
`--expected-source-revision` is also required: it prevents a healthy service
from being accepted when the checkout does not match the intended release
commit. Resolve it from the trusted release tag before running the verifier.
`--expected-frontend-sha256` binds both the running process and the files served
from disk to the fingerprint captured from the reviewed frontend build. The
verifier does not derive this expected value from the mutable live tree.
It does not place orders, contact market APIs, or enable any live feature.

```bash
export MARKET_SENTINEL_PUBLIC_BASIC_USER="operator"
export MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD="the-existing-caddy-password"
export MARKET_SENTINEL_API_TOKEN="the-existing-market-sentinel-api-token"
RELEASE_VERSION="<RELEASE_VERSION>"
EXPECTED_SOURCE_REVISION="$(git -C /opt/market-sentinel rev-parse --verify "v${RELEASE_VERSION}^{commit}")"
EXPECTED_FRONTEND_SHA256="$(sudo cat /etc/market-sentinel/frontend-dist.sha256)"

sudo --preserve-env=MARKET_SENTINEL_PUBLIC_BASIC_USER,MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD,MARKET_SENTINEL_API_TOKEN \
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/verify_production_deployment.py \
  --expected-version "${RELEASE_VERSION}" \
  --expected-source-revision "${EXPECTED_SOURCE_REVISION}" \
  --expected-frontend-sha256 "${EXPECTED_FRONTEND_SHA256}" \
  --frontend-dir /opt/market-sentinel/frontend/dist \
  --backup-directory /var/lib/market-sentinel-backups \
  --public-url https://analytics.example.com \
  --output /var/lib/market-sentinel-deployment-evidence/deployment-evidence-<RELEASE_VERSION>.json
```

Keep the password and API token only in the environment. Do not pass either
secret on the command line. The API token must exactly match the token in the
root-owned service environment file; it lets the verifier prove both that the
loopback API rejects tokenless requests and that Caddy injects the configured
token only after successful Basic Auth.
The generated JSON contains a schema version, UTC collection timestamp, and source version/revision status but no credentials; `--output` requires an existing,
private root-owned parent directory, writes atomically with mode `0600`, and
syncs the replacement directory entry on POSIX so a service account cannot
replace the release-change record. Repeat
the verification after every restore drill. The command
uses `sudo` because it verifies the root-owned service environment file; it
preserves only the two explicitly named Basic Auth variables and the API token
needed for the public proxy and loopback authentication checks. For a
loopback-only staging host, omit `--public-url`; the script will still validate
the local service and timer, but retain all three expected identity arguments for the
deployed release.

Production collection also restores the newest verified backup into a private
temporary directory and boots an isolated read-only application from that copy.
The backed-up `config.json` must exist and load without dropping durable journal
records. Known JSON stores must pass the application's readiness checks, and
restored SQLite files must pass integrity and foreign-key checks. The probe
requires successful health and state responses, tests that all mutation methods
are rejected, and compares file hashes before and after startup. Its subprocess
has a 60-second budget, no inherited credentials or proxies, redirected durable
store paths, and backend socket/DNS connections denied. It never promotes the
restored files over running production state.

The resulting restore evidence includes the application version, source and
frontend fingerprints. Inventory-only restore reports are no longer accepted by
the reviewer or readiness scorer. This isolated probe is not evidence of
off-host backup recovery, service-account permissions, full business-workflow
recovery, measured RPO/RTO, or an actual systemd failover; those operational drills
still need to be performed on the deployment host.

Review the raw collector output directly; do not translate its results into a
hand-written readiness manifest. The reviewer recomputes the raw file digest,
requires a fresh production-mode collection with the exact systemd and public
proxy inventory, rechecks the clean source/runtime/frontend identities, and
rejects missing, duplicate, failed, or unknown checks:

```bash
/opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/review_deployment_evidence.py \
  /var/lib/market-sentinel-deployment-evidence/deployment-evidence-<RELEASE_VERSION>.json \
  --expected-version "${RELEASE_VERSION}" \
  --expected-revision "${EXPECTED_SOURCE_REVISION}" \
  --json
```

The review is deliberately ineligible when the collector used
`--skip-systemd`, omitted `--public-url`, was re-reviewed after its freshness
window, or reported a backup that is no longer recent. Preserve the original
raw bytes for later attestation; changing whitespace also changes the bound
SHA-256 digest.

For score-eligible evidence, manually run the protected-main **Production
deployment evidence** workflow with the exact stable release tag and production
HTTPS origin. Its production-labeled self-hosted collector verifies the live
host; the separate external-probe job attests the exact canonical
`external-probe.json` bytes before upload. The hosted review verifies that
source attestation against the exact workflow SHA, run, attempt, protected-main
ref, and GitHub-hosted runner certificate, then binds the probe object into and
attests canonical `deployment-evidence.json`. The scorer reconstructs the probe
bytes from that envelope and independently repeats the semantic and source
attestation checks. Download the final artifact and pass it with the identical
`--deployment-origin`. Raw reports, handwritten wrappers, and reviewer summaries
remain diagnostic-only. Do not use staging, generic self-hosted runners, or
placeholder origins for this workflow.
Configure `MARKET_SENTINEL_PRODUCTION_ORIGIN` as a protected `production`
environment variable. The workflow rejects an input that is not byte-for-byte
equal to that canonical public origin, rejects private or non-global resolution,
and completes this check before the collector job can access credentials. The
collector executes the verifier from the protected-main checkout with system
Python and passes `/opt/market-sentinel` only as the inspected deployment root;
it never executes a mutable verifier from the deployed checkout. Authenticated
public probes do not follow redirects. Raw evidence is nonce-bound to the exact
workflow SHA, run ID, and run attempt.

For a non-Linux or isolated local loopback smoke test only, add
`--skip-systemd`. This intentionally skips Linux systemd and filesystem
ownership checks while retaining versioned health and metrics validation; it is
not production-host evidence.

### Production rollback drill journal

Run a real rollback drill on the production Linux host before the protected
deployment-evidence workflow. Prepare complete, reviewed current and prior
stable releases first: each needs its clean Git checkout, matching installed
runtime dependencies and frontend build. Keep durable state and the root-owned
service environment outside both release trees. Preserve a known way to
reactivate the current release if the rollback fails. The release switch itself
is an operator action in a second shell; `drill_production_rollback.py` never
changes Git, service units, symlinks, or application state.

Resolve the two revisions from their reviewed stable tags. Obtain each frontend
SHA-256 from its reviewed release asset, not by hashing the mutable live tree at
drill time. Use the protected production provider label, host-identity digest,
and public origin that the deployment-evidence workflow will use. Put only the
observability token in the drill process environment; do not pass a token on
the command line or load the admin or venue environment file. Create the
report directory before starting:

```bash
sudo install -d -o root -g root -m 0700 /var/lib/market-sentinel-rollback-drills
CURRENT_VERSION='<deployed-stable-version>'
CURRENT_REVISION='<reviewed-current-tag-commit-sha>'
CURRENT_FRONTEND_SHA256='<reviewed-current-frontend-tree-sha256>'
ROLLBACK_VERSION='<reviewed-prior-stable-version>'
ROLLBACK_REVISION='<reviewed-prior-tag-commit-sha>'
ROLLBACK_FRONTEND_SHA256='<reviewed-prior-frontend-tree-sha256>'
DEPLOYMENT_PROVIDER='<protected-provider-slug>'
HOST_ID_SHA256='<protected-machine-id-sha256>'
PRODUCTION_ORIGIN='https://analytics.example.com'

sudo --preserve-env=MARKET_SENTINEL_OBSERVABILITY_TOKEN \
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/drill_production_rollback.py \
  --current-version "${CURRENT_VERSION}" \
  --current-revision "${CURRENT_REVISION}" \
  --current-frontend-sha256 "${CURRENT_FRONTEND_SHA256}" \
  --rollback-version "${ROLLBACK_VERSION}" \
  --rollback-revision "${ROLLBACK_REVISION}" \
  --rollback-frontend-sha256 "${ROLLBACK_FRONTEND_SHA256}" \
  --deployment-provider "${DEPLOYMENT_PROVIDER}" \
  --expected-host-id-sha256 "${HOST_ID_SHA256}" \
  --public-origin "${PRODUCTION_ORIGIN}" \
  --confirm-production-drill I_UNDERSTAND_THIS_RESTARTS_PRODUCTION
```

The command requires an interactive root session on Linux; there is no dry-run
mode that can write a successful journal. It first verifies the currently
running release. At the first prompt, use the prepared release-switch procedure
in the second shell to activate the prior release and restart
`market-sentinel-web.service`; type the displayed `ACTIVATED <revision>` phrase
only after that action. At the second prompt, reactivate the original release,
restart the service, and type `REACTIVATED <revision>`. The script requires a
new systemd invocation at each transition and verifies authenticated ready
health, exact version/source/frontend fingerprints, a clean checked-out Git
revision, and the frontend files on disk. It writes the verifier's exact five
ordered observations only after the full current → prior → current sequence.
Confirm public HTTPS health and both worker timers after the original release
is restored, then collect deployment evidence within 24 hours.

An existing `latest.json` is copied byte-for-byte to a unique root-private
`latest.previous-<uuid>.json` before a new attempt invalidates `latest.json`.
The active report remains `in_progress` or `failed` until every observation
passes. If any stage fails or is interrupted, the script prompts for immediate
reactivation and independently checks the current release again. If that
recovery check cannot pass, follow the prepared manual recovery procedure and
keep production evidence blocked. A failed journal, even with a verified final
current release, is never success evidence. The script cannot guarantee
recovery when a host, service, or operator action fails; an operator must remain
present for the full drill. Preserve the previous journals and systemd logs for
the operations record.

## Monitoring and recovery

- Health: `market-sentinel-health.timer` polls `GET /api/health` through
  loopback every minute using `scripts/verify_service_health.py` and the
  distinct `MARKET_SENTINEL_OBSERVABILITY_TOKEN`, rather than sending the
  admin credential in its health request. Its dedicated environment file
  contains no admin or venue credential, and the probe rejects an inherited
  admin variable in production observability-only mode. Ship failures
  of `market-sentinel-health.service` from journald to the selected monitoring
  system and alert after two consecutive failed executions.
  Health, metrics, and authentication probes reject redirects; a response from
  another endpoint cannot prove the requested endpoint is healthy or protected.
  Health and metrics bodies are capped at 1 MiB, including bodies without a
  Content-Length. Public HTTPS health evidence uses the same version and explicit
  readiness checks as loopback health. Each probe request owns one monotonic
  network deadline through DNS, TCP, TLS, response headers and body consumption;
  a slow peer cannot restart the budget by sending occasional bytes. The public
  origin's preliminary DNS check is bounded separately by the same `--timeout`.
  Multiple public probes and health retries have separate budgets, so this is
  not a deadline for the entire collection or retry loop. The supplied health
  and web-startup units additionally enforce 30-second and 60-second systemd
  limits. Use an external process deadline for standalone multi-probe runs.
  Probe DNS uses the bounded helper pool described above; expired lookups cannot
  initiate a later connection. Probe responses must be closed after consumption
  so their socket guards and deadline controller can be released.
  Operational probes are direct connections and ignore inherited HTTP/SOCKS
  proxy variables. Public probes require HTTPS and validate every DNS answer
  immediately before connecting to an approved numeric address, retaining the
  original hostname for TLS certificate verification, SNI and HTTP Host. Literal
  origins use the same public-unicast policy; multicast and reserved addresses
  are rejected. Loopback health/metrics probes intentionally permit local targets.
  These probe guarantees do not extend to separately injected venue SDKs/proxies.
  Deployment origins share one strict HTTPS parser across collection, evidence
  generation, review, and scoring. It preserves bracketed IPv6 addresses,
  canonicalizes DNS names, and rejects userinfo, malformed ports, controls, and
  non-origin paths or delimiters. All collector hostname lookups must return
  exclusively public addresses, including fixture-looking names; tests supply
  their own DNS fixtures rather than bypassing the production validator.
- Startup readiness: run `market-sentinel doctor --strict` against the service
  configuration and production frontend before each deployment and after each
  restore. It fails on corrupt configuration, unwritable storage, or missing
  dependencies, and also treats an armed live-trading configuration as a
  strict-mode failure for operator review.
- Logs: ship `journalctl -u market-sentinel-web` to the selected log system and
  alert on restart loops, authentication failures, failed safety preflights,
  and API rate-limit errors. Every completed HTTP request is emitted as one
  JSON log record with `timestamp`, `request_id`, `method`, path (without its
  query string), status, and duration. Use `request_id` when correlating an
  operator report with the reverse-proxy and service logs; it is also returned
  in the `X-Request-ID` response header.
- Metrics: the authenticated `/metrics` endpoint exposes bounded Prometheus
  counters for completed HTTP requests and request duration, plus current
  in-flight requests, overload rejections, and oversized-response rejections.
  It deliberately never uses request paths, wallets, query values, or
  credentials as labels.
  Caddy Basic Auth and the upstream API token protect this endpoint in the
  supplied deployment. Scrape it through the public proxy or a trusted
  loopback collector, and alert on sustained `5xx` responses, elevated request
  duration, sustained worker saturation or overload rejection, oversized
  responses, and an unexpected loss of request traffic.
- Backups: back up `/var/lib/market-sentinel` daily with encryption and tested
  retention. `market-sentinel-backup.timer` performs an integrity-manifested
  daily archive with 14 retained, cryptographically verified archive/manifest
  pairs. Orphaned and invalid entries remain visible for operator investigation
  but do not displace a restorable pair. The directory contains local
  configuration, paper records, the analytics cache, and redacted
  live-validation reports, decisions, and promotion snapshots. Do not back up
  `.env` files to shared or unencrypted storage.
- Restore drill: quarterly, select an archive from
  `/var/lib/market-sentinel-backups`, verify it, then restore it only into a
  brand-new path on an isolated host. The destination itself must not exist;
  create and permission its trusted parent in advance:

  ```bash
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/restore_state_backup.py \
    --archive /var/lib/market-sentinel-backups/<archive>.tar.gz
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/restore_state_backup.py \
    --archive /var/lib/market-sentinel-backups/<archive>.tar.gz \
    --destination /var/lib/market-sentinel-restore-drill
  ```

  The restore command rejects checksum mismatches, unsafe archive paths,
  compressed archives larger than 256 MiB, expanded archives larger than
  the 1 GiB file-payload limit plus strictly bounded tar headers, padding, and
  extension metadata, archives with more than 10,000 members, oversized
  cumulative PAX/GNU metadata, and every pre-existing
  destination (including an empty directory or file). The compressed and
  expanded limits can be lowered for a drill with `--max-archive-bytes` and
  `--max-bytes`; do not raise them without reviewing the expected backup size.
  The tool resolves an existing parent and atomically creates the final restore
  directory with private permissions, reducing final-component symlink races.
  Start the service loopback-only from the restored state, run the health
  check, and confirm no live trading is enabled by restored configuration.
- Copied-backup drill: on a separate recovery host, obtain a backup archive and
  its adjacent `.json` manifest through the operator's encrypted backup
  transport. Record the archive SHA-256 and backup creation timestamp from the
  trusted source inventory *before* transferring the pair; do not read either
  expected value from the recovered manifest. Install the reviewed release and
  frontend on the recovery host, make a private parent directory for the new
  restore destination, then run:

  ```bash
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/drill_state_recovery.py \
    --archive /mnt/recovered-backups/<archive>.tar.gz \
    --destination /var/lib/market-sentinel-recovery-drill/<new-state-directory> \
    --expected-sha256 '<source-inventory-archive-sha256>' \
    --expected-created-at '<source-inventory-created-at-utc>' \
    --frontend-dir /opt/market-sentinel/frontend/dist \
    --expected-version '<reviewed-release-version>' \
    --expected-source-revision '<reviewed-release-commit>' \
    --expected-frontend-sha256 '<reviewed-frontend-sha256>' \
    --max-backup-age-seconds 93600 \
    --max-restore-validation-seconds 300
  ```

  The command verifies the copied pair, refuses a pre-existing destination,
  restores it privately, checks its file inventory, and boots the isolated
  read-only application probe without credentials or venue access. It emits
  backup age at drill start and elapsed time from local archive verification
  through application validation, and exits unsuccessfully if either exceeds
  the chosen limit. Preserve the JSON output, source inventory, transfer log,
  and host identity in the operations record. A failed run can leave a private
  partial restore for investigation; use a new destination for any retry. The
  script cannot establish that the copy was truly off-host or authenticate the
  source inventory by itself.
  Backup age is a conservative freshness bound, not measured data-loss RPO;
  elapsed restore-validation time excludes incident detection, host provisioning,
  archive transfer, and public-service cutover, so it is not end-to-end RTO.
  This diagnostic report is not accepted as production-readiness score evidence.
- Configuration recovery: an existing malformed `config.json` now fails closed
  and is never silently replaced with defaults. Stop the service and use the
  restore command above to extract the most recent verified backup into a
  brand-new private sibling directory; the restore destination is a directory,
  never the `config.json` path itself. Run `market-sentinel doctor --config
  <new-directory>/config.json` and review that live trading remains disabled or
  intentionally configured. Preserve the malformed file in a root-only
  incident directory, install the verified recovered file through a temporary
  `0600` path with the service account's ownership, and atomically rename that
  temporary file over `/var/lib/market-sentinel/config.json`. Run the loopback
  health checks before restarting public access. Do not delete the damaged file
  until the restored configuration has been verified.

## Incident response

1. Set each affected market's `live_trading_kill_switch=true`, stop the
   service, and revoke exposed API credentials at the venue.
2. Preserve systemd logs and redacted live-validation reports; do not copy raw
   secrets into tickets or chat.
3. Rotate the reverse-proxy API token and operator password; validate service
   health before restoring read-only operation.
4. Create a GitHub private security advisory for product vulnerabilities.
5. For funded incidents, reconcile venue orders, fills, balances, and local
   audit output before considering any live re-enable request.

## Release acceptance

Before deploying a new release, verify its GitHub Actions run, checksum file,
SPDX SBOM, and build-provenance attestation. The release workflow rejects a tag
unless its target commit is already reachable from protected `main`; do not
publish from an unmerged feature branch. Confirm the release tag matches
`pyproject.toml`, install `requirements.lock`, and perform a staged loopback
deployment before public proxy cutover. Install `requirements-live.lock` only
where authenticated CLOB signing is explicitly approved.

### Funded production acceptance

The CLOB V2 wrapper and bounded audit's recovery-journal path are implemented.
Normal product execution remains disabled. The separate bounded audit capability
is enabled only through its dedicated one-shot factory: one allow-listed,
hard-capped, post-only GTC placement followed by exact-ID cancellation inside the
journaled verifier. Its checked-in invocation is confined to the protected
production evidence workflow and still needs explicit operator approval,
eligible credentials/funding, and exact-revision review before it can run. Do
not treat that audit capability or offline tests as permission to enable normal
product execution.

The journal requires a private POSIX directory; Windows funded journals remain
unavailable because the collector cannot verify an owner-only directory ACL.
A prior journal is read with a 64 KiB limit and strict JSON decoding. Duplicate
keys, non-finite numbers, incomplete identity, unsupported schema versions,
unresolved state, or contradictory cancellation/zero-fill evidence block a new
audit without moving or overwriting the prior file. A valid resolved journal
is archived before the next audit. New writes are atomic and size-limited;
failed final persistence cannot establish resolution. These file checks do not
authenticate venue evidence or replace manual reconciliation after an ambiguous
outcome, a stale lock, or a storage failure.

Actual acceptance requires a current credentialed-read report and a deliberately
approved, capped order/cancel report with post-cancel verification. Dry-run,
browser-smoke, readiness-only, and legacy V1 reports are not substitutes.
