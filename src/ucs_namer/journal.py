"""Execution journal with enough information to undo renames and metadata."""

from __future__ import annotations

import base64
import json
import os
import tempfile
from datetime import datetime
from pathlib import Path

from .config import ensure_dirs, journal_dir

VERSION = 1


class JournalError(RuntimeError):
    pass


def _now_id() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S-%f")


class Journal:
    def __init__(self) -> None:
        ensure_dirs()
        self.id = _now_id()
        self.path = journal_dir() / f"{self.id}.json"
        self.data = {
            "version": VERSION,
            "id": self.id,
            "created": datetime.now().isoformat(timespec="seconds"),
            "status": "running",
            "items": [],
        }
        self._written = False

    def add(self, item: dict) -> None:
        self.data["items"].append(item)
        self.save()

    def save(self) -> None:
        ensure_dirs()
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", delete=False, dir=self.path.parent, suffix=".tmp"
        ) as handle:
            json.dump(self.data, handle, ensure_ascii=False, indent=2)
            handle.flush()
            os.fsync(handle.fileno())
            temp_name = handle.name
        os.replace(temp_name, self.path)
        self._written = True

    def finish(self, status: str = "done") -> None:
        self.data["status"] = status
        self.save()

    def remove_if_empty(self) -> None:
        if self._written and not self.data["items"]:
            try:
                self.path.unlink()
            except OSError:
                pass


def encode(raw: bytes | None) -> str | None:
    if raw is None:
        return None
    return base64.b64encode(raw).decode("ascii")


def decode(value: str | None) -> bytes | None:
    if value is None:
        return None
    return base64.b64decode(value.encode("ascii"))


def list_journals() -> list[Path]:
    ensure_dirs()
    return sorted(journal_dir().glob("*.json"), key=lambda path: path.name, reverse=True)


def load(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as error:
        raise JournalError(f"无法读取执行日志：{error}") from error


def latest_pending() -> tuple[Path, dict] | None:
    for path in list_journals():
        try:
            data = load(path)
        except JournalError:
            continue
        if data.get("status") in ("done", "partial", "running") and data.get("items"):
            return path, data
    return None
