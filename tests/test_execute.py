from __future__ import annotations

from pathlib import Path

from tests.conftest import make_bext
from ucs_namer import execute, journal, naming, scan
from ucs_namer.audio import wav_meta


def _row_for(path: Path, **overrides) -> dict:
    row = scan.build_rows([str(path)])[0]
    row.update(
        {
            "cat_id": "GUNAuto",
            "fx_name": "Pistol Cock",
            "keywords_zh": ["手枪上膛", "金属撞击"],
        }
    )
    row.update(overrides)
    return row


def test_execute_renames_wav_and_writes_metadata(wav_factory):
    path = wav_factory("0001_手枪上膛.wav")
    row = _row_for(path)
    expected = naming.compose(row, "L7", "SFX")["chinese_name"]
    result = execute.execute([row], creator="L7", source="SFX")

    assert result["ok"] is True
    assert result["results"][0]["status"] == "ok"
    target = path.parent / expected
    assert target.exists()
    assert not path.exists()
    meta = wav_meta.read_metadata(target)
    assert meta["description"] == "GUNAuto_Pistol Cock 0001_L7_SFX"
    assert meta["ixml"]["SW_UCS"] == "GUNAuto_Pistol Cock 0001_L7_SFX"
    assert meta["ixml"]["SW_CATEGORY"] == "GUNAuto"
    assert meta["ixml"]["SW_CREATOR"] == "L7"
    assert meta["ixml"]["SW_SOURCE"] == "SFX"
    assert row["status"] == "done"


def test_undo_restores_name_and_original_metadata(wav_factory):
    path = wav_factory("0001_手枪上膛.wav", bext=make_bext("OLD"))
    original_bytes = path.read_bytes()
    row = _row_for(path)
    execute.execute([row], creator="L7", source="SFX")
    new_path = Path(row["path"])
    assert new_path != path

    result = execute.undo_last()
    assert result["ok"] is True
    assert path.exists()
    assert not new_path.exists()
    assert path.read_bytes() == original_bytes
    assert journal.latest_pending() is None


def test_undo_without_batch_reports_error():
    result = execute.undo_last()
    assert result["ok"] is False
    assert "没有可撤销" in result["error"]


def test_existing_target_blocks_whole_batch_and_writes_nothing(wav_factory):
    path = wav_factory("0001_手枪上膛.wav")
    row = _row_for(path)
    target = path.parent / naming.compose(row, "L7", "SFX")["chinese_name"]
    target.write_bytes(b"occupied")
    before = path.read_bytes()

    result = execute.execute([row], creator="L7", source="SFX")
    assert result["ok"] is False
    assert result["preflight"]["blocked"]
    assert path.read_bytes() == before
    assert target.read_bytes() == b"occupied"
    assert journal.list_journals() == []


def test_duplicate_targets_are_blocked(wav_factory):
    first = wav_factory("alpha.wav")
    second = make_wav_sibling(first, "beta.wav")
    rows = [_row_for(first), _row_for(second)]
    check = execute.preflight(rows, "L7", "SFX")
    assert check["ok"] is False
    assert any("重复" in issue["message"] for issue in check["blocked"])


def make_wav_sibling(reference: Path, name: str) -> Path:
    target = reference.parent / name
    target.write_bytes(reference.read_bytes())
    return target


def test_non_wav_is_renamed_without_metadata(wav_factory, tmp_path):
    path = wav_factory("0001_手枪上膛.wav")
    mp3 = path.parent / "0001_手枪上膛.mp3"
    mp3.write_bytes(b"ID3fake")
    row = _row_for(mp3)
    result = execute.execute([row], creator="L7", source="SFX")
    assert result["ok"] is True
    assert any("仅改名" in issue["message"] for issue in result["preflight"]["issues"])
    target = Path(row["path"])
    assert target.suffix == ".mp3"
    assert not mp3.exists()
    assert target.read_bytes() == b"ID3fake"


def test_rf64_wav_is_blocked_by_preflight(wav_factory):
    path = wav_factory("0001_手枪上膛.wav")
    raw = bytearray(path.read_bytes())
    raw[0:4] = b"RF64"
    path.write_bytes(bytes(raw))
    check = execute.preflight([_row_for(path)], "L7", "SFX")
    assert check["ok"] is False
    assert any("RF64" in issue["message"] for issue in check["blocked"])


def test_metadata_can_be_disabled(wav_factory):
    path = wav_factory("0001_手枪上膛.wav")
    row = _row_for(path)
    result = execute.execute([row], creator="L7", source="SFX", write_metadata=False)
    assert result["ok"] is True
    meta = wav_meta.read_metadata(Path(row["path"]))
    assert meta["has_bext"] is False
    assert meta["has_ixml"] is False


def test_missing_source_is_blocked(tmp_path):
    missing = tmp_path / "gone.wav"
    row = {
        "id": "x",
        "path": str(missing),
        "original": "gone.wav",
        "stem": "gone",
        "ext": ".wav",
        "folder": str(tmp_path),
        "cat_id": "GUNAuto",
        "fx_name": "Pistol Cock",
        "keywords_zh": ["手枪上膛"],
    }
    check = execute.preflight([row], "L7", "SFX")
    assert check["ok"] is False
    assert any("源文件不存在" in issue["message"] for issue in check["blocked"])


def test_export_csv_has_bom_and_rows(wav_factory, tmp_path):
    path = wav_factory("0001_手枪上膛.wav")
    rows = [_row_for(path)]
    naming.compose_rows(rows, "L7", "SFX")
    target = tmp_path / "out" / "ucs.csv"
    result = execute.export_csv(rows, target)
    assert result["ok"] is True
    raw = target.read_bytes()
    assert raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    assert "原文件名" in text
    assert "GUNAuto_Pistol Cock 0001_L7_SFX" in text
