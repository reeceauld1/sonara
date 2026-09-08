"""Local app configuration and credential storage under %APPDATA%/Sonara."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

APP_NAME = "Sonara"


def app_data_dir() -> Path:
    base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
    directory = Path(base) / APP_NAME
    directory.mkdir(parents=True, exist_ok=True)
    return directory


CONFIG_PATH = app_data_dir() / "config.json"
TOKEN_PATH = app_data_dir() / "token.json"
CLIENT_SECRETS_PATH = app_data_dir() / "client_secret.json"
MODELS_DIR = app_data_dir() / "models"


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(data: dict[str, Any]) -> None:
    CONFIG_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def update_config(**kwargs: Any) -> dict[str, Any]:
    data = load_config()
    data.update(kwargs)
    save_config(data)
    return data
