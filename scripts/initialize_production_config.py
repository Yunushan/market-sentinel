"""Initialize the private production config before enabling worker timers."""

from __future__ import annotations

import json
import os
import stat
import sys
from pathlib import Path

from core.models import AppConfig
from core.storage import ConfigCommitError, ConfigConflictError, ConfigLoadError, load_config, save_config


PRODUCTION_CONFIG = Path("/var/lib/market-sentinel/config.json")
SERVICE_USER = "market-sentinel"


def _require_private_entry(info: os.stat_result, *, kind: str, uid: int, gid: int) -> None:
    expected_type = stat.S_ISDIR if kind == "state directory" else stat.S_ISREG
    expected_mode = 0o700 if kind == "state directory" else 0o600
    if not expected_type(info.st_mode):
        expected_name = "directory" if kind == "state directory" else "regular file"
        raise ValueError(f"{kind} must be a {expected_name}")
    if (info.st_uid, info.st_gid) != (uid, gid):
        raise ValueError(f"{kind} must be owned by the {SERVICE_USER} user and group")
    if stat.S_IMODE(info.st_mode) != expected_mode:
        raise ValueError(f"{kind} must have mode {expected_mode:04o}")


def initialize_config(path: Path, *, uid: int, gid: int) -> str:
    """Create safe defaults once, or validate an existing private configuration."""
    _require_private_entry(path.parent.lstat(), kind="state directory", uid=uid, gid=gid)
    try:
        existing = path.lstat()
    except FileNotFoundError:
        existing = None
    if existing is not None:
        _require_private_entry(existing, kind="configuration", uid=uid, gid=gid)
        load_config(path)
        return "existing"

    # save_config holds the configuration lock and rejects an intervening create.
    save_config(AppConfig(), path)
    _require_private_entry(path.lstat(), kind="configuration", uid=uid, gid=gid)
    load_config(path)
    return "created"


def main() -> int:
    if sys.platform != "linux":
        print("Production configuration initialization requires Linux", file=sys.stderr)
        return 1
    import pwd

    try:
        account = pwd.getpwnam(SERVICE_USER)
    except KeyError:
        print(f"Service account {SERVICE_USER} does not exist", file=sys.stderr)
        return 1
    if (os.geteuid(), os.getegid()) != (account.pw_uid, account.pw_gid):
        print(f"Run as the {SERVICE_USER} service user and group", file=sys.stderr)
        return 1
    try:
        status = initialize_config(PRODUCTION_CONFIG, uid=account.pw_uid, gid=account.pw_gid)
    except (OSError, ValueError, ConfigLoadError, ConfigConflictError, ConfigCommitError) as exc:
        print(f"Production configuration initialization failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps({"status": status, "path": str(PRODUCTION_CONFIG)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
