from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any, Dict

from .models import AppConfig


DEFAULT_CONFIG_PATH = Path(__file__).resolve().parent.parent / "data" / "config.json"


def load_config(path: Path = DEFAULT_CONFIG_PATH) -> AppConfig:
    if not path.exists():
        return AppConfig()
    try:
        data: Dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        return AppConfig.from_dict(data)
    except Exception:
        # If config is corrupted, don't crash; return defaults.
        return AppConfig()


def save_config(cfg: AppConfig, path: Path = DEFAULT_CONFIG_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    data = json.dumps(cfg.to_dict(), indent=2, sort_keys=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(data)
            tmp.write("\n")
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            tmp_path.unlink()
