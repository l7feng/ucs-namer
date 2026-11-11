"""Single-instance guard and window focusing for the desktop app.

A second copy of the GUI would fight the first one over `ipc.json` (see
docs/adr/0004), so the later process brings the existing window to the front
and exits instead of starting up.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wt

MAIN_TITLE = "UCS 命名翻译器"
MUTEX_NAME = r"Local\ucs-namer-gui"
ERROR_ALREADY_EXISTS = 183
SW_SHOW = 5
SW_RESTORE = 9

_handle = None


def acquire() -> bool:
    """Create the mutex. Returns True when another instance already owns it."""
    global _handle
    if _handle is not None:
        return True
    try:
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return False
    try:
        handle = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    except OSError:
        return False
    if not handle:
        return False
    _handle = handle
    return kernel32.GetLastError() == ERROR_ALREADY_EXISTS


def release() -> None:
    global _handle
    handle, _handle = _handle, None
    if not handle:
        return
    try:
        ctypes.windll.kernel32.CloseHandle(handle)
    except (AttributeError, OSError):
        pass


def _hwnd_for(title: str) -> int:
    try:
        user32 = ctypes.windll.user32
    except AttributeError:
        return 0
    hwnd = user32.FindWindowW(None, title)
    if hwnd:
        return hwnd
    candidates: list[int] = []

    def collect(found: int, _param) -> bool:
        length = user32.GetWindowTextLengthW(found)
        if length <= 0:
            return True
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(found, buf, length + 1)
        if buf.value.startswith(title):
            candidates.append(found)
            return False
        return True

    proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)(collect)
    user32.EnumWindows(proc, 0)
    return candidates[0] if candidates else 0


def focus_existing(title: str = MAIN_TITLE) -> bool:
    """Restore and raise the running instance's window. Best effort."""
    try:
        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32
    except AttributeError:
        return False
    hwnd = _hwnd_for(title)
    if not hwnd:
        return False
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    user32.ShowWindow(hwnd, SW_SHOW)
    user32.BringWindowToTop(hwnd)

    foreground = user32.GetForegroundWindow()
    current = kernel32.GetCurrentThreadId()
    target_thread = user32.GetWindowThreadProcessId(hwnd, None)
    attached = 0
    if foreground and target_thread and target_thread != current:
        attached = user32.AttachThreadInput(current, target_thread, True)
    try:
        user32.SetForegroundWindow(hwnd)
        user32.SetActiveWindow(hwnd)
    finally:
        if attached:
            user32.AttachThreadInput(current, target_thread, False)
    return True
