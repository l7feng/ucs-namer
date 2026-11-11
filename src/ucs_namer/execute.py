"""Preflight, rename, metadata write, CSV export and undo."""

from __future__ import annotations

import csv
import json
import os
from datetime import date
from pathlib import Path

from . import journal as journal_store
from . import metadata, naming
from .audio import wav_meta


class ExecuteError(RuntimeError):
    pass


def _target(row: dict) -> Path:
    return Path(str(row["path"])).parent / str(row["chinese_name"])


def preflight(rows: list[dict], creator: str = "", source: str = "", write_metadata: bool = True) -> dict:
    """Check every target before touching any file. Conflicts stop the batch."""
    issues: list[dict] = []
    seen: dict[str, str] = {}
    for row in rows:
        naming.compose_rows([row], creator, source)
        if row.get("error"):
            issues.append({"id": row.get("id"), "level": "block", "message": row["error"]})
            continue
        target = _target(row)
        key = os.path.normcase(str(target.resolve()))
        if key in seen:
            issues.append(
                {
                    "id": row.get("id"),
                    "level": "block",
                    "message": f"目标文件名与「{seen[key]}」重复：{target.name}",
                }
            )
        seen[key] = str(row.get("original", ""))
        if len(str(target)) > 259:
            issues.append(
                {
                    "id": row.get("id"),
                    "level": "block",
                    "message": f"目标路径超过 259 字符，Windows 可能无法写入：{target}",
                }
            )
        if not Path(str(row["path"])).exists():
            issues.append({"id": row.get("id"), "level": "block", "message": "源文件不存在"})
            continue
        if target.exists() and os.path.normcase(str(target)) != os.path.normcase(str(row["path"])):
            issues.append(
                {"id": row.get("id"), "level": "block", "message": f"目标已存在，拒绝覆盖：{target.name}"}
            )
        if write_metadata and str(row.get("ext", "")).lower() == ".wav":
            try:
                wav_meta.read_metadata(Path(str(row["path"])))
            except wav_meta.WavMetaError as error:
                issues.append(
                    {
                        "id": row.get("id"),
                        "level": "block",
                        "message": f"WAV 元数据不可写：{error}；可取消元数据写入或跳过该文件",
                    }
                )
        elif write_metadata and str(row.get("ext", "")).lower() not in (".wav",):
            issues.append(
                {
                    "id": row.get("id"),
                    "level": "warn",
                    "message": f"{row.get('ext')} 不支持 BWF/iXML，仅改名",
                }
            )
    blocked = [issue for issue in issues if issue["level"] == "block"]
    return {"ok": not blocked, "issues": issues, "blocked": blocked, "can_execute": not blocked}


def execute(rows: list[dict], creator: str = "", source: str = "", write_metadata: bool = True) -> dict:
    check = preflight(rows, creator, source, write_metadata)
    if not check["ok"]:
        return {"ok": False, "error": "预检未通过，未改动任何文件", "preflight": check}

    journal = journal_store.Journal()
    results = []
    try:
        for row in rows:
            item = {
                "id": row.get("id"),
                "original": row.get("original", ""),
                "old_path": str(row["path"]),
                "new_path": str(_target(row)),
                "renamed": False,
                "metadata_written": False,
                "bext": None,
                "ixml": None,
                "meta_before": {},
                "meta_after": {},
                "warnings": list(row.get("warnings") or []),
            }
            source_path = Path(str(row["path"]))
            target_path = _target(row)
            item["meta_before"] = metadata.flat(source_path)
            original_chunks = None
            try:
                if write_metadata and str(row.get("ext", "")).lower() == ".wav":
                    original_chunks = wav_meta.patch_metadata(
                        source_path,
                        str(row.get("english_metadata") or ""),
                        metadata.metadata_values(row, creator, source),
                    )
                    item["metadata_written"] = True
                    item["bext"] = journal_store.encode(original_chunks.get("original_bext"))
                    item["ixml"] = journal_store.encode(original_chunks.get("original_ixml"))
                    item["warnings"].extend(original_chunks.get("warnings") or [])
                if os.path.normcase(str(target_path)) != os.path.normcase(str(source_path)):
                    if target_path.exists():
                        raise ExecuteError(f"目标已存在：{target_path.name}")
                    os.rename(source_path, target_path)
                    item["renamed"] = True
                item["status"] = "ok"
                item["new_name"] = target_path.name
                item["meta_after"] = metadata.flat(target_path if target_path.exists() else source_path)
                try:
                    journal.add(item)
                except OSError as log_error:
                    item["warnings"].append(f"执行日志写入失败，本批可能无法撤销：{log_error}")
                results.append(item)
                row["path"] = str(target_path)
                row["status"] = "done"
                row["original"] = target_path.name
            except Exception as error:  # noqa: BLE001 - report per file and continue
                if item["metadata_written"] and original_chunks is not None and source_path.exists():
                    try:
                        wav_meta.restore_chunks(
                            source_path,
                            original_chunks.get("original_bext"),
                            original_chunks.get("original_ixml"),
                        )
                        item["metadata_written"] = False
                    except Exception as restore_error:  # noqa: BLE001
                        item["warnings"].append(f"回滚元数据失败：{restore_error}")
                item["status"] = "error"
                item["error"] = str(error)
                item["meta_after"] = metadata.flat(source_path)
                results.append(item)
                row["status"] = "error"
                row["error"] = str(error)
    finally:
        try:
            if results:
                journal.finish("done" if all(item["status"] == "ok" for item in results) else "partial")
            else:
                journal.remove_if_empty()
        except OSError:
            pass
    return {
        "ok": any(item["status"] == "ok" for item in results),
        "results": results,
        "preflight": check,
        "journal": journal.id,
    }


