# Code signing policy

## Current status

MarketSentinel is evaluating [SignPath Foundation](https://signpath.org/) as a
possible code signing provider. The current release workflow does not use
SignPath, and Foundation acceptance has not been verified. No download should
be described as Foundation-signed without artifact-specific verification.
Check published release notes and checksums; verify a signature when present.

If the project selects SignPath and the Foundation accepts it, the required
acknowledgement after a verified signed release is: “Free code signing
provided by SignPath.io, certificate by SignPath Foundation.” Release and
download pages will be updated only when that statement becomes true.

## Proposed people and responsibilities

| Role | Member | Responsibility |
| --- | --- | --- |
| Author and committer | [@Yunushan](https://github.com/Yunushan) | Maintains the source, build scripts, and protected repository. |
| Reviewer | [@Yunushan](https://github.com/Yunushan) | Reviews proposed changes from people without commit access before accepting them. |
| Signing approver | [@Yunushan](https://github.com/Yunushan) | Inspects the exact release and manually approves each signing request. |

This is a single-maintainer project. The maintainer's separate release approval
is an explicit decision, but it is not an independent person's review. GitHub
multi-factor authentication and SignPath multi-factor authentication are
required of anyone exercising these roles; current account settings must be
verified before submitting an application. SignPath's terms do not expressly
require different people for these roles, but the Foundation has not approved
this proposed assignment.

## Release boundary

The proposed signing scope is MarketSentinel's own Windows executable and MSI
installer built by the project's GitHub Actions release workflow. The project
will not use its certificate to sign third-party binaries as if it maintained
their source. Third-party dependencies included in a package retain their own
origin and licensing obligations. MarketSentinel's source is licensed under
[0BSD](LICENSE); bundled dependency licenses must be checked separately.

The locked [opinion-api 0.4.0](https://pypi.org/project/opinion-api/0.4.0/)
and [opinion-clob-sdk 0.7.0](https://pypi.org/project/opinion-clob-sdk/0.7.0/)
releases identify MIT in package metadata, but their published source archives
and installed wheels contain no full license/notice file. This leaves the
Windows third-party notice inventory incomplete. Obtain version-specific
license text and attribution from the publisher, then generate and verify a
notice inventory for the exact bundled Python and frontend dependencies before
claiming that a signed distribution has complete third-party notices.

Before Foundation signing is enabled, the release workflow must upload the
unsigned artifact to GitHub Actions, submit that artifact through SignPath's
trusted GitHub build integration, verify source and build origin, enforce
product-name and version metadata, obtain manual approval, and verify the
returned executable and installer signatures and timestamps. A release must
come from an exact tag on protected `main` and pass its required checks. The
existing stable-release signing gate stays closed until a publicly trusted
signing integration is configured and verified. The current release setup and
remaining gap are described in [CI/CD and Releases](docs/CI_CD.md) and
[Required GitHub Repository Settings](docs/REPOSITORY_SETTINGS.md).

## Privacy and installation

See the [MarketSentinel privacy policy](PRIVACY.md) for local state,
network requests, and third-party venues. The Windows MSI supports normal
Windows uninstall. A portable installation can be removed by deleting its
directory after stopping the app and separately reviewing any user data or
backups kept elsewhere.
