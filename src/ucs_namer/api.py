"""Headless capability layer shared by the GUI bridge, the CLI and the MCP server.

Everything in here is pure data work: it only touches `self.rows`, settings and
the journal. Anything that needs a window (file dialogs, the detached metadata
window) stays in `gui/bridge.py`. This split is what makes CLI / MCP possible
without duplicating business logic -- see docs/adr/0004.
"""

from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from . import __version__, config, engine, execute, llm, metadata, naming, scan, ucs
from . import journal as journal_store
from .audio import wav_meta

DEFAULT_JOB_TIMEOUT = 1800.0


def _error(message: str, **extra) -> dict:
    return {"ok": False, "error": str(message), **extra}


def _replace_text(text: str, find: str, replace: str, case_sensitive: bool) -> tuple[str, int]:
    if case_sensitive:
        return text.replace(find, replace), text.count(find)
    pattern = re.compile(re.escape(find), re.IGNORECASE)
    return pattern.subn(lambda _match: replace, text)


class CoreApi:
    """Stateful batch-naming session without any UI dependency."""

    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._job: dict | None = None
        self._lock = threading.Lock()

    # -- job plumbing ----------------------------------------------------
    def _enabled_rows(self) -> list[dict]:
        return [row for row in self.rows if row.get("enabled", True)]

    def _job_snapshot(self) -> dict | None:
        with self._lock:
            return dict(self._job) if self._job is not None else None

    def _set_job(self, patch: dict) -> None:
        with self._lock:
            if self._job is not None:
                self._job.update(patch)

    def _progress(self, done: int, total: int, message: str) -> None:
        self._set_job({"done": done, "total": total, "message": message})

    def _start_job(self, kind: str, message: str, worker) -> dict:
        with self._lock:
            if self._job is not None and self._job.get("running"):
                return _error("已有任务正在运行，请等待完成")
            self._job = {
                "kind": kind,
                "running": True,
                "done": 0,
                "total": 0,
                "message": message,
                "error": "",
                "result": None,
                "started": time.time(),
            }
        thread = threading.Thread(target=self._run_job, args=(worker,), daemon=True)
        thread.start()
        return {"ok": True}

    def _run_job(self, worker) -> None:
        try:
            result = worker(self._progress)
            self._set_job({"running": False, "result": result, "message": "完成"})
        except Exception as error:  # noqa: BLE001 - surfaced to the UI
            self._set_job({"running": False, "error": str(error), "message": "失败"})

    def wait_job(self, timeout: float = DEFAULT_JOB_TIMEOUT) -> dict:
        """Block until the running job finishes; used by CLI and MCP callers."""
        try:
            limit = max(1.0, float(timeout))
        except (TypeError, ValueError):
            limit = DEFAULT_JOB_TIMEOUT
        deadline = time.monotonic() + limit
        while time.monotonic() < deadline:
            job = self._job_snapshot()
            if job is None:
                return _error("任务已结束或被清理")
            if not job.get("running"):
                error = job.get("error") or ""
                return {
                    "ok": not error,
                    "kind": job.get("kind", ""),
                    "error": error,
                    "message": job.get("message", ""),
                    "result": job.get("result"),
                }
            time.sleep(0.15)
        self._set_job({"running": False, "error": "任务超时", "message": "超时"})
        return _error("任务超时")

    def _call_sync(self, starter, timeout: float = DEFAULT_JOB_TIMEOUT) -> dict:
        started = starter()
        if not started.get("ok"):
            return started
        return self.wait_job(timeout)

    # -- bootstrap and rows ---------------------------------------------
    def bootstrap(self) -> dict:
        settings = config.public_settings()
        return {
            "ok": True,
            "version": __version__,
            "settings": settings,
            "category_count": len(ucs.CATEGORIES),
            "extensions": sorted(scan.SUPPORTED_EXTENSIONS),
        }

    def get_rows(self) -> dict:
        return {"ok": True, "rows": self.rows}

    def status(self) -> dict:
        settings = config.public_settings()
        return {
            "ok": True,
            "job": self._job_snapshot(),
            "total": len(self.rows),
            "enabled": len(self._enabled_rows()),
            "api_key_set": settings["api_key_set"],
        }

    def ack_job(self) -> dict:
        with self._lock:
            if self._job is not None and not self._job.get("running"):
                self._job = None
        return {"ok": True}

    def _merge_rows(self, paths: list[str]) -> dict:
        try:
            found, groups = scan.build_rows_grouped(paths)
        except Exception as error:  # noqa: BLE001
            return _error(f"扫描失败：{error}")
        existing = {os.path.normcase(str(Path(row["path"]))) for row in self.rows}
        added = 0
        for row in found:
            key = os.path.normcase(str(Path(row["path"])))
            if key in existing:
                continue
            existing.add(key)
            self.rows.append(row)
            added += 1
        return {
            "ok": True,
            "added": added,
            "duplicates": len(found) - added,
            "total": len(self.rows),
            "rows": self.rows,
            "groups": groups,
        }

    def add_paths(self, paths) -> dict:
        if not isinstance(paths, list) or not paths:
            return _error("没有收到文件路径")
        if self._job_snapshot() and self._job_snapshot().get("running"):
            return _error("任务运行中，暂不能添加素材")
        return self._merge_rows([str(item) for item in paths])

    def start_add_paths(self, paths) -> dict:
        """Scan several dropped roots off the UI thread with per-folder stats."""
        if not isinstance(paths, list) or not paths:
            return _error("没有收到文件路径")
        if self._job_snapshot() and self._job_snapshot().get("running"):
            return _error("任务运行中，暂不能添加素材")
        roots = [str(item) for item in paths]

        def worker(progress) -> dict:
            progress(0, len(roots), "正在扫描 " + str(len(roots)) + " 个位置")
            result = self._merge_rows(roots)
            if not result.get("ok"):
                raise RuntimeError(result.get("error") or "扫描失败")
            progress(len(roots), len(roots), "扫描完成")
            return result

        started = self._start_job("scan", "正在批量扫描", worker)
        if not started.get("ok"):
            return started
        return {"ok": True, "started": True}

    def scan_now(self, paths, timeout: float = DEFAULT_JOB_TIMEOUT) -> dict:
        """Synchronous batch scan for CLI / MCP callers."""
        if not isinstance(paths, list) or not paths:
            return _error("没有收到文件路径")
        job = self._call_sync(lambda: self.start_add_paths(paths), timeout)
        if not job.get("ok"):
            return _error(job.get("error") or "扫描失败")
        payload = job.get("result")
        if isinstance(payload, dict):
            return payload
        return {"ok": True, "result": payload}

    def clear_rows(self) -> dict:
        if self._job_snapshot() and self._job_snapshot().get("running"):
            return _error("任务运行中，暂不能清空")
        self.rows = []
        return {"ok": True, "rows": self.rows}

    def remove_row(self, row_id: str) -> dict:
        self.rows = [row for row in self.rows if row.get("id") != row_id]
        return {"ok": True, "rows": self.rows}

    def update_row(self, payload: dict) -> dict:
        row = next((item for item in self.rows if item.get("id") == payload.get("id")), None)
        if row is None:
            return _error("找不到该素材")
        for key in ("cat_id", "fx_name", "label_override", "notes"):
            if key in payload:
                row[key] = str(payload.get(key) or "")
        if "keywords_zh" in payload:
            row["keywords_zh"] = naming.split_keywords(payload.get("keywords_zh"))
        if "enabled" in payload:
            row["enabled"] = bool(payload["enabled"])
        self._recompose([row])
        return {"ok": True, "row": row}

    def preview_row(self, payload: dict) -> dict:
        row = dict(payload)
        settings = config.load()
        try:
            result = naming.compose(row, settings["creator"], settings["source"])
        except naming.NamingError as error:
            return {
                "ok": True,
                "error": str(error),
                "chinese_name": "",
                "english_ucs": "",
                "warnings": [],
            }
        return {"ok": True, "error": "", **result}

    # -- categories ------------------------------------------------------
    def all_categories(self) -> dict:
        return {"ok": True, "categories": ucs.all_categories()}

    def search_categories(self, query: str = "", limit: int = 60) -> dict:
        try:
            size = max(1, min(200, int(limit)))
        except (TypeError, ValueError):
            size = 60
        return {"ok": True, "categories": [item.to_dict() for item in ucs.search(query, size)]}

    # -- settings --------------------------------------------------------
    def save_settings(self, payload: dict) -> dict:
        try:
            saved = config.update_from_ui(payload or {})
        except OSError as error:
            return _error(f"保存设置失败：{error}")
        naming.compose_rows(self.rows, saved.get("creator", ""), saved.get("source", ""))
        return {"ok": True, "settings": config.public_settings()}

    def _test_settings(self, payload: dict | None) -> dict:
        current = config.load()
        merged = dict(current)
        for key in ("base_url", "model", "engine", "mt_provider"):
            value = str((payload or {}).get(key) or "").strip()
            if value:
                merged[key] = value
        key = str((payload or {}).get("api_key") or "").strip()
        if key:
            merged["api_key"] = key
        return merged

    def test_connection(self, payload: dict | None = None) -> dict:
        settings = self._test_settings(payload)
        engine_name = engine.current(settings)

        def worker(_progress) -> dict:
            return engine.test(settings)

        return self._start_job("test", f"正在自检 {engine.label(engine_name)}", worker)

    # -- translation, execution, undo ------------------------------------
    def start_translate(self) -> dict:
        if not self.rows:
            return _error("请先添加素材")
        if not self._enabled_rows():
            return _error("没有启用的素材")
        settings = config.load()
        engine_name = engine.current(settings)
        ready = engine.readiness(settings)
        if not ready["ok"]:
            return _error(ready["message"])

        def worker(progress) -> dict:
            settings = config.load()
            engine_name = engine.current(settings)
            targets = self._enabled_rows()
            # A new run must start clean: a row that failed last time would
            # otherwise be skipped by every engine and never recover.
            for row in targets:
                row["error"] = ""
                row["translated"] = False
            try:
                engine.translate_rows(targets, settings, progress)
            except llm.LLMError as error:
                return {"engine": engine_name, "translated": 0, "failed": [{"id": None, "error": str(error)}], "total": len(targets)}
            failed = [
                {"id": row.get("id"), "original": row.get("original"), "error": row.get("error")}
                for row in targets
                if row.get("error")
            ]
            naming.compose_rows(self.rows, settings["creator"], settings["source"])
            translated = sum(1 for row in targets if row.get("translated"))
            return {"engine": engine_name, "translated": translated, "failed": failed, "total": len(targets)}

        return self._start_job("translate", f"准备{engine.label(engine_name)}翻译", worker)

    def preflight(self) -> dict:
        if not self._enabled_rows():
            return _error("没有启用的素材")
        settings = config.load()
        rows = self._enabled_rows()
        naming.compose_rows(rows, settings["creator"], settings["source"])
        check = execute.preflight(
            rows, settings["creator"], settings["source"], settings["write_metadata"]
        )
        return {"ok": True, "check": check, "total": len(rows)}

    def start_execute(self) -> dict:
        if not self._enabled_rows():
            return _error("没有启用的素材")

        def worker(progress) -> dict:
            settings = config.load()
            rows = self._enabled_rows()
            progress(0, len(rows), "正在预检目标文件名")
            naming.compose_rows(rows, settings["creator"], settings["source"])
            result = execute.execute(
                rows, settings["creator"], settings["source"], settings["write_metadata"]
            )
            for item in result.get("results", []):
                item.pop("bext", None)
                item.pop("ixml", None)
            progress(len(rows), len(rows), "执行完成")
            return result

        return self._start_job("execute", "准备执行", worker)

    def undo(self, journal_id: str = "") -> dict:
        def worker(_progress) -> dict:
            result = execute.undo(journal_id)
            if result.get("ok"):
                by_id = {row.get("id"): row for row in self.rows}
                for item in result.get("results", []):
                    row = by_id.get(item.get("id"))
                    if row is None or item.get("status") != "ok":
                        continue
                    row["path"] = item.get("path", row["path"])
                    scan.refresh_row_path(row)
                    row["status"] = "pending"
            return result

        return self._start_job("undo", "正在撤销上一批", worker)

    def export_to(self, path) -> dict:
        if not self.rows:
            return _error("没有可导出的素材")
        if not str(path or "").strip():
            return _error("没有指定导出路径")
        try:
            return execute.export_csv(self.rows, str(path))
        except OSError as error:
            return _error(f"导出失败：{error}")

    # -- synchronous facades for CLI / MCP -------------------------------
    def preview_all(self) -> dict:
        """Dry run over every enabled row: what would change, without writing."""
        settings = config.load()
        targets = self._enabled_rows()
        if not targets:
            return _error("没有启用的素材")
        naming.compose_rows(targets, settings["creator"], settings["source"])
        items = []
        counts = {"same": 0, "add": 0, "overwrite": 0, "rename": 0}
        for row in targets:
            diff = metadata.diff_row(row, settings["creator"], settings["source"])
            for cell in diff.get("fields", []):
                state = cell.get("state")
                if state in counts:
                    counts[state] += 1
            items.append(
                {
                    "row_id": str(row.get("id")),
                    "name": row.get("original", ""),
                    "path": str(row.get("path", "")),
                    "chinese_name": row.get("chinese_name", ""),
                    "english_ucs": row.get("english_ucs", ""),
                    "overwrite_count": diff.get("overwrite_count", 0),
                    "diff": diff,
                }
            )
        check = execute.preflight(
            targets, settings["creator"], settings["source"], settings["write_metadata"]
        )
        return {
            "ok": True,
            "dry_run": True,
            "total": len(items),
            "counts": counts,
            "preflight": check,
            "items": items,
        }

    def apply_now(
        self,
        confirm: bool = False,
        yolo: bool = False,
        timeout: float = DEFAULT_JOB_TIMEOUT,
    ) -> dict:
        """Write to disk only when explicitly confirmed; otherwise等同于 preview."""
        if not (confirm or yolo):
            result = self.preview_all()
            if result.get("ok"):
                result["note"] = "预演模式：未写入任何文件。确认无误后带 confirm=true 再调用。"
            return result
        job = self._call_sync(self.start_execute, timeout)
        if not job.get("ok"):
            return {
                "ok": False,
                "dry_run": False,
                "error": job.get("error") or "执行失败",
                "result": job.get("result"),
            }
        payload = job.get("result") or {}
        results = payload.get("results") or []
        check = payload.get("preflight") or {}
        summary = {
            "ok": sum(1 for item in results if item.get("status") == "ok"),
            "error": sum(1 for item in results if item.get("status") != "ok"),
        }
        if not payload.get("ok"):
            blocked = [
                issue.get("message", "")
                for issue in (check.get("issues") or [])
                if issue.get("level") == "block"
            ]
            reason = payload.get("error") or (
                "预检未通过，未改动任何文件" if blocked else "所有文件都执行失败"
            )
            return {
                "ok": False,
                "dry_run": False,
                "error": reason,
                "counts": summary,
                "journal": payload.get("journal", ""),
                "preflight": check,
                "results": results,
            }
        return {
            "ok": True,
            "dry_run": False,
            "journal": payload.get("journal", ""),
            "counts": summary,
            "results": results,
            "preflight": check,
        }

    def undo_now(self, journal_id: str = "", timeout: float = DEFAULT_JOB_TIMEOUT) -> dict:
        job = self._call_sync(lambda: self.undo(journal_id), timeout)
        payload = job.get("result") or {}
        if not job.get("ok"):
            return {"ok": False, "error": job.get("error") or "撤销失败", "result": payload}
        return {
            "ok": bool(payload.get("ok")),
            "error": "" if payload.get("ok") else (payload.get("error") or "撤销未成功"),
            "journal": payload.get("journal", ""),
            "results": payload.get("results", []),
        }

    # -- metadata helpers ------------------------------------------------
    def read_metadata(self, row_id: str) -> dict:
        row = next((item for item in self.rows if item.get("id") == row_id), None)
        if row is None:
            return _error("找不到该素材")
        path = Path(str(row.get("path", "")))
        if path.suffix.lower() != ".wav":
            return _error("仅 WAV 支持 BWF/iXML 元数据")
        try:
            return {"ok": True, **wav_meta.read_metadata(path)}
        except wav_meta.WavMetaError as error:
            return _error(str(error))

    def _find_row(self, row_id) -> dict | None:
        key = str(row_id or "")
        return next((item for item in self.rows if str(item.get("id")) == key), None)

    def _recompose(self, rows: list[dict]) -> None:
        settings = config.load()
        naming.compose_rows(rows, settings["creator"], settings["source"])

    def metadata_view(self, row_id: str = "") -> dict:
        row = self._find_row(row_id)
        if row is None:
            return _error("找不到该素材")
        return {
            "ok": True,
            "row_id": str(row.get("id")),
            "name": row.get("original", ""),
            "path": str(row.get("path", "")),
            "info": metadata.inspect_file(row.get("path", "")),
        }

    def preview_diff(self, row_id: str = "") -> dict:
        row = self._find_row(row_id)
        if row is None:
            return _error("找不到该素材")
        settings = config.load()
        self._recompose([row])
        return {
            "ok": True,
            "row_id": str(row.get("id")),
            "name": row.get("original", ""),
            "path": str(row.get("path", "")),
            "enabled": bool(row.get("enabled", True)),
            "diff": metadata.diff_row(row, settings["creator"], settings["source"]),
        }

    def metadata_neighbor(self, row_id: str = "", step: int = 1) -> dict:
        if not self.rows:
            return _error("列表还是空的")
        ids = [str(item.get("id")) for item in self.rows]
        key = str(row_id or "")
        index = ids.index(key) if key in ids else 0
        try:
            step = int(step)
        except (TypeError, ValueError):
            step = 1
        target = max(0, min(len(ids) - 1, index + step))
        return {"ok": True, "row_id": ids[target], "index": target, "total": len(ids)}

    def _find_journal(self, journal_id: str = ""):
        key = str(journal_id or "").strip()
        for path in journal_store.list_journals():
            try:
                data = journal_store.load(path)
            except journal_store.JournalError:
                continue
            if key and data.get("id") == key:
                return path, data
            if not key and data.get("status") in ("done", "partial", "running") and data.get("items"):
                return path, data
        return None

    def execution_items(self, journal_id: str = "") -> dict:
        found = self._find_journal(journal_id)
        if found is None:
            return _error("暂无可回看的执行批次")
        _path, data = found
        items = [
            {
                "id": str(item.get("id") or ""),
                "original": item.get("original", ""),
                "new_name": item.get("new_name", ""),
                "status": item.get("status", ""),
                "has_snapshot": bool(item.get("meta_before") or item.get("meta_after")),
            }
            for item in data.get("items", [])
        ]
        return {
            "ok": True,
            "journal": data.get("id", ""),
            "created": data.get("created", ""),
            "items": items,
        }

    def execution_diff(self, journal_id: str = "", row_id: str = "") -> dict:
        found = self._find_journal(journal_id)
        if found is None:
            return _error("暂无可回看的执行批次")
        _path, data = found
        items = data.get("items", [])
        key = str(row_id or "")
        item = next((entry for entry in items if str(entry.get("id")) == key), None)
        if item is None and items:
            item = items[0]
        if item is None:
            return _error("该批次没有条目")
        before = dict(item.get("meta_before") or {})
        after = dict(item.get("meta_after") or {})
        old_name = str(item.get("original") or "")
        new_name = str(item.get("new_name") or Path(str(item.get("new_path") or "")).name)
        return {
            "ok": True,
            "journal": data.get("id", ""),
            "row_id": str(item.get("id") or ""),
            "status": item.get("status", ""),
            "has_snapshot": bool(before or after),
            "diff": metadata.diff_from_snapshot(before, after, old_name, new_name),
        }

    # -- batch editing ----------------------------------------------------
    def _rows_by_ids(self, ids) -> list[dict]:
        if not isinstance(ids, (list, tuple)):
            return []
        wanted = {str(item) for item in ids}
        return [row for row in self.rows if str(row.get("id")) in wanted]

    def _apply_patch(self, rows: list[dict], patch, mode: str) -> None:
        patch = patch or {}
        for row in rows:
            for key in ("cat_id", "fx_name", "label_override", "notes"):
                if key in patch:
                    row[key] = str(patch.get(key) or "")
            if "keywords_zh" in patch:
                incoming = naming.split_keywords(patch.get("keywords_zh"))
                if mode == "append":
                    incoming = naming.split_keywords(
                        list(row.get("keywords_zh") or []) + incoming
                    )
                row["keywords_zh"] = incoming
        self._recompose(rows)

    def batch_update(self, ids, patch, mode: str = "replace") -> dict:
        rows = self._rows_by_ids(ids)
        if not rows:
            return _error("没有选中的素材")
        self._apply_patch(rows, patch, mode)
        return {"ok": True, "changed": len(rows), "rows": self.rows}

    def batch_replace(self, ids, field, find, replace, case_sensitive=False) -> dict:
        rows = self._rows_by_ids(ids)
        if not rows:
            return _error("没有选中的素材")
        field = str(field or "")
        if field not in ("fx_name", "cat_id", "label_override", "notes", "keywords_zh"):
            return _error("不支持在该字段上查找替换")
        find = str(find or "")
        if not find:
            return _error("请填写查找内容")
        replacement = str(replace or "")
        sensitive = bool(case_sensitive)
        hits = 0
        for row in rows:
            if field == "keywords_zh":
                items = []
                for item in row.get("keywords_zh") or []:
                    new, count = _replace_text(str(item), find, replacement, sensitive)
                    hits += count
                    items.append(new)
                row["keywords_zh"] = naming.split_keywords(items)
            else:
                new, count = _replace_text(str(row.get(field) or ""), find, replacement, sensitive)
                hits += count
                row[field] = new
        self._recompose(rows)
        return {"ok": True, "changed": len(rows), "hits": hits, "rows": self.rows}

    def batch_enabled(self, ids, enabled) -> dict:
        rows = self._rows_by_ids(ids)
        for row in rows:
            row["enabled"] = bool(enabled)
        return {"ok": True, "changed": len(rows), "rows": self.rows}

    def batch_remove(self, ids) -> dict:
        if self._job_snapshot() and self._job_snapshot().get("running"):
            return _error("任务运行中，暂不能移除素材")
        rows = self._rows_by_ids(ids)
        wanted = {str(row.get("id")) for row in rows}
        self.rows = [row for row in self.rows if str(row.get("id")) not in wanted]
        return {"ok": True, "removed": len(wanted), "rows": self.rows}

    def folder_groups(self) -> dict:
        return {"ok": True, "groups": scan.folder_groups(self.rows)}

    def batch_by_folder(self, root, patch, mode: str = "replace") -> dict:
        root = str(root or "")
        rows = [
            row
            for row in self.rows
            if os.path.normcase(str(Path(str(row.get("path", ""))).parent)) == os.path.normcase(root)
        ]
        if not rows:
            return _error("该文件夹下没有素材")
        self._apply_patch(rows, patch, mode)
        return {"ok": True, "changed": len(rows), "rows": self.rows}