def undo_last() -> dict:
    found = journal_store.latest_pending()
    if not found:
        return {"ok": False, "error": "没有可撤销的执行批次"}
    return _undo_found(found)


def undo(journal_id: str = "") -> dict:
    """Undo a specific batch by id, falling back to the latest pending batch."""
    key = str(journal_id or "").strip()
    if not key:
        return undo_last()
    for path in journal_store.list_journals():
        try:
            data = journal_store.load(path)
        except journal_store.JournalError:
            continue
        if str(data.get("id") or "") != key:
            continue
        if str(data.get("status") or "").startswith("undo"):
            return {"ok": False, "error": f"该批次已经撤销过：{key}", "journal": key}
        return _undo_found((path, data))
    return {"ok": False, "error": f"找不到执行批次：{key}", "journal": key}


def _undo_found(found) -> dict:
    path, data = found
    results = []
    for item in reversed(data.get("items", [])):
        old_path = Path(str(item.get("old_path", "")))
        new_path = Path(str(item.get("new_path", "")))
        current = new_path if new_path.exists() else old_path
        try:
            if item.get("renamed") and new_path != old_path and new_path.exists():
                if old_path.exists():
                    raise ExecuteError(f"原路径已存在，跳过：{old_path.name}")
                os.rename(new_path, old_path)
                current = old_path
            if item.get("metadata_written") and current.exists():
                wav_meta.restore_chunks(
                    current,
                    journal_store.decode(item.get("bext")),
                    journal_store.decode(item.get("ixml")),
                )
            results.append({"id": item.get("id"), "status": "ok", "path": str(current)})
        except Exception as error:  # noqa: BLE001
            results.append({"id": item.get("id"), "status": "error", "error": str(error)})
    status = "undone" if all(item["status"] == "ok" for item in results) else "undo-partial"
    try:
        data["status"] = status
        data["undone_at"] = date.today().isoformat()
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    except OSError:
        pass
    return {"ok": any(item["status"] == "ok" for item in results), "results": results, "journal": data.get("id")}


def export_csv(rows: list[dict], path: str | Path) -> dict:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            [
                "原文件名",
                "UCS英文名（元数据）",
                "中文UCS名（文件名）",
                "CatID",
                "分类",
                "关键词",
                "置信度",
                "备注",
            ]
        )
        for row in rows:
            category = (row.get("category") or {}).get("label", "")
            writer.writerow(
                [
                    row.get("original", ""),
                    row.get("english_ucs", ""),
                    row.get("chinese_name", ""),
                    row.get("cat_id", ""),
                    category,
                    "，".join(row.get("keywords_zh") or []),
                    row.get("confidence", ""),
                    row.get("notes", ""),
                ]
            )
    return {"ok": True, "path": str(target), "count": len(rows)}
