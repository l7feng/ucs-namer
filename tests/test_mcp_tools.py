"""MCP protocol surface: handshake, tool registry and the dry-run default."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from tests.conftest import make_wav
from ucs_namer import ipc, mcp_server


def _session() -> ipc.Session:
    return ipc.Session(prefer_ipc=False)


def _call(session, name, arguments=None, request_id=1):
    return mcp_server.handle(
        {
            "jsonrpc": "2.0",
            "id": request_id,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments or {}},
        },
        session,
    )


def _payload(response) -> dict:
    return json.loads(response["result"]["content"][0]["text"])


def test_initialize_echoes_protocol_version():
    response = mcp_server.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "2025-06-18"},
        },
        _session(),
    )
    result = response["result"]
    assert result["protocolVersion"] == "2025-06-18"
    assert result["capabilities"]["tools"]["listChanged"] is False
    assert result["serverInfo"]["name"] == "ucs-namer"


def test_tools_list_is_stable():
    response = mcp_server.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, _session())
    tools = response["result"]["tools"]
    names = [tool["name"] for tool in tools]
    assert names == [
        "ucs_scan",
        "ucs_list_rows",
        "ucs_metadata",
        "ucs_diff",
        "ucs_set_row",
        "ucs_replace",
        "ucs_batch_enabled",
        "ucs_preview",
        "ucs_apply",
        "ucs_undo",
        "ucs_report",
        "ucs_folders",
        "ucs_categories",
    ]
    for tool in tools:
        assert tool["description"]
        assert tool["inputSchema"]["type"] == "object"


def test_notification_has_no_response():
    assert mcp_server.handle({"jsonrpc": "2.0", "method": "notifications/initialized"}, _session()) is None


def test_unknown_tool_and_method_errors():
    bad_tool = _call(_session(), "ucs_nope")
    assert bad_tool["error"]["code"] == -32602
    bad_method = mcp_server.handle({"jsonrpc": "2.0", "id": 3, "method": "resources/list"}, _session())
    assert bad_method["error"]["code"] == -32601


def test_scan_then_list(tmp_path):
    folder = tmp_path / "drop"
    folder.mkdir()
    make_wav(folder / "one.wav")
    make_wav(folder / "two.wav")
    session = _session()
    scanned = _payload(_call(session, "ucs_scan", {"paths": [str(folder)]}))
    assert scanned["ok"] is True
    assert scanned["added"] == 2
    listed = _payload(_call(session, "ucs_list_rows", {"limit": 1}))
    assert listed["total"] == 2
    assert listed["shown"] == 1
    assert listed["truncated"] is True


def test_apply_defaults_to_dry_run(tmp_path):
    folder = tmp_path / "drop"
    folder.mkdir()
    wav = make_wav(folder / "one.wav")
    session = _session()
    _call(session, "ucs_scan", {"paths": [str(folder)]})
    row_id = _payload(_call(session, "ucs_list_rows"))["rows"][0]["id"]
    _call(session, "ucs_set_row", {"row_id": row_id, "field": "cat_id", "value": "RAIN"})
    _call(session, "ucs_set_row", {"row_id": row_id, "field": "fx_name", "value": "RainA"})

    response = _call(session, "ucs_apply", {})
    payload = _payload(response)
    assert payload["dry_run"] is True
    assert response["result"]["isError"] is False
    assert wav.exists()
    assert wav.name == "one.wav"


def test_apply_with_confirm_writes_then_undo(tmp_path):
    folder = tmp_path / "drop"
    folder.mkdir()
    wav = make_wav(folder / "one.wav")
    session = _session()
    _call(session, "ucs_scan", {"paths": [str(folder)]})
    row_id = _payload(_call(session, "ucs_list_rows"))["rows"][0]["id"]
    _call(session, "ucs_set_row", {"row_id": row_id, "field": "cat_id", "value": "RAIN"})
    _call(session, "ucs_set_row", {"row_id": row_id, "field": "fx_name", "value": "RainA"})

    applied = _payload(_call(session, "ucs_apply", {"confirm": True}))
    assert applied["ok"] is True, applied
    assert applied["journal"]
    assert not wav.exists()

    undone = _payload(_call(session, "ucs_undo", {"journal_id": applied["journal"]}))
    assert undone["ok"] is True, undone
    assert wav.exists()


def test_categories_tool_returns_ucs_rows():
    payload = _payload(_call(_session(), "ucs_categories", {"query": "雨", "limit": 5}))
    assert payload["ok"] is True
    assert payload["categories"]


def test_stdio_loop_speaks_jsonrpc(tmp_path):
    root = Path(__file__).resolve().parents[1]
    requests = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {"protocolVersion": "2024-11-05"},
                }
            ),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized"}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        ]
    )
    done = subprocess.run(
        [sys.executable, "-m", "ucs_namer.mcp_server"],
        input=requests + "\n",
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(root),
        env={**os.environ, "PYTHONPATH": str(root / "src")},
        timeout=120,
        check=False,
    )
    lines = [line for line in done.stdout.splitlines() if line.strip()]
    assert len(lines) == 2
    assert json.loads(lines[0])["result"]["serverInfo"]["name"] == "ucs-namer"
    assert len(json.loads(lines[1])["result"]["tools"]) == len(mcp_server.TOOLS)
