"""IPC transport, CLI surface and the apply/undo safety rails."""

from __future__ import annotations

import json
import socket

import pytest

from tests.conftest import make_bext, make_wav
from ucs_namer import cli, config, ipc, scan
from ucs_namer.api import CoreApi


def _api_with(tmp_path, names) -> CoreApi:
    folder = tmp_path / "lib"
    folder.mkdir()
    rows = []
    for index, name in enumerate(names):
        path = make_wav(folder / name, bext=make_bext(f"OLD-{index}") if index == 0 else None)
        rows.append(scan.make_row(path))
    api = CoreApi()
    api.rows = rows
    return api


def _prepare(api: CoreApi, mapping: dict[str, str]) -> None:
    for row in api.rows:
        row["cat_id"] = "RAIN"
        row["fx_name"] = mapping.get(row["original"], "Rain")
    api.preview_all()


# -- transport ------------------------------------------------------------
def test_endpoint_written_and_removed():
    server = ipc.Server(CoreApi()).start()
    try:
        endpoint = ipc.read_endpoint()
        assert endpoint is not None
        assert endpoint["port"] == server.port
        assert endpoint["token"] == server.token
        assert ipc.available() is True
    finally:
        server.stop()
    assert ipc.read_endpoint() is None
    assert ipc.available() is False


def test_call_round_trip(tmp_path):
    api = _api_with(tmp_path, ["a.wav"])
    server = ipc.Server(api).start()
    try:
        response = ipc.call("status")
        assert response["ok"] is True
        assert response["total"] == 1
        assert response["standalone"] is False
        rows = ipc.call("get_rows")
        assert len(rows["rows"]) == 1
    finally:
        server.stop()


def test_wrong_token_rejected():
    server = ipc.Server(CoreApi()).start()
    try:
        endpoint = ipc.read_endpoint()
        with socket.create_connection((endpoint["host"], endpoint["port"]), timeout=3) as sock:
            sock.sendall(
                json.dumps({"token": "0" * 48, "method": "status", "params": {}}).encode() + b"\n"
            )
            answer = json.loads(sock.recv(65536).decode())
        assert answer["ok"] is False
        assert "token" in answer["error"]
    finally:
        server.stop()


def test_private_and_window_methods_rejected():
    server = ipc.Server(CoreApi()).start()
    try:
        private = ipc.call("_start_job")
        assert private["ok"] is False
        assert "不允许" in private["error"]
        window = ipc.call("detach_metadata")
        assert window["ok"] is False
        assert "不允许" in window["error"]
        dialog = ipc.call("pick_files")
        assert dialog["ok"] is False
        assert "不允许" in dialog["error"]
    finally:
        server.stop()


def test_bad_params_reported_not_crashing():
    server = ipc.Server(CoreApi()).start()
    try:
        answer = ipc.call("get_rows", nope=1)
        assert answer["ok"] is False
        assert "参数" in answer["error"]
        assert ipc.available() is True
    finally:
        server.stop()


def test_stale_endpoint_with_dead_pid_is_cleaned():
    path = ipc.endpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps({"pid": 999999, "host": "127.0.0.1", "port": 1, "token": "x"}),
        encoding="utf-8",
    )
    assert ipc.read_endpoint() is None
    assert path.exists() is False
    assert ipc.available() is False


def test_session_falls_back_to_standalone():
    session = ipc.Session()
    payload = session.call("status")
    assert payload.get("standalone") is True
    assert session.standalone is True


def test_session_forwards_to_server(tmp_path):
    api = _api_with(tmp_path, ["a.wav"])
    server = ipc.Server(api).start()
    try:
        session = ipc.Session()
        payload = session.call("status")
        assert payload.get("standalone") is False
        assert payload["total"] == 1
    finally:
        server.stop()


def test_scan_now_unwraps_job_result(tmp_path):
    folder = tmp_path / "drop"
    folder.mkdir()
    make_wav(folder / "one.wav")
    make_wav(folder / "two.wav")
    api = CoreApi()
    payload = api.scan_now([str(folder)])
    assert payload["ok"] is True
    assert payload["added"] == 2
    assert payload["total"] == 2


