# Platform Support

MarketSentinel does not claim full support for a platform until that platform has install instructions, dependency installation, `python app.py --smoke-test`, `python verify.py`, and release-package expectations covered by CI or an equivalent repeatable runner.

## Current Support Matrix

| Platform | Current status | Verification |
| --- | --- | --- |
| Windows | Supported source platform; Windows x64 portable zip and MSI release packages are built. | GitHub Actions `windows-2025-vs2026` runs Python verification, Tkinter smoke, and project verification. Release workflow builds EXE/MSI on `windows-2025-vs2026`. |
| Windows 11 | Supported source compatibility check on hosted Windows ARM. | GitHub Actions `windows-11-arm` runs Python 3.12 dependency installation, Tkinter smoke, and `python verify.py`. |
| Windows 10 | Self-hosted compatibility target, not enabled by default. | GitHub does not provide a standard hosted Windows 10 runner. CI includes an opt-in `Windows 10 self-hosted / Python 3.12` job gated by repository variable `ENABLE_WINDOWS_10_SELF_HOSTED=true` and requiring a self-hosted runner labelled `windows-10`. |
| Ubuntu Linux | Supported source platform. | GitHub Actions `ubuntu-latest` runs Python verification, Tkinter smoke, and project verification. |
| Red Hat Enterprise Linux / UBI | RHEL-family source compatibility is smoke checked, but not fully certified as a desktop platform yet. | GitHub Actions runs dependency installation, import checks, compile checks, metadata checks, fixture checks, and CI/CD workflow checks inside `registry.access.redhat.com/ubi8/python-312:latest`, `registry.access.redhat.com/ubi9/python-312:latest`, and `registry.access.redhat.com/ubi10/python-312-minimal:latest`. RHEL 7 era ABI compatibility is checked with `quay.io/pypa/manylinux2014_x86_64:latest`. Full `python app.py --smoke-test` and `python verify.py` still require RHEL runners with Tkinter available before these are promoted to full support. |
| Rocky Linux | Rocky Linux source compatibility is smoke checked, but not fully certified as a desktop platform yet. | GitHub Actions runs dependency installation and the Enterprise Linux smoke suite inside `rockylinux/rockylinux:8`, `rockylinux/rockylinux:9`, and `rockylinux/rockylinux:10`. Full `python app.py --smoke-test` and `python verify.py` still require Rocky runners with Tkinter available before these are promoted to full support. |
| macOS | Supported source platform after CI verification; no native app bundle is built yet. | GitHub Actions `macos-14`, `macos-15`, and `macos-26` run Python verification, Tkinter smoke, and project verification. |
| Other Linux distributions | Source-compatible target, not fully certified. | Ubuntu is the full Linux CI representative; RHEL-family and Rocky compatibility are covered by container smoke checks. Additional distributions still need named repeatable checks before being advertised as fully supported. |
| BSD | not marked fully supported. | Requires a BSD runner, dependency install validation, Tkinter availability check, and release/install documentation. |
| generic Unix | not marked fully supported beyond Linux/macOS source compatibility. | Requires a named OS runner and repeatable verification. |
| Solaris | not marked fully supported. | Requires a Solaris runner, supported Python/dependency toolchain, Tkinter availability check, and release/install documentation. |
| Android | Native/mobile app is not supported yet; mobile web compatibility is smoke checked. | GitHub Actions runs the built React UI through mobile web profiles for Android 14/API 34, Android 15/API 35, and Android 16/API 36. Native Android support still requires a separate mobile package, emulator/device CI, install smoke, and API connectivity model. |
| iOS | Native/mobile app is not supported yet; mobile web compatibility is smoke checked. | GitHub Actions runs the built React UI through mobile web profiles for iOS 15, iOS 16, iOS 18, and iOS 26. Native iOS support still requires a separate iOS package, simulator/device CI with available runtimes, signing, install smoke, and API connectivity model. |

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

The strict gate is expected to fail until Windows 10, RHEL/Rocky desktop runners with Tkinter, BSD, generic Unix beyond Linux/macOS, Solaris, native Android, native iOS, and additional Linux distribution evidence exists. That failure is intentional: it prevents MarketSentinel from advertising 100% platform test coverage before the required runners, packaging, and mobile architecture exist.
