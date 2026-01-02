from __future__ import annotations

import json
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
    data = cfg.to_dict()
    path.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
