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
- Run under the dedicated `market-sentinel` user. Use `/var/lib/market-sentinel`
  for state and a root-owned `/etc/market-sentinel/market-sentinel.env` for
  credentials and tokens.
- Do not enable funded trading or live copy execution in a service until the
  evidence gates in `README.md` and `polymarket/live_verification.py` pass.

## Install on RHEL/Rocky/Ubuntu

```bash
sudo useradd --system --home /var/lib/market-sentinel --shell /sbin/nologin market-sentinel
sudo install -d -o market-sentinel -g market-sentinel -m 0700 /var/lib/market-sentinel
sudo install -d -o root -g market-sentinel -m 0750 /etc/market-sentinel
sudo install -m 0600 deploy/systemd/market-sentinel.env.example /etc/market-sentinel/market-sentinel.env
sudo install -m 0644 deploy/systemd/market-sentinel.conf /etc/tmpfiles.d/market-sentinel.conf
sudo systemd-tmpfiles --create /etc/tmpfiles.d/market-sentinel.conf

sudo mkdir -p /opt/market-sentinel
sudo chown "$USER" /opt/market-sentinel
git clone https://github.com/Yunushan/market-sentinel.git /opt/market-sentinel
cd /opt/market-sentinel
# Validate the checked-out source with the test dependency set before deployment.
python3 -m venv .verify-venv
.verify-venv/bin/python -m pip install --upgrade pip
.verify-venv/bin/python -m pip install --require-hashes -r requirements-test.lock
.verify-venv/bin/python -m pip install --no-deps .
.verify-venv/bin/python verify.py --frontend-build --frontend-live-smoke
rm -rf .verify-venv

# Install the lean runtime dependency set used by the systemd service.
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps .
```

An authenticated Polymarket CLOB SDK is intentionally excluded from the
baseline runtime. Install it only for an explicitly approved signed-trading
workflow:

```bash
.venv/bin/python -m pip install --require-hashes -r requirements-live.lock
```

Build the React frontend before starting the service:

```bash
cd /opt/market-sentinel/frontend
npm ci
npm run build
```

Install the systemd unit and validate it:

```bash
sudo install -m 0644 deploy/systemd/market-sentinel-web.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-health.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-health.timer /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-backup.service /etc/systemd/system/
sudo install -m 0644 deploy/systemd/market-sentinel-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now market-sentinel-web
sudo systemctl enable --now market-sentinel-health.timer
sudo systemctl enable --now market-sentinel-backup.timer
sudo systemctl start market-sentinel-backup.service
sudo systemctl status market-sentinel-web
sudo systemctl status market-sentinel-health.timer
sudo systemctl status market-sentinel-backup.timer
sudo journalctl -u market-sentinel-web -f
/opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/verify_service_health.py
/opt/market-sentinel/.venv/bin/market-sentinel doctor --strict --config /var/lib/market-sentinel/config.json --frontend-dir /opt/market-sentinel/frontend/dist
```

The web and health units use strict systemd sandboxes, private device and
hostname/clock namespaces, restricted network address families, and a root-owned
environment file. The web unit has a strict read-only `doctor` preflight before
startup and a startup health check; the timer runs a separate loopback health
check every minute. Both units limit start failures to five attempts in five
minutes, and the health unit times out after 30 seconds. A start-limit hit is an
operator action item rather than a signal to retry continuously; inspect
`journalctl -u market-sentinel-web` or `journalctl -u market-sentinel-health`
and use `systemctl reset-failed` only after correcting the cause. Review
`systemd-analyze security market-sentinel-web` and
`systemd-analyze security market-sentinel-health` after installation and tighten
any setting that does not prevent normal operation on the chosen distribution.
The web unit manages `/var/lib/market-sentinel` with `StateDirectory` and mode
`0700`, so a normal service start does not depend on a pre-existing writable
state directory. The initial install command remains useful for inspecting
ownership before the first start.

The backup timer runs a local, network-isolated state backup each day with a
14-artifact retention limit. It writes archives and SHA-256 manifests only to
`/var/lib/market-sentinel-backups`, owned by the service account and separate
from the live state directory. Place `/var/lib` on encrypted storage or change
the backup destination to an encrypted mounted volume before using this in
production. Archive, manifest, and retention updates are published atomically
and their directory changes are synced on POSIX filesystems. SQLite state databases are captured with SQLite's online backup API
instead of copying WAL sidecar files. The archive intentionally excludes
`/etc/market-sentinel` and its credentials; protect and back up that root-owned
configuration through the host's secret-management and configuration process.

## TLS and browser access

Install Caddy from its official package repository, copy
`deploy/caddy/Caddyfile.example` to `/etc/caddy/Caddyfile`, and replace the
example hostname. Set these protected Caddy environment values:

