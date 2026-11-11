"""pywebview bridge: adds window-bound actions on top of the headless CoreApi.

All batch logic lives in `ucs_namer.api.CoreApi` so that the CLI and the MCP
server can reuse it; this module only keeps what genuinely needs a window:
native file dialogs, the CSV save dialog and the detached metadata window.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

from .. import config, metadata
from ..api import CoreApi, _error


def _assets_index() -> Path:
    return Path(__file__).resolve().parent / "assets" / "index.html"


def first_existing_dir(current: str = "", fallback: str = "") -> str:
    for candidate in (current, fallback):
        text = (candidate or "").strip().strip('"')
        if not text:
            continue
        path = Path(text)
        if path.is_dir():
            return str(path)
        if path.is_file():
            return str(path.parent)
        parent = path.parent
        while parent != parent.parent and not parent.is_dir():
            parent = parent.parent
        if parent.is_dir():
            return str(parent)
    return ""


class Bridge(CoreApi):
    """Exposed as window.pywebview.api.<method>."""

    def __init__(self, window_getter=None) -> None:
        super().__init__()
        self._window_getter = window_getter
        self._detached_window = None
        self._detached_row = ""

    # -- infrastructure -------------------------------------------------
    def _window(self):
        getter = self._window_getter
        if getter is not None:
            return getter()
        try:
            import webview
        except ImportError:
            return None
        windows = getattr(webview, "windows", [])
        return windows[0] if windows else None

    # -- native dialogs --------------------------------------------------
    def pick_folders(self) -> dict:
        """Pick several folders in one native dialog, then scan them all."""
        try:
            import webview
        except ImportError:
            return _error("未安装 pywebview，无法打开文件选择器")
        window = self._window()
        if window is None:
            return _error("窗口尚未就绪")
        initial = first_existing_dir(config.load().get("last_folder", ""))
        try:
            chosen = window.create_file_dialog(
                webview.FOLDER_DIALOG, directory=initial or None, allow_multiple=True
            )
        except TypeError:
            try:
                chosen = window.create_file_dialog(webview.FOLDER_DIALOG, allow_multiple=True)
            except TypeError:
                chosen = window.create_file_dialog(webview.FOLDER_DIALOG)
        except OSError as error:
            return _error(f"打开文件夹失败：{error}")
        if not chosen:
            return {"ok": True, "started": False, "added": 0, "rows": self.rows, "groups": []}
        paths = (
            [str(item) for item in chosen] if isinstance(chosen, (list, tuple)) else [str(chosen)]
        )
        config.save({"last_folder": paths[0]})
        return self.start_add_paths(paths)

    def pick_folder(self) -> dict:
        return self.pick_folders()

    def pick_files(self) -> dict:
        try:
            import webview
        except ImportError:
            return _error("未安装 pywebview，无法打开文件选择器")
        window = self._window()
        if window is None:
            return _error("窗口尚未就绪")
        initial = first_existing_dir(config.load().get("last_folder", ""))
        file_types = ("音频文件 (*.wav;*.mp3;*.flac;*.aif;*.aiff;*.ogg)", "所有文件 (*.*)")
        try:
            chosen = window.create_file_dialog(
                webview.OPEN_DIALOG,
                directory=initial or None,
                allow_multiple=True,
                file_types=file_types,
            )
        except TypeError:
            chosen = window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        except OSError as error:
            return _error(f"打开文件失败：{error}")
        if not chosen:
            return {"ok": True, "added": 0, "rows": self.rows}
        paths = [str(item) for item in chosen]
        config.save({"last_folder": str(Path(paths[0]).parent)})
        return self._merge_rows(paths)

    def export_csv(self) -> dict:
        if not self.rows:
            return _error("没有可导出的素材")
        window = self._window()
        if window is None:
            return _error("窗口尚未就绪")
        try:
            import webview

            chosen = window.create_file_dialog(
                webview.SAVE_DIALOG,
                save_filename="UCS命名清单.csv",
                file_types=("CSV (*.csv)",),
            )
        except Exception as error:  # noqa: BLE001
            return _error(f"打开保存对话框失败：{error}")
        if not chosen:
            return {"ok": True, "path": ""}
        path = chosen if isinstance(chosen, str) else chosen[0]
        if not path.lower().endswith(".csv"):
            path += ".csv"
        return self.export_to(path)

    # -- detached metadata window ----------------------------------------
    def detach_metadata(self, row_id: str = "") -> dict:
        row = self._find_row(row_id) or (self.rows[0] if self.rows else None)
        if row is None:
            return _error("请先添加素材")
        self._detached_row = str(row.get("id"))
        title = "元数据查看 · " + str(row.get("original") or "")
        try:
            import webview
        except ImportError:
            return _error("未安装 pywebview，无法打开独立窗口")
        win = self._detached_window
        if win is not None:
            try:
                win.set_title(title)
                win.show()
                win.evaluate_js("window.ucsMetaLoad && window.ucsMetaLoad();")
                return {"ok": True, "reused": True}
            except Exception:  # noqa: BLE001
                self._detached_window = None
        try:
            html = _assets_index().read_text(encoding="utf-8")
            html = html.replace("<head>", "<head><script>window.UCS_META_VIEW=true;</script>", 1)
            win = webview.create_window(
                title,
                html=html,
                js_api=self,
                width=940,
                height=720,
                min_size=(720, 520),
                background_color="#0b1017",
            )
        except Exception as error:  # noqa: BLE001
            return _error(f"独立窗口创建失败：{error}")
        if win is None:
            return _error("独立窗口创建被取消")
        self._detached_window = win

        def on_close():
            self._detached_window = None
            return True

        with contextlib.suppress(Exception):
            win.events.closing += on_close
        return {"ok": True, "reused": False}

    def meta_window_state(self, row_id: str = "") -> dict:
        key = str(row_id or self._detached_row or "")
        row = self._find_row(key) or (self.rows[0] if self.rows else None)
        if row is None:
            return {"ok": True, "empty": True, "rows": [], "index": 0, "total": 0}
        key = str(row.get("id"))
        self._detached_row = key
        ids = [str(item.get("id")) for item in self.rows]
        return {
            "ok": True,
            "empty": False,
            "row_id": key,
            "index": ids.index(key),
            "total": len(ids),
            "name": row.get("original", ""),
            "path": str(row.get("path", "")),
            "info": metadata.inspect_file(row.get("path", "")),
            "rows": [
                {"id": str(item.get("id")), "name": item.get("original", "")}
                for item in self.rows
            ],
        }

    def close_meta_window(self) -> dict:
        win = self._detached_window
        if win is None:
            return {"ok": True}
        try:
            win.hide()
            return {"ok": True}
        except Exception:  # noqa: BLE001
            with contextlib.suppress(Exception):
                win.destroy()
            self._detached_window = None
            return {"ok": True}
