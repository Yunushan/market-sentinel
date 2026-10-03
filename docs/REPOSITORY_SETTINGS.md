# Required GitHub Repository Settings

These controls are configured in GitHub, not source control. An administrator
must enable them before treating a release as production-ready.

## Production main branch

Protect `main` with:

1. Required successful `Python package build`, `CodeQL`, `Dependency review`,
   `Frontend dependency audit`, `Python dependency audit`,
   `Secret history scan`, and `Workflow and shell lint` checks. `Python
   package build` is the aggregate CI gate: it waits for the supported
   Python/OS matrix, enterprise Linux containers, Windows 11, React build,
   mobile-web smoke, and real Tkinter GUI lifecycle jobs.
2. Pull requests for every change, with zero required approving reviews in
   this single-maintainer repository. Keep stale-review dismissal enabled for
   any optional reviews. Do not enable Code Owner or most-recent-push approval
   gates that the sole change author cannot satisfy.
3. Signed commits for every change admitted to the protected branch.
4. No force pushes, no branch deletion, and no direct administrator bypass for
   normal releases.
5. Required conversation resolution and linear history.

The separate tag-triggered `Release` workflow performs release validation. Its
protected `release` environment must gate publishing, code signing, SBOM,
checksums, and provenance rather than being configured as a branch status check.

## Single-maintainer authorization

`@Yunushan` is the sole maintainer. Protected pull requests, the complete
required CI gate, signed commits, exact release refs, and run-specific
environment approvals are the required controls. `.github/CODEOWNERS` records
ownership but is not a required approval gate. The maintainer's environment
approval is a deliberate second action by the same person. It does not provide
independent authorization against maintainer error or account compromise.
Readiness assessments and release decisions must describe that limitation plainly.

## Security and automation

1. Enable dependency graph, Dependabot alerts, Dependabot security updates,
   secret scanning, and push protection.
2. Enable private vulnerability reporting.
3. Review and merge or close the active Dependabot pull requests after CI.
4. In `Actions` -> `General`, allow selected actions only, permit GitHub-owned
   actions, and keep SHA pinning required. The `Security` workflow downloads
   the reviewed actionlint and gitleaks release binaries directly and verifies
   their SHA-256 digests before execution. The Windows release signer similarly
   executes SignPath's official connector bundle at a reviewed commit and
   verified SHA-256 digest, with bounded, suppressed provider output. No third-party GitHub Actions need
   to be added to the allowlist. Every checked-in action reference is pinned to
   a full commit SHA; do not broaden the allowlist without reviewing the new
   action's provenance and permissions.
5. Enable non-provider secret-pattern scanning when GitHub makes that control
   available for the repository plan. It complements, but does not replace,
   secret scanning push protection and the source-level secret hygiene gate.

## Release environment

Create the `release` environment with exactly `@Yunushan` as the required
reviewer, permit the run initiator to approve it, and select
**Selected branches and tags** deployment rules containing exactly the
protected branch `main` and tag pattern `v*.*.*`. Do not select **Protected
branches only**: GitHub applies that setting to branches, so it can block the
normal tag-triggered release job. The workflow
uses this protected environment for every stable tag, including drafts, and whenever
signing is independently required. The reviewed provider selection is the
protected variable `WINDOWS_SIGNING_PROVIDER=signpath`. Also configure
`SIGNPATH_ORGANIZATION_ID` to the actual provider UUID and
`WINDOWS_SIGNING_CERTIFICATE_SHA256` to the lowercase SHA-256 fingerprint of
the reviewed public signing certificate's DER bytes. Store `SIGNPATH_API_TOKEN`
as a protected environment secret and set `REQUIRE_WINDOWS_CODE_SIGNING=true`.
The governance collector binds these three public provider variables and the
required secret names into its canonical snapshot; missing and malformed
configuration fails validation. This inventory does not prove provider acceptance.
The API token can submit and read requests for the single `market-sentinel`
project; it must not approve signing or administer the organization.

