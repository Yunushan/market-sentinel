# SignPath release signing

The local integration is implemented; provider acceptance and a real signed
release remain unverified. There is no configured SignPath account, certificate
or API token in the repository's release environment. Keep this distinction in
release notes and readiness evidence. Foundation acknowledgements apply only
after Foundation acceptance and verification of a signed artifact.

## Operator setup

Create the actual SignPath organization and `market-sentinel` project, enable
the supported GitHub origin-verification integration for
`Yunushan/market-sentinel`, and require human approval on `release-signing`.
Restrict signing to this repository's reviewed `release.yml` workflow and
release source on protected `main`; inspect the exact source SHA, tag, run and
parameters before approval. Require MFA for the signing approver. The CI user
may submit and read project requests but must not approve or change policy.
These controls require provider-side read-back; source code cannot attest them.

Create configurations named `market-sentinel-exe-v1` and
`market-sentinel-msi-v1` using the exact checked-in XML in `deploy/signpath/`.
Review their provider-side contents and prevent the CI user from editing them.
Their required `nativeVersion`, `releaseTag` and `sourceRevision` parameters
bind each request to release metadata. The EXE is signed before building the
MSI; the MSI configuration signs its outer container without repacking or
signing its embedded files. ProductName and ProductVersion are independently
checked after signing because SignPath's MSI restriction attributes do not
include those properties. Provider schema acceptance of these configurations
still requires a real account and the provider's current XSD.

Configure the protected `release` environment with the three public variables
and API secret described in [repository settings](REPOSITORY_SETTINGS.md).
Pin the real public certificate's DER SHA-256 fingerprint, not a private key
or a guessed certificate. Certificate rotation requires a reviewed fingerprint
change and a new signed acceptance run. Never enable a PFX fallback.

## Reviewed connector and verification

The signer uses the [official GitHub trusted build integration](https://docs.signpath.io/trusted-build-systems/github),
which retrieves a stored GitHub artifact and supplies verified origin data.
It executes the official Node 24 bundle from commit
[`f6d04783b4569d051e0c80105fe66e82819d0092`](https://github.com/SignPath/github-action-submit-signing-request/commit/f6d04783b4569d051e0c80105fe66e82819d0092)
only after its SHA-256 matches
`40af2e2581648d7357de3c13ca081ce87ac97f0aa66fdd2c5e3f1468d3232751`.
This downloaded, pinned runtime preserves the repository's GitHub-owned
Actions allowlist. Updating either pin requires source review. The reviewed
task uses the scoped GitHub token, with `actions: read` and `contents: read`;
it does not call `getIDToken`, so the Windows job does not grant OIDC write
permission. The separate provenance publish job retains its required OIDC
permission.

Each uploaded artifact is named for its kind, exact SHA, run and attempt. The
wrapper checks GitHub's artifact ID, digest, source and workflow metadata,
verifies that the checkout stays clean, and submits only the stored artifact
ID. It waits at most 15 minutes, suppresses upstream output that might expose
provider diagnostics, and never accepts upstream extraction paths.

The wrapper reads the completed request from the fixed SignPath API origin.
The [documented response contract](https://docs.signpath.io/build-system-integration)
includes completed status, project/policy/configuration slugs, parameters and
verified repository/build origin. Missing or differing fields fail closed.
The exact tag/main source and current run URL must match. A live response from
this project's provider has not yet been accepted; any different representation
requires review rather than weakening validation.

HTTP responses have byte limits, 30-second socket timeouts and a 180-second
streaming deadline (a blocking read can add at most one socket timeout).
Redirects are rejected. The returned ZIP must contain only the expected file.
Windows verifies trusted Authenticode, the pinned signer, a timestamp, all
signatures and product metadata before an atomic replacement. The EXE may
change only its checksum, certificate directory, signature and alignment.
The read-only native MSI checker compares every structured-storage stream,
root class and state except the two exact root Authenticode signature streams.
Database tables, custom actions, cabinet bytes and summary information must
stay identical. Signature verification and MSI comparison each have a
two-minute bound and suppress child output.

## Required acceptance

After provider setup, verify a protected release through manual signing
approval, both completed request records, source/build origin, public trust,
timestamps and certificate fingerprint. Verify the final portable EXE and MSI,
then run the installed MSI smoke test and release asset/provenance checks.
Retain artifact-specific evidence. Reconcile pending requests in the provider
dashboard before retrying a timeout. No stable artifact may publish when any
provider or validation step fails. The existing explicitly unsigned prerelease
lane remains available for development and carries no production signing claim.
