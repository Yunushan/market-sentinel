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
  provided Caddy example supplies Basic Auth, TLS, security headers, and the
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

sudo mkdir -p /opt/market-sentinel
sudo chown "$USER" /opt/market-sentinel
git clone https://github.com/Yunushan/market-sentinel.git /opt/market-sentinel
cd /opt/market-sentinel
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install --require-hashes -r requirements.lock
.venv/bin/python -m pip install --no-deps .
.venv/bin/python verify.py --frontend-build --frontend-live-smoke
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
sudo systemctl daemon-reload
sudo systemctl enable --now market-sentinel-web
sudo systemctl status market-sentinel-web
sudo journalctl -u market-sentinel-web -f
/opt/market-sentinel/.venv/bin/python /opt/market-sentinel/scripts/verify_service_health.py
```

The unit uses restart-on-failure, a strict systemd sandbox, a root-owned
environment file, and a health check after startup. Review `systemd-analyze
security market-sentinel-web` after installation and tighten any setting that
does not prevent normal operation on the chosen distribution.

## TLS and browser access

Install Caddy from its official package repository, copy
`deploy/caddy/Caddyfile.example` to `/etc/caddy/Caddyfile`, and replace the
example hostname. Set these protected Caddy environment values:

```bash
MARKET_SENTINEL_API_TOKEN="$(openssl rand -hex 32)"
MARKET_SENTINEL_CADDY_PASSWORD_HASH="$(caddy hash-password --plaintext 'replace-this-password')"
```

Use the same `MARKET_SENTINEL_API_TOKEN` in
`/etc/market-sentinel/market-sentinel.env`. Configure DNS and permit only ports
80/443 to Caddy. Keep 8765 private. Test the public hostname, the TLS renewal
path, and authenticated browser flow before enabling any live feature.

## Monitoring and recovery

- Health: poll `GET /api/health` through loopback every minute using
  `scripts/verify_service_health.py`; alert after two consecutive failures.
- Logs: ship `journalctl -u market-sentinel-web` to the selected log system and
  alert on restart loops, authentication failures, failed safety preflights,
  and API rate-limit errors.
- Backups: back up `/var/lib/market-sentinel` daily with encryption and tested
  retention. The directory contains local configuration, paper records, and
  redacted live-validation reports. Do not back up `.env` files to shared or
  unencrypted storage.
- Restore drill: quarterly, restore a backup into an isolated host, start the
  service loopback-only, run the health check, and confirm no live trading is
  enabled by restored configuration.

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
SPDX SBOM, and build-provenance attestation. Confirm the release tag matches
`pyproject.toml`, install only from `requirements.lock`, and perform a staged
loopback deployment before public proxy cutover.

Funded production acceptance additionally requires a current credentialed-read
report and a deliberately approved, capped order/cancel report with
post-cancel verification. Dry-run, browser-smoke, and readiness-only reports
are not substitutes.
