"""Windows OLE drag-and-drop that hands real absolute paths to the page.

WebView2 normally treats an external drop as navigation, so external drop is
disabled and the host control receives the OLE drop instead. Failure here only
removes the drag entry point; the file pickers keep working.
"""

from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)

_HANDLERS: dict[int, tuple] = {}


def install(window, timeout: float = 15.0) -> bool:
    try:
        if not window.events.shown.wait(timeout):
            log.warning("窗口未在 %.0f 秒内显示，跳过文件拖放接入", timeout)
            return False
        native = getattr(window, "native", None)
        control = getattr(native, "webview", None) if native is not None else None
        if native is None or control is None:
            log.warning("当前 GUI 后端没有可用的 WebView2 控件，跳过文件拖放接入")
            return False
        from System import Func, Type

        native.Invoke(Func[Type](lambda: _attach(control)))
        return True
    except Exception:  # pragma: no cover - only reachable with a real GUI
        log.exception("文件拖放接入失败")
        return False


def _attach(control) -> None:  # pragma: no cover - WinForms/WebView2 only
    from System.Windows.Forms import DataFormats, DragDropEffects

    copy_effect = DragDropEffects.Copy
    none_effect = getattr(DragDropEffects, "None")
    try:
        control.AllowExternalDrop = False
    except Exception:  # noqa: BLE001 - older WebView2 builds lack it
        log.warning("WebView2 不支持 AllowExternalDrop，外部文件可能仍由页面接管")
    control.AllowDrop = True

    def has_files(args) -> bool:
        try:
            return bool(args.Data and args.Data.GetDataPresent(DataFormats.FileDrop))
        except Exception:  # noqa: BLE001
            return False

    def on_drag_enter(sender, args) -> None:
        if has_files(args):
            args.Effect = copy_effect
            _script(control, "window.ucsDrag && window.ucsDrag(true);")
        else:
            args.Effect = none_effect

    def on_drag_over(sender, args) -> None:
        args.Effect = copy_effect if has_files(args) else none_effect

    def on_drag_leave(sender, args) -> None:
        _script(control, "window.ucsDrag && window.ucsDrag(false);")

    def on_drag_drop(sender, args) -> None:
        _script(control, "window.ucsDrag && window.ucsDrag(false);")
        if not has_files(args):
            args.Effect = none_effect
            return
        try:
            paths = [str(item) for item in args.Data.GetData(DataFormats.FileDrop)]
        except Exception:
            log.exception("读取拖入文件路径失败")
            args.Effect = none_effect
            return
        args.Effect = copy_effect
        if paths:
            _script(
                control,
                "window.ucsDrop && window.ucsDrop("
                + json.dumps(paths, ensure_ascii=False)
                + ");",
            )

    control.DragEnter += on_drag_enter
    control.DragOver += on_drag_over
    control.DragLeave += on_drag_leave
    control.DragDrop += on_drag_drop
    _HANDLERS[id(control)] = (on_drag_enter, on_drag_over, on_drag_leave, on_drag_drop)


def _script(control, code: str) -> None:  # pragma: no cover - WebView2 only
    try:
        core = getattr(control, "CoreWebView2", None)
        if core is not None:
            core.ExecuteScriptAsync(code)
    except Exception:
        log.debug("向页面发送拖放事件失败", exc_info=True)
