"""Scan audio files and build preview rows."""

from __future__ import annotations

import os
import re
import uuid
from pathlib import Path

from . import ucs

SUPPORTED_EXTENSIONS = {".wav", ".mp3", ".flac", ".aif", ".aiff", ".ogg"}
_BRACKET_PREFIX = re.compile(r"^【[^】]*】")


def supported(path: Path) -> bool:
    return path.suffix.lower() in SUPPORTED_EXTENSIONS


def _expand(paths: list[str]) -> list[Path]:
    result: list[Path] = []
    seen: set[str] = set()

    def add(path: Path) -> None:
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            return
        seen.add(key)
        result.append(path)

    for raw in paths:
        text = str(raw or "").strip().strip('"')
        if not text:
            continue
        path = Path(text)
        if path.is_file() and supported(path):
            add(path)
            continue
        if path.is_dir():
            for root, dirs, files in os.walk(path):
                dirs[:] = [name for name in dirs if not name.startswith(".")]
                for name in files:
                    candidate = Path(root) / name
                    if supported(candidate):
                        add(candidate)
    result.sort(key=lambda item: str(item).casefold())
    return result


def _working_name(name: str) -> str:
    return _BRACKET_PREFIX.sub("", name).strip()


def make_row(path: Path) -> dict:
    name = path.name
    stem = path.stem
    ext = path.suffix
    working = _working_name(name)
    existing = ucs.category_prefix(working)
    existing_ucs = ""
    if existing is not None:
        existing_ucs = Path(working).stem
    try:
        size = path.stat().st_size
    except OSError:
        size = 0
    return {
        "id": uuid.uuid4().hex[:12],
        "path": str(path),
        "original": name,
        "stem": stem,
        "ext": ext,
        "folder": path.parent.name,
        "size": size,
        "is_wav": ext.lower() == ".wav",
        "existing_ucs": existing_ucs,
        "cat_id": existing.cat_id if existing else "",
        "fx_name": "",
        "keywords_zh": [],
        "confidence": 0.0,
        "notes": "",
        "translated": False,
        "enabled": True,
        "status": "pending",
        "error": "",
        "warnings": [],
    }


def build_rows(paths: list[str]) -> list[dict]:
    return [make_row(path) for path in _expand(paths)]


def build_rows_grouped(paths: list[str]) -> tuple[list[dict], list[dict]]:
    """Scan several roots at once and report how many files each one supplied.

    Returns (rows, groups); groups describe each dropped/selected root so the UI
    can show a per-folder breakdown for a batch import.
    """
    collected: list[tuple[Path, str]] = []
    groups: list[dict] = []
    for raw in paths:
        text = str(raw or "").strip().strip('"')
        if not text:
            continue
        path = Path(text)
        if path.is_file() and supported(path):
            groups.append(
                {"root": str(path), "label": path.name, "is_dir": False, "found": 1}
            )
            collected.append((path, path.parent.name or str(path.parent)))
            continue
        if not path.is_dir():
            continue
        found = 0
        for root, dirs, files in os.walk(path):
            dirs[:] = [name for name in dirs if not name.startswith(".")]
            for name in files:
                candidate = Path(root) / name
                if supported(candidate):
                    collected.append((candidate, path.name or str(path)))
                    found += 1
        groups.append(
            {"root": str(path), "label": path.name or str(path), "is_dir": True, "found": found}
        )
    rows: list[dict] = []
    seen: set[str] = set()
    for path, _label in collected:
        key = os.path.normcase(str(path.resolve()))
        if key in seen:
            continue
        seen.add(key)
        rows.append(make_row(path))
    rows.sort(key=lambda item: str(item["path"]).casefold())
    return rows, groups


def folder_groups(rows: list[dict]) -> list[dict]:
    """Group current rows by their parent directory for batch operations."""
    result: dict[str, dict] = {}
    for row in rows:
        parent = str(Path(str(row.get("path", ""))).parent)
        entry = result.setdefault(
            parent, {"root": parent, "label": Path(parent).name or parent, "count": 0}
        )
        entry["count"] += 1
    return sorted(result.values(), key=lambda item: item["label"].casefold())


def refresh_row_path(row: dict) -> dict:
    """Update display fields after a rename (used by the bridge)."""
    path = Path(str(row.get("path", "")))
    row["original"] = path.name
    row["stem"] = path.stem
    row["ext"] = path.suffix
    row["folder"] = path.parent.name
    return row
