# MarketSentinel privacy policy

This disclosure covers the open-source MarketSentinel desktop app, local web
interface, and a deployment you operate yourself. The project does not
currently offer a hosted MarketSentinel account or production service. If you
deploy the app for other people, you control the server, reverse proxy,
monitoring, logs, backups, access, and retention for that deployment.

## Data kept by the app

MarketSentinel can save selected markets, watched wallet addresses or
usernames, alerts, paper orders, copy settings, and related history in a local
configuration file. Source and writable portable installs default to
`data/config.json`; the Windows installer launcher uses
`%APPDATA%\market-sentinel\data\config.json` when the package directory is not
writable. Optional analytics caches, long-running scan
databases, live-validation reports, logs, exports, and backup files may contain
wallet, account, market, order, or activity details. Their locations can be
changed by the operator. These files may remain until their applicable purge
control is used or the operator removes them; deleting the app alone does not
remove every custom path, export, or backup.

Credentials for optional authenticated features are supplied through
environment variables or protected credential files. The configuration loader
and writer reject credential values in `config.json`. Operators remain
responsible for protecting their environment files, logs, exports, and backups.

## Network requests

The browser interface talks to the MarketSentinel Python API. That API binds
to loopback by default; publishing it to other users requires an explicit
operator configuration and access controls.

Market data, wallet tracking, alerts, account reads, and optional trading
features contact the selected venue's API. Polymarket is the enabled venue in
the example configuration; other venue adapters are disabled there until
selected. Enabled background alert and wallet pollers can continue requesting
data while the app runs. Depending on the feature, requests may disclose a
wallet address, username, market identifier, account credential or signed
authentication data, and the client's IP address to that venue. A manually
started dependency-version check queries PyPI for package versions. This
repository does not configure a separate MarketSentinel-operated analytics or
advertising service; its `/metrics` endpoint reports aggregate HTTP activity
to the operator's local or configured monitoring system.

The selected venue's terms and privacy policy govern data that venue receives.
For the default enabled venue, see [Polymarket's privacy policy](https://polymarket.com/privacy).
Before enabling another adapter, review that venue's official privacy policy
and the adapter's documented endpoints in the
[market capability matrix](README.md#market-capability-matrix). A hosted
operator's own hosting and monitoring providers may also process request and
operational data under that operator's policies. For the optional dependency
check, see [PyPI's privacy notice](https://policies.python.org/pypi.org/Privacy-Notice/).

## Access, deletion, and questions

On a local installation, you control the data files and can remove watched
wallets, alerts, paper history, cached analytics, and exports using available
app controls or by deleting the relevant local files after stopping the app.
Check custom storage paths and backups separately. Removing local data does
not remove records held by a venue or published on a blockchain.

For general questions, open an issue in the
[MarketSentinel repository](https://github.com/Yunushan/market-sentinel/issues).
For a sensitive report, use its [private vulnerability reporting process](SECURITY.md).
