"""Loopback IPC so the CLI and the MCP server can drive the running GUI.

The GUI process owns the only `rows` list and journal; everything else forwards.
Endpoint details (port + token) live in `%LOCALAPPDATA%\\ucs-namer\\ipc.json`.
See docs/adr/0004 for why the state was not duplicated.
"""

from __future__ import annotations

import contextlib
import hmac
import json
import os
import secrets
import socket
import socketserver
import threading
import time
from pathlib import Path

from . import config

REQUEST_LIMIT = 1 << 20
RESPONSE_LIMIT = 1 << 28
TOKEN_BYTES = 24
CONNECT_TIMEOUT = 5.0
DEFAULT_TIMEOUT = 1800.0

EXPOSED = frozenset(
    {
        "bootstrap",
        "get_rows",
        "status",
        "ack_job",
        "add_paths",
        "start_add_paths",
        "scan_now",
        "clear_rows",
        "remove_row",
        "update_row",
        "preview_row",
        "all_categories",
        "search_categories",
        "save_settings",
        "test_connection",
        "start_translate",
        "preflight",
        "start_execute",
        "undo",
        "undo_now",
        "export_to",
        "preview_all",
        "apply_now",
        "read_metadata",
        "metadata_view",
        "preview_diff",
        "metadata_neighbor",
        "execution_items",
        "execution_diff",
        "batch_update",
        "batch_replace",
        "batch_enabled",
        "batch_remove",
        "folder_groups",
        "batch_by_folder",
    }
)


def endpoint_path() -> Path:
    return config.local_dir() / "ipc.json"


def _pid_alive(pid: int) -> bool:
    """True when the process still exists; non-Windows returns True (no claim either way)."""
    if pid <= 0:
        return False
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
    except (AttributeError, ImportError):
        return True
    handle = kernel32.OpenProcess(0x1000, False, pid)
    if not handle:
        return False
    try:
        code = ctypes.c_ulong()
        if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
            return False
        return code.value == 259
    finally:
        kernel32.CloseHandle(handle)


def read_endpoint() -> dict | None:
    path = endpoint_path()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not data.get("port"):
        return None
    pid = data.get("pid")
    if isinstance(pid, int) and pid != os.getpid() and not _pid_alive(pid):
        with contextlib.suppress(OSError):
            path.unlink()
        return None
    return data


# -- server ---------------------------------------------------------------
class _Handler(socketserver.StreamRequestHandler):
    timeout = 300

    def handle(self) -> None:
        try:
            raw = self.rfile.readline(REQUEST_LIMIT + 1)
        except OSError:
            return
        if not raw:
            return
        response = self.server.dispatch(raw)  # type: ignore[attr-defined]
        try:
            self.wfile.write(_dump(response))
        except OSError:
            pass


def _dump(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"


class _TcpServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True

    def __init__(self, address, api, token: str) -> None:
        self.api = api
        self.token = token
        super().__init__(address, _Handler)

    def dispatch(self, raw: bytes) -> dict:
        if len(raw) > REQUEST_LIMIT:
            return {"ok": False, "error": "请求过大"}
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, ValueError):
            return {"ok": False, "error": "请求不是合法 JSON"}
        if not isinstance(payload, dict):
            return {"ok": False, "error": "请求必须是 JSON 对象"}
        if not hmac.compare_digest(str(payload.get("token") or ""), self.token):
            return {"ok": False, "error": "token 校验失败"}
        method = str(payload.get("method") or "")
        if method not in EXPOSED:
            return {"ok": False, "error": f"不允许调用的方法：{method or '(空)'}"}
        params = payload.get("params") or {}
        if not isinstance(params, dict):
            return {"ok": False, "error": "params 必须是 JSON 对象"}
        target = getattr(self.api, method, None)
        if not callable(target):
            return {"ok": False, "error": f"方法不存在：{method}"}
        try:
            result = target(**params)
        except TypeError as error:
            return {"ok": False, "error": f"参数不匹配：{error}"}
        except Exception as error:  # noqa: BLE001 - reported to the caller
            return {"ok": False, "error": f"{type(error).__name__}: {error}"}
        if isinstance(result, dict):
            return result
        return {"ok": True, "result": result}


class Server:
    """Runs inside the GUI process and exposes CoreApi over loopback."""

    def __init__(self, api, host: str = "127.0.0.1", port: int = 0) -> None:
        self.token = secrets.token_hex(TOKEN_BYTES)
        self._impl = _TcpServer((host, port), api, self.token)
        self.host = self._impl.server_address[0]
        self.port = int(self._impl.server_address[1])
        self._thread: threading.Thread | None = None

    def start(self) -> Server:
        self._thread = threading.Thread(
            target=self._impl.serve_forever, name="ucs-ipc", daemon=True
        )
        self._thread.start()
        self._write_endpoint()
        return self

    def _write_endpoint(self) -> None:
        config.ensure_dirs()
        payload = {
            "pid": os.getpid(),
            "host": self.host,
            "port": self.port,
            "token": self.token,
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        path = endpoint_path()
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp, path)

    def stop(self) -> None:
        with contextlib.suppress(Exception):
            self._impl.shutdown()
        with contextlib.suppress(Exception):
            self._impl.server_close()
        self._drop_endpoint()

    def _drop_endpoint(self) -> None:
        path = endpoint_path()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if str(data.get("token") or "") == self.token:
            try:
                path.unlink()
            except OSError:
                pass