# -- write rails ----------------------------------------------------------
def test_apply_without_confirm_does_not_touch_files(tmp_path):
    api = _api_with(tmp_path, ["a.wav", "b.wav"])
    _prepare(api, {"a.wav": "RainA", "b.wav": "RainB"})
    before = sorted(item["path"] for item in api.rows)
    payload = api.apply_now()
    assert payload["ok"] is True
    assert payload["dry_run"] is True
    assert payload["counts"]["rename"] >= 2
    assert sorted(item["path"] for item in api.rows) == before
    assert all(not item.get("journal") for item in [payload])


def test_apply_confirm_writes_and_undo_restores(tmp_path):
    api = _api_with(tmp_path, ["a.wav", "b.wav"])
    _prepare(api, {"a.wav": "RainA", "b.wav": "RainB"})
    originals = sorted(item["path"] for item in api.rows)

    applied = api.apply_now(confirm=True)
    assert applied["ok"] is True, applied
    assert applied["dry_run"] is False
    assert applied["journal"]
    assert applied["counts"]["ok"] == 2
    renamed = sorted(item["path"] for item in api.rows)
    assert renamed != originals

    undone = api.undo_now(applied["journal"])
    assert undone["ok"] is True, undone
    assert sorted(item["path"] for item in api.rows) == originals


def test_apply_blocks_on_duplicate_targets(tmp_path):
    api = _api_with(tmp_path, ["a.wav", "b.wav"])
    _prepare(api, {"a.wav": "Same", "b.wav": "Same"})
    payload = api.apply_now(confirm=True)
    assert payload["ok"] is False
    assert "预检未通过" in payload["error"]
    assert payload["preflight"]["blocked"]


# -- CLI surface ----------------------------------------------------------
def test_cli_list_json_after_subcommand(tmp_path, capsys):
    folder = tmp_path / "drop"
    folder.mkdir()
    make_wav(folder / "one.wav")
    state = tmp_path / "state.json"
    assert cli.main(["--no-ipc", "--state", str(state), "scan", str(folder)]) == 0
    capsys.readouterr()
    assert cli.main(["--no-ipc", "--state", str(state), "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert len(payload["rows"]) == 1


def test_cli_state_persists_between_invocations(tmp_path, capsys):
    folder = tmp_path / "drop"
    folder.mkdir()
    make_wav(folder / "one.wav")
    state = tmp_path / "state.json"
    cli.main(["--no-ipc", "--state", str(state), "scan", str(folder)])
    capsys.readouterr()
    cli.main(["--no-ipc", "--state", str(state), "set", "--all", "--field", "cat_id", "--value", "RAIN"])
    capsys.readouterr()
    cli.main(["--no-ipc", "--state", str(state), "list", "--json"])
    payload = json.loads(capsys.readouterr().out)
    assert payload["rows"][0]["cat_id"] == "RAIN"


def test_cli_apply_requires_confirm(tmp_path, capsys):
    folder = tmp_path / "drop"
    folder.mkdir()
    wav = make_wav(folder / "one.wav")
    state = tmp_path / "state.json"
    cli.main(["--no-ipc", "--state", str(state), "scan", str(folder)])
    cli.main(["--no-ipc", "--state", str(state), "set", "--all", "--field", "cat_id", "--value", "RAIN"])
    cli.main(["--no-ipc", "--state", str(state), "set", "--all", "--field", "fx_name", "--value", "RainA"])
    capsys.readouterr()
    assert cli.main(["--no-ipc", "--state", str(state), "apply", "--json"]) == 0
    dry = json.loads(capsys.readouterr().out)
    assert dry["dry_run"] is True
    assert wav.exists()

    assert cli.main(["--no-ipc", "--state", str(state), "apply", "--confirm", "--json"]) == 0
    written = json.loads(capsys.readouterr().out)
    assert written["dry_run"] is False
    assert written["journal"]
    assert not wav.exists()


def test_cli_undo_reports_missing_batch(tmp_path, capsys):
    state = tmp_path / "state.json"
    code = cli.main(["--no-ipc", "--state", str(state), "undo"])
    assert code == 1
    assert "没有可撤销" in capsys.readouterr().out


@pytest.mark.parametrize("command", [["--help"], ["apply", "--help"]])
def test_cli_help_exits_cleanly(command, capsys):
    with pytest.raises(SystemExit) as caught:
        cli.main(command)
    assert caught.value.code == 0
    assert "ucs-namer-cli" in capsys.readouterr().out


def test_settings_dir_isolated():
    assert str(config.local_dir()).endswith("ucs-namer")
