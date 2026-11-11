"""Read-only metadata inspection and before/after previews for the GUI.

Nothing in this module writes to an audio file. It exists so the interface can
show what a WAV already carries and what an execution would change, leaving the
decision about keeping existing metadata to the human.
"""

from __future__ import annotations

from pathlib import Path

from .audio import wav_meta

EMPTY = "无"

SW_TAGS = ["SW_UCS", "SW_CATEGORY", "SW_KEYWORDS", "SW_CREATOR", "SW_SOURCE"]

# (flat key, label shown in the UI). Order is the display order.
WRITTEN_FIELDS = [
    ("bext.description", "bext.description"),
    ("iXML.SW_UCS", "iXML SW_UCS"),
    ("iXML.SW_CATEGORY", "iXML SW_CATEGORY"),
    ("iXML.SW_KEYWORDS", "iXML SW_KEYWORDS"),
    ("iXML.SW_CREATOR", "iXML SW_CREATOR"),
    ("iXML.SW_SOURCE", "iXML SW_SOURCE"),
]

BEXT_FIELDS = [
    "Description",
    "Originator",
    "OriginatorReference",
    "OriginationDate",
    "OriginationTime",
    "TimeReference",
    "Version",
    "UMID",
    "LoudnessValue",
    "LoudnessRange",
    "MaxTruePeakLevel",
    "MaxMomentaryLoudness",
    "MaxShortTermLoudness",
    "CodingHistory",
]


def clean(value) -> str:
    text = "" if value is None else str(value)
    text = text.replace("\x00", "").strip()
    return text or EMPTY


def _is_wav(path: Path) -> bool:
    return path.suffix.lower() == ".wav"


def read(path) -> dict:
    """Read the file, returning a normalised dict or an error description."""
    path = Path(path)
    result = {
        "ok": True,
        "path": str(path),
        "name": path.name,
        "is_wav": _is_wav(path),
        "has_bext": False,
        "has_ixml": False,
        "bext": {},
        "ixml": {},
        "error": "",
    }
    if not path.exists():
        result["ok"] = False
        result["error"] = "文件不存在"
        return result
    if not _is_wav(path):
        result["error"] = "非 WAV 文件，仅改名，不写 BWF / iXML"
        return result
    try:
        meta = wav_meta.read_metadata(path)
    except wav_meta.WavMetaError as error:
        result["ok"] = False
        result["error"] = str(error)
        return result
    result["has_bext"] = bool(meta.get("has_bext"))
    result["has_ixml"] = bool(meta.get("has_ixml"))
    result["bext"] = dict(meta.get("bext") or {})
    result["ixml"] = dict(meta.get("ixml") or {})
    if meta.get("error"):
        result["error"] = str(meta["error"])
    return result


def inspect_file(path) -> dict:
    """Everything the file currently carries, in display order."""
    info = read(path)
    rows: list[dict] = []
    if info["is_wav"] and info["ok"]:
        bext = info["bext"]
        for field in BEXT_FIELDS:
            rows.append(
                {
                    "group": "BWF / bext",
                    "label": "bext." + field,
                    "value": clean(bext.get(field)),
                    "present": clean(bext.get(field)) != EMPTY,
                }
            )
        for tag in sorted(info["ixml"], key=str.casefold):
            rows.append(
                {
                    "group": "iXML",
                    "label": "iXML." + tag,
                    "value": clean(info["ixml"].get(tag)),
                    "present": True,
                }
            )
        if not info["has_ixml"]:
            rows.append({"group": "iXML", "label": "iXML", "value": EMPTY, "present": False})
    return {
        "ok": info["ok"],
        "path": info["path"],
        "name": info["name"],
        "is_wav": info["is_wav"],
        "has_bext": info["has_bext"],
        "has_ixml": info["has_ixml"],
        "error": info["error"],
        "rows": rows,
    }