# -- client ---------------------------------------------------------------
def available(timeout: float = 0.3) -> bool:
    endpoint = read_endpoint()
    if not endpoint:
        return False
    try:
        with socket.create_connection(
            (str(endpoint.get("host") or "127.0.0.1"), int(endpoint["port"])), timeout=timeout
        ):
            return True
    except (OSError, KeyError, TypeError, ValueError):
        return False


def call(method: str, timeout: float = DEFAULT_TIMEOUT, **params) -> dict:
    endpoint = read_endpoint()
    if not endpoint:
        return {"ok": False, "error": "GUI 未运行（找不到 ipc.json）", "standalone": True}
    payload = {"token": endpoint.get("token"), "method": method, "params": params}
    try:
        with socket.create_connection(
            (str(endpoint.get("host") or "127.0.0.1"), int(endpoint["port"])),
            timeout=CONNECT_TIMEOUT,
        ) as sock:
            sock.settimeout(max(CONNECT_TIMEOUT, float(timeout)))
            sock.sendall(_dump(payload))
            buffer = b""
            while not buffer.endswith(b"\n"):
                chunk = sock.recv(65536)
                if not chunk:
                    break
                buffer += chunk
                if len(buffer) > RESPONSE_LIMIT:
                    return {"ok": False, "error": "响应过大，请缩小范围", "standalone": False}
    except (OSError, KeyError, TypeError, ValueError) as error:
        return {"ok": False, "error": f"连接 GUI 失败：{error}", "standalone": True}
    if not buffer.strip():
        return {"ok": False, "error": "GUI 没有返回内容", "standalone": False}
    try:
        data = json.loads(buffer.decode("utf-8").strip())
    except (UnicodeDecodeError, ValueError):
        return {"ok": False, "error": "GUI 返回的不是合法 JSON", "standalone": False}
    if not isinstance(data, dict):
        return {"ok": True, "result": data, "standalone": False}
    data.setdefault("standalone", False)
    return data


class Session:
    """Forward calls to the running GUI, or fall back to an in-process CoreApi.

    In standalone mode an optional state file keeps the row list alive between
    short-lived CLI invocations; the GUI path never touches it.
    """

    PERSIST = frozenset(
        {
            "add_paths",
            "scan_now",
            "clear_rows",
            "remove_row",
            "update_row",
            "batch_update",
            "batch_replace",
            "batch_enabled",
            "batch_remove",
            "batch_by_folder",
            "apply_now",
            "undo_now",
            "undo",
        }
    )

    def __init__(
        self,
        prefer_ipc: bool = True,
        timeout: float = DEFAULT_TIMEOUT,
        state_path: Path | None = None,
    ) -> None:
        self.prefer_ipc = prefer_ipc
        self.timeout = timeout
        self.state_path = state_path
        self._local = None
        self._checked = False

    @property
    def standalone(self) -> bool:
        return self._local is not None

    def _ensure(self) -> None:
        if self._local is not None or self._checked:
            return
        self._checked = True
        if self.prefer_ipc and available():
            return
        from .api import CoreApi

        self._local = CoreApi()
        if self.state_path is not None:
            _load_state(self._local, self.state_path)

    def _persist(self, method: str) -> None:
        if self.state_path is None or method not in self.PERSIST:
            return
        if self._local is not None:
            _save_state(self._local, self.state_path)

    def call(self, method: str, **params) -> dict:
        self._ensure()
        if self._local is not None:
            target = getattr(self._local, method)
            result = target(**params)
            self._persist(method)
            if isinstance(result, dict):
                result.setdefault("standalone", True)
                return result
            return {"ok": True, "result": result, "standalone": True}
        response = call(method, timeout=self.timeout, **params)
        if response.get("standalone"):
            from .api import CoreApi

            self._local = CoreApi()
            if self.state_path is not None:
                _load_state(self._local, self.state_path)
            target = getattr(self._local, method)
            result = target(**params)
            self._persist(method)
            if isinstance(result, dict):
                result["standalone"] = True
                result["fallback_reason"] = response.get("error", "")
                return result
            return {"ok": True, "result": result, "standalone": True}
        return response


def default_state_path() -> Path:
    return config.local_dir() / "cli-session.json"


def _load_state(api, path: Path) -> None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return
    rows = data.get("rows") if isinstance(data, dict) else None
    if isinstance(rows, list):
        api.rows = [row for row in rows if isinstance(row, dict)]


def _save_state(api, path: Path) -> None:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_name(path.name + ".tmp")
        temp.write_text(
            json.dumps({"rows": api.rows}, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        os.replace(temp, path)
    except (OSError, TypeError, ValueError):
        pass