```bash
MARKET_SENTINEL_API_TOKEN="$(openssl rand -hex 32)"
MARKET_SENTINEL_CADDY_PASSWORD_HASH="$(caddy hash-password --plaintext 'replace-this-password')"
MARKET_SENTINEL_ALLOWED_ORIGINS="https://analytics.example.com"
```

Use the same `MARKET_SENTINEL_API_TOKEN` in
`/etc/market-sentinel/market-sentinel.env`. Configure DNS and permit only ports
80/443 to Caddy. Keep 8765 private. Test the public hostname, the TLS renewal
path, and authenticated browser flow before enabling any live feature. Set
`MARKET_SENTINEL_ALLOWED_ORIGINS` in that protected environment file to the exact
public Caddy origin; it must match the replaced Caddy hostname, omit any path,
and must not use a wildcard. Multiple separately trusted origins are
comma-separated.

## Deployment evidence

After a deployment, collect a read-only verification record from the VPS. It
checks the systemd web service and health timer, validates the loopback health
endpoint and release version, and, when given a public URL, proves that an
unauthenticated request receives `401` before validating the authenticated HTTPS
proxy response, cache policy, and browser security headers.
It also verifies the root-owned, private service environment file and private
state/backup directories used by the bundled systemd units.
It also requires a successful backup completed within the last 26 hours; enable
the timer and run the service once before collecting deployment evidence.
It does not place orders, contact market APIs, or enable any live feature.

```bash
export MARKET_SENTINEL_PUBLIC_BASIC_USER="operator"
export MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD="the-existing-caddy-password"

sudo --preserve-env=MARKET_SENTINEL_PUBLIC_BASIC_USER,MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD \
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/verify_production_deployment.py \
  --expected-version <RELEASE_VERSION> \
  --public-url https://analytics.example.com \
  --output /var/lib/market-sentinel-deployment-evidence/deployment-evidence-<RELEASE_VERSION>.json
```

Keep the password only in the environment. Do not pass it on the command line.
The generated JSON contains a schema version, UTC collection timestamp, and source version/revision status but no credentials; `--output` requires an existing,
private root-owned parent directory, writes atomically with mode `0600`, and
syncs the replacement directory entry on POSIX so a service account cannot
replace the release-change record. Repeat
the verification after every restore drill. The command
uses `sudo` because it verifies the root-owned service environment file; it
preserves only the two explicitly named Basic Auth variables for the public
proxy check. For a
loopback-only staging host, omit `--public-url`; the script will still validate
the local service and timer.

## Monitoring and recovery

- Health: `market-sentinel-health.timer` polls `GET /api/health` through
  loopback every minute using `scripts/verify_service_health.py`. Ship failures
  of `market-sentinel-health.service` from journald to the selected monitoring
  system and alert after two consecutive failed executions.
- Startup readiness: run `market-sentinel doctor --strict` against the service
  configuration and production frontend before each deployment and after each
  restore. It fails on corrupt configuration, unwritable storage, or missing
  dependencies, and also treats an armed live-trading configuration as a
  strict-mode failure for operator review.
- Logs: ship `journalctl -u market-sentinel-web` to the selected log system and
  alert on restart loops, authentication failures, failed safety preflights,
  and API rate-limit errors.
- Backups: back up `/var/lib/market-sentinel` daily with encryption and tested
  retention. `market-sentinel-backup.timer` performs an integrity-manifested
  daily archive with 14 retained copies. The directory contains local
  configuration, paper records, and redacted live-validation reports. Do not
  back up `.env` files to shared or unencrypted storage.
- Restore drill: quarterly, select an archive from
  `/var/lib/market-sentinel-backups`, verify it, then restore it only into a
  new empty directory on an isolated host:

  ```bash
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/restore_state_backup.py \
    --archive /var/lib/market-sentinel-backups/<archive>.tar.gz
  /opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/restore_state_backup.py \
    --archive /var/lib/market-sentinel-backups/<archive>.tar.gz \
    --destination /var/lib/market-sentinel-restore-drill
  ```

  The restore command rejects checksum mismatches, unsafe archive paths,
  archive bombs beyond its stated safety limits, and nonempty destinations.
  Start the service loopback-only from the restored state, run the health
  check, and confirm no live trading is enabled by restored configuration.
- Configuration recovery: an existing malformed `config.json` now fails closed
  and is never silently replaced with defaults. Preserve that file for
  investigation, restore the most recent verified backup to
  `/var/lib/market-sentinel/config.json`, then run the health check before
  restarting the service. Do not delete the damaged file until the restored
  configuration has been verified.

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

Funded production acceptance additionally requires a current credentialed-read
report and a deliberately approved, capped order/cancel report with
post-cancel verification. Dry-run, browser-smoke, and readiness-only reports
are not substitutes.
