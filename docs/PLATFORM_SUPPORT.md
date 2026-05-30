# Platform Support

MarketSentinel does not claim full support for a platform until that platform has install instructions, dependency installation, `python app.py --smoke-test`, `python verify.py`, and release-package expectations covered by CI or an equivalent repeatable runner.

## Current Support Matrix

| Platform | Current status | Verification |
| --- | --- | --- |
| Windows | Supported source platform; Windows x64 portable zip and MSI release packages are built. | GitHub Actions `windows-latest` runs Python verification, Tkinter smoke, and project verification. Release workflow builds EXE/MSI on `windows-latest`. |
| Ubuntu Linux | Supported source platform. | GitHub Actions `ubuntu-latest` runs Python verification, Tkinter smoke, and project verification. |
| macOS | Supported source platform after CI verification; no native app bundle is built yet. | GitHub Actions `macos-latest` runs Python verification, Tkinter smoke, and project verification. |
| Other Linux distributions | Source-compatible target, not fully certified. | Not individually tested; Ubuntu is the Linux CI representative. |
| BSD | not marked fully supported. | Requires a BSD runner, dependency install validation, Tkinter availability check, and release/install documentation. |
| generic Unix | not marked fully supported beyond Linux/macOS source compatibility. | Requires a named OS runner and repeatable verification. |
| Solaris | not marked fully supported. | Requires a Solaris runner, supported Python/dependency toolchain, Tkinter availability check, and release/install documentation. |
| Android | Not supported as a native/mobile app. | Requires a separate mobile client or supported web-client deployment model, mobile CI, packaging, and API connectivity model. |
| iOS | Not supported as a native/mobile app. | Requires a separate mobile client or supported web-client deployment model, mobile CI, packaging, signing, and API connectivity model. |

## Promotion Gates

A platform can move to fully supported only when all of these are true:

- CI or an equivalent repeatable runner installs Python dependencies from a clean environment.
- `python app.py --smoke-test` passes on that platform.
- `python verify.py` passes on that platform.
- The React frontend build path is documented for that platform or explicitly declared shared.
- Release artifacts or source-install expectations are documented.
- Known platform-specific limitations are documented.

Android and iOS need additional gates because Tkinter desktop UI and a local Python process are not native mobile application models. Full mobile support requires a separate mobile packaging strategy, such as a mobile web client backed by a reachable server or a native wrapper with a supported backend deployment model.

## Machine Check

Run the normal support-claim check with:

```bash
python scripts/verify_platform_support.py
```

Run the strict full-platform certification gate with:

```bash
python scripts/verify_platform_support.py --require-full
```

The strict gate is expected to fail until BSD, generic Unix beyond Linux/macOS, Solaris, Android, iOS, and additional Linux distribution evidence exists. That failure is intentional: it prevents MarketSentinel from advertising 100% platform test coverage before the required runners, packaging, and mobile architecture exist.
