from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from ucs_namer import llm


class _State:
    def __init__(self, responder):
        self.responder = responder
        self.requests: list[dict] = []


def _serve(responder):
    state = _State(responder)

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            length = int(self.headers.get("Content-Length", 0))
            payload = json.loads(self.rfile.read(length) or b"{}")
            state.requests.append(payload)
            status, body = state.responder(payload)
            raw = body.encode("utf-8") if isinstance(body, str) else json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, state


def _settings(server) -> dict:
    return {
        "base_url": f"http://127.0.0.1:{server.server_port}/v1",
        "api_key": "test-key",
        "model": "mock-model",
        "timeout": 5,
        "batch_size": 6,
        "temperature": 0.2,
    }


def _row(name: str, folder: str = "set") -> dict:
    return {
        "id": name,
        "original": name,
        "folder": folder,
        "stem": name.rsplit(".", 1)[0],
        "ext": ".wav",
        "existing_ucs": "",
        "cat_id": "",
        "fx_name": "",
        "keywords_zh": [],
        "translated": False,
        "error": "",
    }


def _item(index: int, cat_id: str = "GUNAuto") -> dict:
    return {
        "id": index,
        "cat_id": cat_id,
        "fx_name": "Pistol Cock",
        "keywords_zh": ["手枪上膛", "金属撞击"],
        "confidence": 0.87,
        "notes": "测试",
    }


def _completion(content) -> dict:
    text = content if isinstance(content, str) else json.dumps(content, ensure_ascii=False)
    return {"choices": [{"message": {"content": text}}]}


def test_translate_rows_applies_valid_items():
    server, state = _serve(lambda _payload: (200, _completion({"items": [_item(1), _item(2)]})))
    try:
        rows = [_row("手枪上膛.wav"), _row("金属撞击.wav")]
        llm.translate_rows(rows, _settings(server))
        assert all(row["translated"] for row in rows)
        assert rows[0]["cat_id"] == "GUNAuto"
        assert rows[0]["fx_name"] == "Pistol Cock"
        assert rows[0]["keywords_zh"] == ["手枪上膛", "金属撞击"]
        assert rows[0]["confidence"] == pytest.approx(0.87)
        assert len(state.requests) == 1
        assert "手枪上膛.wav" in state.requests[0]["messages"][1]["content"]
    finally:
        server.shutdown()


def test_missing_item_is_retried_individually():
    def responder(payload):
        text = payload["messages"][1]["content"]
        if "金属撞击.wav" in text:
            return 200, _completion({"items": [_item(1)]})
        return 200, _completion({"items": [_item(1)]})

    server, state = _serve(responder)
    try:
        rows = [_row("手枪上膛.wav"), _row("金属撞击.wav")]
        llm.translate_rows(rows, _settings(server))
        assert all(row["translated"] for row in rows)
        assert len(state.requests) == 2
        assert "金属撞击.wav" in state.requests[1]["messages"][1]["content"]
        assert "手枪上膛.wav" not in state.requests[1]["messages"][1]["content"]
    finally:
        server.shutdown()


def test_response_format_400_falls_back_without_json_mode():
    def responder(payload):
        if "response_format" in payload:
            return 400, {"error": "response_format is not supported"}
        return 200, _completion({"items": [_item(1)]})

    server, state = _serve(responder)
    try:
        rows = [_row("手枪上膛.wav")]
        llm.translate_rows(rows, _settings(server))
        assert rows[0]["translated"] is True
        assert len(state.requests) == 2
        assert "response_format" in state.requests[0]
        assert "response_format" not in state.requests[1]
    finally:
        server.shutdown()


def test_invalid_catid_sets_error_and_raises():
    server, _state = _serve(
        lambda _payload: (200, _completion({"items": [_item(1, cat_id="NOTREAL")]}))
    )
    try:
        rows = [_row("手枪上膛.wav")]
        with pytest.raises(llm.LLMError):
            llm.translate_rows(rows, _settings(server))
        assert rows[0]["translated"] is False
        assert "NOTREAL" in rows[0]["error"]
    finally:
        server.shutdown()


def test_missing_keywords_is_rejected():
    bad = _item(1)
    bad["keywords_zh"] = []
    server, _state = _serve(lambda _payload: (200, _completion({"items": [bad]})))
    try:
        rows = [_row("手枪上膛.wav")]
        with pytest.raises(llm.LLMError):
            llm.translate_rows(rows, _settings(server))
        assert rows[0]["error"]
    finally:
        server.shutdown()


def test_content_with_markdown_fence_is_parsed():
    content = '```json\n{"items":[{"id":1,"cat_id":"GUNAuto","fx_name":"Pistol Cock","keywords_zh":["手枪"],"confidence":1}]}\n```'
    server, _state = _serve(lambda _payload: (200, _completion(content)))
    try:
        rows = [_row("手枪.wav")]
        llm.translate_rows(rows, _settings(server))
        assert rows[0]["translated"] is True
    finally:
        server.shutdown()


def test_test_connection_reports_model():
    server, _state = _serve(lambda _payload: (200, _completion({"ok": True})))
    try:
        result = llm.test_connection(_settings(server))
        assert result["ok"] is True
        assert result["model"] == "mock-model"
    finally:
        server.shutdown()


def test_missing_api_key_is_reported():
    settings = {
        "base_url": "https://api.example.com/v1",
        "api_key": "",
        "model": "x",
        "timeout": 5,
    }
    with pytest.raises(llm.LLMError, match="API Key"):
        llm.translate_rows([_row("a.wav")], settings)


def test_endpoint_normalization():
    assert llm._endpoint("https://x/v1/") == "https://x/v1/chat/completions"
    assert llm._endpoint("https://x/v1/chat/completions") == "https://x/v1/chat/completions"
    with pytest.raises(llm.LLMError):
        llm._endpoint("")


def test_extract_json_variants():
    assert llm._extract_json('prefix {"items": []} suffix') == {"items": []}
    with pytest.raises(llm.LLMError):
        llm._extract_json("no json here")
