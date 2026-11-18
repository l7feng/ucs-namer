"""Local settings and app-data locations.

The API key is stored in the current user's AppData only. It is never written
to the repository, logs or project records.
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

APP_NAME = "ucs-namer"

DEFAULTS: dict = {
    "base_url": "https://api.deepseek.com/v1",
    "model": "deepseek-chat",
    "api_key": "",
    "engine": "api",
    "mt_provider": "google",
    "batch_size": 6,
    "timeout": 120,
    "temperature": 0.2,
    "creator": "",
    "source": "",
    "write_metadata": True,
    "last_folder": "",
    "window": {"width": 1280, "height": 840},
}


def _root(env: str, fallback: Path) -> Path:
    value = os.environ.get(env)
    return Path(value) / APP_NAME if value else fallback


def app_dir() -> Path:
    return _root("APPDATA", Path.home() / "AppData" / "Roaming" / APP_NAME)


def local_dir() -> Path:
    return _root("LOCALAPPDATA", Path.home() / "AppData" / "Local" / APP_NAME)


def settings_path() -> Path:
    return app_dir() / "settings.json"


def journal_dir() -> Path:
    return local_dir() / "journal"


def log_dir() -> Path:
    return local_dir() / "logs"


def ensure_dirs() -> None:
    for path in (app_dir(), local_dir(), journal_dir(), log_dir()):
        path.mkdir(parents=True, exist_ok=True)


def _merge(base: dict, patch: dict) -> dict:
    result = dict(base)
    for key, value in patch.items():
        if key == "window" and isinstance(value, dict):
            window = dict(result.get("window") or {})
            window.update(value)
            result["window"] = window
        else:
            result[key] = value
    return result


def load() -> dict:
    ensure_dirs()
    path = settings_path()
    if not path.exists():
        return dict(DEFAULTS)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return dict(DEFAULTS)
    if not isinstance(raw, dict):
        return dict(DEFAULTS)
    return _merge(DEFAULTS, raw)


def save(patch: dict) -> dict:
    """Merge a patch into settings and write atomically."""
    ensure_dirs()
    current = load()
    updated = _merge(current, patch)
    path = settings_path()
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", delete=False, dir=path.parent, suffix=".tmp"
    ) as handle:
        json.dump(updated, handle, ensure_ascii=False, indent=2)
        handle.flush()
        os.fsync(handle.fileno())
        temp_name = handle.name
    os.replace(temp_name, path)
    return updated


def public_settings() -> dict:
    """Settings safe to send to the renderer (the key itself is never sent)."""
    current = load()
    return {
        "base_url": current.get("base_url", ""),
        "model": current.get("model", ""),
        "api_key_set": bool(str(current.get("api_key", "")).strip()),
        "engine": current.get("engine", "api"),
        "mt_provider": current.get("mt_provider", "google"),
        "batch_size": int(current.get("batch_size", 6) or 6),
        "timeout": int(current.get("timeout", 120) or 120),
        "temperature": float(current.get("temperature", 0.2) or 0.2),
        "creator": current.get("creator", ""),
        "source": current.get("source", ""),
        "write_metadata": bool(current.get("write_metadata", True)),
        "last_folder": current.get("last_folder", ""),
    }


def update_from_ui(payload: dict) -> dict:
    """Apply a UI payload without letting an empty key erase a saved key."""
    patch: dict = {}
    for key in ("base_url", "model", "creator", "source"):
        if key in payload:
            patch[key] = str(payload.get(key) or "").strip()
    if "batch_size" in payload:
        try:
            patch["batch_size"] = max(1, min(20, int(payload["batch_size"])))
        except (TypeError, ValueError):
            pass
    if "timeout" in payload:
        try:
            patch["timeout"] = max(15, min(600, int(payload["timeout"])))
        except (TypeError, ValueError):
            pass
    if "temperature" in payload:
        try:
            patch["temperature"] = max(0.0, min(2.0, float(payload["temperature"])))
        except (TypeError, ValueError):
            pass
    if "write_metadata" in payload:
        patch["write_metadata"] = bool(payload["write_metadata"])
    if "engine" in payload:
        value = str(payload.get("engine") or "").strip().lower()
        if value in ("api", "local", "mt"):
            patch["engine"] = value
    if "mt_provider" in payload:
        value = str(payload.get("mt_provider") or "").strip().lower()
        if value in ("google", "argos"):
            patch["mt_provider"] = value
    if payload.get("clear_key"):
        patch["api_key"] = ""
    elif str(payload.get("api_key") or "").strip():
        patch["api_key"] = str(payload["api_key"]).strip()
    return save(patch)


def mask_key(key: str) -> str:
    key = (key or "").strip()
    if len(key) <= 8:
        return "****" if key else ""
    return f"{key[:4]}…{key[-4:]}"