def flat(path) -> dict[str, str]:
    """Flat snapshot of the fields an execution writes, for journals and diffs."""
    info = read(path)
    result: dict[str, str] = {}
    if not info["is_wav"] or not info["ok"]:
        return result
    result["bext.description"] = clean(info["bext"].get("Description"))
    for tag in SW_TAGS:
        result["iXML." + tag] = clean(info["ixml"].get(tag))
    return result


def metadata_values(row: dict, creator: str = "", source: str = "") -> dict[str, str]:
    """Single source of truth for the iXML SW_* fields an execution writes."""
    values = {
        "SW_UCS": str(row.get("english_metadata") or ""),
        "SW_CATEGORY": str(row.get("cat_id") or ""),
        "SW_KEYWORDS": f"{row.get('fx_name', '')}；{'，'.join(row.get('keywords_zh') or [])}".strip("；"),
    }
    if creator:
        values["SW_CREATOR"] = creator
    if source:
        values["SW_SOURCE"] = source
    return values


def planned(row: dict, creator: str = "", source: str = "") -> dict[str, str]:
    """Values an execution would write. Pure computation, never touches disk."""
    values = metadata_values(row, creator, source)
    result = {
        "bext.description": clean(row.get("english_metadata") or row.get("english_ucs")),
    }
    for tag in SW_TAGS:
        result["iXML." + tag] = clean(values.get(tag))
    return result


def _field(key: str, label: str, before: str, after: str, has_existing: bool) -> dict:
    before = before or EMPTY
    after = after or EMPTY
    if key == "filename" and before != after:
        state = "rename"
    elif before == after:
        state = "same"
    elif before == EMPTY:
        state = "add"
    else:
        state = "overwrite"
    return {
        "key": key,
        "label": label,
        "before": before,
        "after": after,
        "state": state,
        "has_existing": has_existing or before != EMPTY,
    }


def _unchanged_extras(info: dict) -> list[dict]:
    """Existing metadata that the executor does not touch."""
    extras: list[dict] = []
    for field in BEXT_FIELDS:
        if field == "Description":
            continue
        value = clean(info["bext"].get(field))
        if value != EMPTY:
            extras.append({"label": "bext." + field, "value": value})
    for tag in sorted(info["ixml"], key=str.casefold):
        if tag not in SW_TAGS:
            extras.append({"label": "iXML." + tag, "value": clean(info["ixml"].get(tag))})
    return extras


def diff_row(row: dict, creator: str = "", source: str = "") -> dict:
    """Execution preview: what the file has now vs what would be written."""
    info = read(row.get("path", ""))
    before = flat(row.get("path", "")) if info["is_wav"] else {}
    after = planned(row, creator, source)
    old_name = Path(str(row.get("path", ""))).name
    new_name = str(row.get("chinese_name") or old_name)
    fields = [_field("filename", "文件名", old_name, new_name, True)]
    for key, label in WRITTEN_FIELDS:
        fields.append(_field(key, label, before.get(key, EMPTY), after.get(key, EMPTY), False))
    return {
        "ok": info["ok"],
        "error": info["error"],
        "path": info["path"],
        "name": info["name"],
        "is_wav": info["is_wav"],
        "has_bext": info["has_bext"],
        "has_ixml": info["has_ixml"],
        "row_id": row.get("id", ""),
        "fields": fields,
        "extras": _unchanged_extras(info),
        "overwrite_count": sum(1 for item in fields if item["state"] == "overwrite"),
    }


def diff_from_snapshot(
    before: dict[str, str], after: dict[str, str], old_name: str, new_name: str
) -> dict:
    """Post-execution review using the snapshots stored in the journal."""
    fields = [_field("filename", "文件名", old_name, new_name, True)]
    for key, label in WRITTEN_FIELDS:
        fields.append(
            _field(key, label, before.get(key, EMPTY), after.get(key, EMPTY), False)
        )
    return {
        "ok": True,
        "error": "",
        "fields": fields,
        "extras": [],
        "overwrite_count": sum(1 for item in fields if item["state"] == "overwrite"),
    }
