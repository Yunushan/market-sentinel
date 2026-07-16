from __future__ import annotations

import argparse
import base64
import binascii
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def signtool_path() -> str:
    executable = shutil.which("signtool.exe") or shutil.which("signtool")
    if not executable:
        raise SystemExit("signtool was not found. Install the Windows SDK signing tools on the release runner.")
    return executable


def decode_certificate(encoded: str) -> bytes:
    try:
        return base64.b64decode(encoded, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise SystemExit("WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64 is not valid base64.") from exc


def sign_file(executable: str, certificate_path: Path, password: str, timestamp_url: str, target: Path) -> None:
    sign_command = [
        executable,
        "sign",
        "/fd",
        "SHA256",
        "/f",
        str(certificate_path),
        "/p",
        password,
        "/tr",
        timestamp_url,
        "/td",
        "SHA256",
        str(target),
    ]
    subprocess.run(sign_command, check=True)
    subprocess.run([executable, "verify", "/pa", "/all", str(target)], check=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sign and verify Windows MarketSentinel release files.")
    parser.add_argument("--path", action="append", required=True, type=Path, help="EXE or MSI file to sign.")
    parser.add_argument(
        "--timestamp-url",
        default=os.environ.get("WINDOWS_CODE_SIGNING_TIMESTAMP_URL", "https://timestamp.digicert.com"),
    )
    args = parser.parse_args()
    certificate = os.environ.get("WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64", "").strip()
    password = os.environ.get("WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD", "")
    if not certificate or not password:
        raise SystemExit(
            "WINDOWS_CODE_SIGNING_CERTIFICATE_BASE64 and WINDOWS_CODE_SIGNING_CERTIFICATE_PASSWORD are required."
        )
    targets = [path.resolve() for path in args.path]
    missing = [str(path) for path in targets if not path.is_file()]
    if missing:
        raise SystemExit("Signing targets do not exist: " + ", ".join(missing))

    executable = signtool_path()
    with tempfile.TemporaryDirectory(prefix="market-sentinel-sign-") as tmpdir:
        certificate_path = Path(tmpdir) / "release-signing.pfx"
        certificate_path.write_bytes(decode_certificate(certificate))
        for target in targets:
            sign_file(executable, certificate_path, password, str(args.timestamp_url), target)
            print(f"[ok] signed {target.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
