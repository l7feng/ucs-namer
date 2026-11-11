"""Minimal MCP (Model Context Protocol) server over stdio, no third-party deps.

Talks JSON-RPC 2.0 with newline-delimited messages, exposes the batch-naming
capability set as tools, and forwards every call to the running GUI over
loopback IPC (falling back to an in-process session when the GUI is closed).

`ucs_apply` defaults to a dry run: nothing is written unless the caller passes
confirm=true. See docs/adr/0004.
"""

from __future__ import annotations

import json
import sys

from . import __version__, ipc
from .cli import force_utf8_stdio

SERVER_NAME = "ucs-namer"
PROTOCOL_VERSION = "2024-11-05"
DEFAULT_LIMIT = 100

TOOLS: list[dict] = [
    {
        "name": "ucs_scan",
        "description": "扫描文件或文件夹，把找到的音频加入待处理清单。返回新增数量、重复跳过数量与每个根目录的统计。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "paths": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "文件或文件夹的绝对路径，可多个",
                }
            },
            "required": ["paths"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_list_rows",
        "description": "列出当前清单：原始文件名、中文名、UCS 英文名、启用状态。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "limit": {"type": "integer", "description": "最多返回几条，0 表示全部，默认 100"}
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_metadata",
        "description": "查看某个文件现有的元数据（BWF bext 与 iXML）。没有值的字段显示「无」。",
        "inputSchema": {
            "type": "object",
            "properties": {"row_id": {"type": "string", "description": "行 id"}},
            "required": ["row_id"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_diff",
        "description": "改名前后对比。给 row_id 只看一条；不给则预演全部。每条字段带 state：same 不变 / add 新增 / overwrite 覆盖 / rename 改名。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "row_id": {"type": "string", "description": "留空表示全量预演"},
                "limit": {"type": "integer", "description": "全量时最多返回几条，默认 100"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_set_row",
        "description": "修改一条素材的字段。keywords_zh 用逗号分隔；append=true 时追加而不是替换。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "row_id": {"type": "string"},
                "field": {
                    "type": "string",
                    "enum": ["cat_id", "fx_name", "label_override", "notes", "keywords_zh"],
                },
                "value": {"type": "string"},
                "append": {"type": "boolean", "description": "仅对 keywords_zh 有意义"},
            },
            "required": ["row_id", "field", "value"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_replace",
        "description": "在指定字段上批量查找替换。ids 留空表示全部行。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}, "description": "留空=全部"},
                "field": {
                    "type": "string",
                    "enum": ["cat_id", "fx_name", "label_override", "notes", "keywords_zh"],
                },
                "find": {"type": "string"},
                "replace": {"type": "string"},
                "case_sensitive": {"type": "boolean"},
            },
            "required": ["field", "find", "replace"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_batch_enabled",
        "description": "批量启用或跳过（跳过的不参与执行）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "ids": {"type": "array", "items": {"type": "string"}},
                "enabled": {"type": "boolean"},
            },
            "required": ["ids", "enabled"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_preview",
        "description": "全量预演：统计不变/新增/覆盖/改名各多少，逐条给出将改成的文件名与覆盖风险。不写入任何文件。",
        "inputSchema": {
            "type": "object",
            "properties": {"limit": {"type": "integer", "description": "默认 100"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_apply",
        "description": "写入磁盘（改名 + 写元数据）。默认只预演；必须显式传 confirm=true 才真正写。返回 journal_id 供撤销。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "confirm": {
                    "type": "boolean",
                    "description": "必须为 true 才写盘。建议先调 ucs_preview 给人看再确认",
                }
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_undo",
        "description": "撤销一批写盘，按字节恢复到执行前。不给 journal_id 则撤最新一批。",
        "inputSchema": {
            "type": "object",
            "properties": {"journal_id": {"type": "string", "description": "留空=最新一批"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_report",
        "description": "回看某批执行：给了 row_id 则看该文件的元数据前后快照，否则列出批次里的文件。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "journal_id": {"type": "string", "description": "留空=最新一批"},
                "row_id": {"type": "string"},
            },
            "additionalProperties": False,
        },
    },
    {
        "name": "ucs_folders",
        "description": "按父文件夹统计当前清单，便于按文件夹批量操作。",
        "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False},
    },
    {
        "name": "ucs_categories",
        "description": "查询 UCS 8.2.1 分类表，按中英文关键词模糊匹配。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "limit": {"type": "integer", "description": "默认 60"},
            },
            "additionalProperties": False,
        },
    },
]

TOOL_NAMES = {tool["name"] for tool in TOOLS}


def _trim(payload: dict, key: str, limit: int) -> dict:
    items = payload.get(key)
    if not isinstance(items, list):
        return payload
    payload["total"] = len(items)
    if limit and limit > 0 and len(items) > limit:
        payload[key] = items[:limit]
        payload["shown"] = limit
        payload["truncated"] = True
    else:
        payload["shown"] = len(items)
        payload["truncated"] = False
    return payload


def _limit(value) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return DEFAULT_LIMIT


def call_tool(name: str, args: dict, session: ipc.Session) -> dict:
    if name == "ucs_scan":
        return session.call("scan_now", paths=[str(item) for item in args.get("paths") or []])
    if name == "ucs_list_rows":
        payload = session.call("get_rows")
        return _trim(payload, "rows", _limit(args.get("limit", DEFAULT_LIMIT)))
    if name == "ucs_metadata":
        return session.call("metadata_view", row_id=str(args.get("row_id") or ""))
    if name == "ucs_diff":
        row_id = str(args.get("row_id") or "")
        if row_id:
            return session.call("preview_diff", row_id=row_id)
        payload = session.call("preview_all")
        return _trim(payload, "items", _limit(args.get("limit", DEFAULT_LIMIT)))
    if name == "ucs_set_row":
        mode = "append" if args.get("append") else "replace"
        return session.call(
            "batch_update",
            ids=[str(args.get("row_id") or "")],
            patch={str(args.get("field")): str(args.get("value") or "")},
            mode=mode,
        )
    if name == "ucs_replace":
        ids = [str(item) for item in args.get("ids") or []]
        if not ids:
            ids = [
                str(row.get("id"))
                for row in (session.call("get_rows").get("rows") or [])
            ]
        return session.call(
            "batch_replace",
            ids=ids,
            field=str(args.get("field") or ""),
            find=str(args.get("find") or ""),
            replace=str(args.get("replace") or ""),
            case_sensitive=bool(args.get("case_sensitive")),
        )
    if name == "ucs_batch_enabled":
        return session.call(
            "batch_enabled",
            ids=[str(item) for item in args.get("ids") or []],
            enabled=bool(args.get("enabled")),
        )
    if name == "ucs_preview":
        payload = session.call("preview_all")
        return _trim(payload, "items", _limit(args.get("limit", DEFAULT_LIMIT)))
    if name == "ucs_apply":
        return session.call("apply_now", confirm=bool(args.get("confirm")))
    if name == "ucs_undo":
        return session.call("undo_now", journal_id=str(args.get("journal_id") or ""))
    if name == "ucs_report":
        journal_id = str(args.get("journal_id") or "")
        row_id = str(args.get("row_id") or "")
        if row_id:
            return session.call("execution_diff", journal_id=journal_id, row_id=row_id)
        return session.call("execution_items", journal_id=journal_id)
    if name == "ucs_folders":
        return session.call("folder_groups")
    if name == "ucs_categories":
        return session.call(
            "search_categories",
            query=str(args.get("query") or ""),
            limit=_limit(args.get("limit", 60)) or 60,
        )
    return {"ok": False, "error": f"未知工具：{name}"}


def _result(request_id, payload) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "result": payload}


def _error(request_id, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}


def handle(request: dict, session: ipc.Session) -> dict | None:
    method = str(request.get("method") or "")
    request_id = request.get("id")
    is_notification = "id" not in request

    if method == "initialize":
        requested = str((request.get("params") or {}).get("protocolVersion") or "")
        return _result(
            request_id,
            {
                "protocolVersion": requested or PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": __version__},
            },
        )
    if method in ("notifications/initialized", "initialized", "notifications/cancelled"):
        return None
    if method == "ping":
        return _result(request_id, {})
    if method == "tools/list":
        return _result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        params = request.get("params") or {}
        name = str(params.get("name") or "")
        if name not in TOOL_NAMES:
            return _error(request_id, -32602, f"未知工具：{name}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            return _error(request_id, -32602, "arguments 必须是对象")
        try:
            payload = call_tool(name, args, session)
        except Exception as error:  # noqa: BLE001 - reported back to the client
            return _error(request_id, -32603, f"{type(error).__name__}: {error}")
        text = json.dumps(payload, ensure_ascii=False, indent=2)
        return _result(
            request_id,
            {
                "content": [{"type": "text", "text": text}],
                "isError": not bool(payload.get("ok")),
            },
        )
    if is_notification:
        return None
    return _error(request_id, -32601, f"不支持的方法：{method}")


def _session() -> ipc.Session:
    state = ipc.default_state_path().with_name("mcp-session.json")
    return ipc.Session(prefer_ipc=True, state_path=state)


def _write(payload: dict) -> None:
    """Always emit UTF-8 bytes so the wire format never depends on the code page."""
    text = json.dumps(payload, ensure_ascii=False) + "\n"
    buffer = getattr(sys.stdout, "buffer", None)
    if buffer is not None:
        buffer.write(text.encode("utf-8"))
        buffer.flush()
    else:
        sys.stdout.write(text)
        sys.stdout.flush()


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    session = _session()
    if not ipc.available() and not ipc.read_endpoint():
        print(
            "[ucs-namer-mcp] 界面未运行：本次是独立会话，清单不与界面共享。",
            file=sys.stderr,
            flush=True,
        )
    stdin = sys.stdin.buffer
    for raw in stdin:
        line = raw.decode("utf-8", "replace").strip()
        if not line:
            continue
        try:
            request = json.loads(line)
        except ValueError:
            _write(_error(None, -32700, "解析失败：不是合法 JSON"))
            continue
        if not isinstance(request, dict):
            _write(_error(None, -32600, "请求必须是 JSON 对象"))
            continue
        response = handle(request, session)
        if response is None:
            continue
        _write(response)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
