"""GUI entry point: pywebview window around the single-page UI."""

from __future__ import annotations

import logging
from pathlib import Path

from .. import instance, ipc

ASSETS = Path(__file__).resolve().parent / "assets"


def enable_dpi_awareness() -> None:
    try:
        import ctypes

        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except (ImportError, AttributeError, OSError):
        pass


def _log_path() -> Path:
    from ..config import log_dir

    return log_dir() / "ucs-namer.log"


def configure_logging() -> None:
    try:
        path = _log_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        logging.basicConfig(
            filename=str(path),
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s %(message)s",
            encoding="utf-8",
        )
    except OSError:
        logging.basicConfig(level=logging.INFO)


def run() -> int:
    enable_dpi_awareness()
    configure_logging()
    if instance.acquire():
        instance.focus_existing()
        return 0
    try:
        return _run_window()
    finally:
        instance.release()


def _run_window() -> int:
    try:
        import webview
    except ImportError:
        print('未安装 pywebview：请执行 pip install "pywebview>=5" 后重试。')
        return 2

    from .bridge import Bridge
    from .drop import install as install_drop

    api = Bridge()
    window = webview.create_window(
        instance.MAIN_TITLE,
        str(ASSETS / "index.html"),
        js_api=api,
        width=1360,
        height=880,
        min_size=(1080, 680),
        background_color="#0b1017",
    )
    api._window_getter = lambda: window
    server = ipc.Server(api).start()
    try:
        webview.start(install_drop, (window,), icon=str(ASSETS / "app.ico"))
    finally:
        server.stop()
    return 0