The workflow uses SignPath's official trusted GitHub connector and verifies the
completed request and returned EXE/MSI before replacing the staged unsigned
files. An exportable PFX is rejected as a production provider. The provider
account, project, origin verification, exact artifact configurations,
manual signing approval and publicly trusted certificate still require real
operator setup and acceptance. [SignPath release signing](SIGNPATH_RELEASE_SIGNING.md)
describes that contract. [SignPath Foundation](https://signpath.org/) acceptance
is unverified. Keep the stable gate closed until an actual protected signed
release passes. Stable tags require signing even if the variable is
absent or false because a draft can later be published manually.
If credentials are unavailable, an explicitly unsigned testing/development run
must use a validated prerelease tag; only prerelease artifacts can select the unprotected
`release-unsigned` environment. The workflow labels those Windows assets
unsigned, and they must not be treated as production-trusted. Never add venue
credentials to the `release` environment or repository-wide secrets.

Also store `READINESS_ADMIN_TOKEN` in the protected `release` environment for
the manually dispatched governance-evidence workflow. Use a repository-scoped,
expiring fine-grained token with only `Administration: read`, `Environments:
read`, `Actions: read`, and repository `Variables: read`; rotate it before expiry and after any suspected
exposure. This read-only governance credential is separate from code-signing and
venue credentials. The workflow never persists its value and records only the
policy-relevant names returned by GitHub.

## Production environment

Create the separate `production` environment with exactly `@Yunushan` as its
required reviewer, permit the run initiator to approve it, and restrict
deployment to protected branches. The funded evidence lane accepts only the
first attempt of an exactly approved run and binds the approval reviewer,
environment id, live environment protections, and policy timestamps into the
attested artifact. This approval is operator confirmation, not independent
review. A funded order still requires explicit user authorization for its exact
market, limit, and cap before dispatch.

The governance collector requires the secret inventory used by the deployment
and Polymarket acceptance workflows:

- `MARKET_SENTINEL_API_TOKEN`, `MARKET_SENTINEL_PUBLIC_BASIC_USER`,
  `MARKET_SENTINEL_PUBLIC_BASIC_PASSWORD`, and
  `MARKET_SENTINEL_ONCALL_RECEIPT_TOKEN`;
- `POLY_ADDRESS`, `POLY_API_KEY`, `POLY_API_SECRET`, `POLY_PASSPHRASE`,
  `POLYMARKET_PRIVATE_KEY`, `POLYMARKET_FUNDER_ADDRESS`, and
  `POLYMARKET_SIGNATURE_TYPE`;
- `RELAYER_API_KEY` and `RELAYER_API_KEY_ADDRESS`.

The `POLYMARKET_RECOVERY_STORE_URL`, `POLYMARKET_RECOVERY_STORE_TOKEN`, and
`POLYMARKET_RECOVERY_ENCRYPTION_KEY_BASE64` names are reserved for a future
off-host recovery integration. No workflow currently uses them. Adding empty
or placeholder secrets would not provide recoverability after host loss, so
they are not part of the required governance inventory. The funded audit still
requires its durable private journal on the production runner; off-host
recovery needs a provider-specific write/read-back and restore drill.

It also requires these production environment variables:

- `MARKET_SENTINEL_PRODUCTION_ORIGIN`;
- `MARKET_SENTINEL_ONCALL_RECEIPT_ORIGIN`;
- `MARKET_SENTINEL_ALERTMANAGER_GID`, the positive numeric GID of the
  Alertmanager service group on the production evidence host;
- `MARKET_SENTINEL_DEPLOYMENT_PROVIDER`;
- `MARKET_SENTINEL_PRODUCTION_HOST_ID_SHA256`;
- `POLYMARKET_FUNDED_TOKEN_ALLOWLIST`, encoded as the compact, sorted canonical
  JSON array accepted by `polymarket.funded_policy`.

Keep every value scoped to this environment. Do not duplicate private keys,
API credentials, recovery tokens, or on-call bearer tokens in repository-level
secrets, workflow inputs, configuration files, artifacts, or logs.

## Evidence check

Before a release, collect read-only proof that the externally configured controls
match this policy. Set `GITHUB_TOKEN` only in the calling shell to a fine-grained
token with repository `Administration: read`, `Environments: read`, `Actions: read`,
and `Variables: read` permissions;
the command does not print or persist the token.

```bash
python scripts/verify_repository_settings.py \
  --repository Yunushan/market-sentinel \
  --branch main
```

It validates required checks, up-to-date and administrator-enforced branch
protection, pull-request, single-maintainer, signed-commit, conversation, and
linear-history controls, disabled force pushes and deletions,
the exact owner-only release approval route,
the exact `main`/`v*.*.*` deployment-ref policy, the current Windows-signing
secret-name contract, the owner-only protected production approval route and
branches, the required production secret and variable names, and
`REQUIRE_WINDOWS_CODE_SIGNING=true`. Passing this settings audit does not prove
that a publicly trusted signer has been integrated. It reports a nonzero exit status
on any missing control. Run it from an administrator-authorized workstation;
a normal workflow token is intentionally insufficient for this audit.

The collector also emits a canonical, secret-free governance-state snapshot and
its `governance_state_sha256`. Secret and variable values are never included;
only their names and the public `REQUIRE_WINDOWS_CODE_SIGNING` policy value are
bound. The governance-evidence workflow collects the state with an
administration-read token, re-reads it immediately before attestation, and
rejects any drift. When the readiness scorer consumes either governance
artifact, it independently re-fetches the same nine GitHub API documents with
an administration-read `GH_TOKEN` or `GITHUB_TOKEN` from the scorer process and
requires an exact digest match. The scorer intentionally uses an empty temporary
`gh` configuration and ignores stored interactive `gh auth login` credentials,
so export the token only for that invocation.
Unavailable administration-read access, a failed control, or any intervening
settings change therefore awards no governance points even if the attested
artifact is still within its freshness window.
