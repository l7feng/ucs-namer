"""Metadata inspection, before/after previews, batch editing and review."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import make_bext, make_ixml, make_wav
from ucs_namer import execute, metadata, naming, scan, ucs
from ucs_namer.audio import wav_meta
from ucs_namer.gui.bridge import Bridge


def _cat_id() -> str:
    return ucs.all_categories()[0]["cat_id"]


def _row(path, fx: str = "Metal Hit") -> dict:
    row = scan.make_row(Path(path))
    row["cat_id"] = _cat_id()
    row["fx_name"] = fx
    row["keywords_zh"] = ["金属", "撞击"]
    naming.compose_rows([row], "L7", "SFX")
    return row


def test_bext_standard_fields_are_decoded(wav_factory):
    path = wav_factory(bext=make_bext("OLD", originator="FIELD_RECORDER"))
    parsed = wav_meta.read_metadata(path)["bext"]
    assert parsed["Description"] == "OLD"
    assert parsed["Originator"] == "FIELD_RECORDER"
    assert parsed["OriginationDate"] == "2026-01-02"
    assert parsed["Version"] == "1"
    assert parsed["CodingHistory"] == ""


def test_inspect_file_without_metadata_marks_every_field_wu(wav_factory):
    info = metadata.inspect_file(wav_factory())
    assert info["has_bext"] is False
    assert info["has_ixml"] is False
    assert info["rows"]
    assert all(item["value"] == metadata.EMPTY for item in info["rows"])
    assert any(item["label"] == "iXML" for item in info["rows"])


def test_inspect_file_lists_existing_ixml_tags(wav_factory):
    path = wav_factory(
        bext=make_bext("OLD"), ixml=make_ixml("  <SW_UCS>OLD_UCS</SW_UCS>\n")
    )
    info = metadata.inspect_file(path)
    labels = [item["label"] for item in info["rows"]]
    assert "iXML.PROJECT" in labels
    assert "iXML.SW_UCS" in labels
    assert any(
        item["label"] == "iXML.SW_UCS" and item["value"] == "OLD_UCS"
        for item in info["rows"]
    )


def test_diff_marks_existing_values_as_overwrite_and_lists_untouched(wav_factory):
    path = wav_factory(
        bext=make_bext("OLD"), ixml=make_ixml("  <SW_UCS>OLD_UCS</SW_UCS>\n")
    )
    diff = metadata.diff_row(_row(path), "L7", "SFX")
    assert diff["ok"] is True
    assert diff["overwrite_count"] >= 2
    states = {item["key"]: item["state"] for item in diff["fields"]}
    assert states["bext.description"] == "overwrite"
    assert states["iXML.SW_UCS"] == "overwrite"
    extras = [item["label"] for item in diff["extras"]]
    assert "iXML.PROJECT" in extras
    assert "bext.Originator" in extras


def test_diff_without_existing_metadata_only_adds(wav_factory):
    diff = metadata.diff_row(_row(wav_factory()), "L7", "SFX")
    assert diff["overwrite_count"] == 0
    assert all(item["state"] != "overwrite" for item in diff["fields"])
    assert all(
        item["before"] == metadata.EMPTY
        for item in diff["fields"]
        if item["key"] != "filename"
    )


def test_bridge_metadata_view_and_preview_diff(wav_factory):
    path = wav_factory(bext=make_bext("OLD"))
    row = _row(path)
    bridge = Bridge()
    bridge.rows = [row]
    view = bridge.metadata_view(row["id"])
    assert view["ok"] is True
    assert view["info"]["has_bext"] is True
    preview = bridge.preview_diff(row["id"])
    assert preview["ok"] is True
    assert preview["diff"]["overwrite_count"] >= 1
    assert bridge.metadata_neighbor(row["id"], 1)["ok"] is True


def test_batch_update_replace_enable_and_remove(wav_factory):
    bridge = Bridge()
    rows = [_row(wav_factory(name=f"f{i}.wav")) for i in range(3)]
    bridge.rows = rows
    ids = [row["id"] for row in rows]

    assert bridge.batch_update(ids, {"fx_name": "New FX", "keywords_zh": "甲，乙"})["changed"] == 3
    assert all(row["fx_name"] == "New FX" for row in bridge.rows)
    assert all(row["keywords_zh"] == ["甲", "乙"] for row in bridge.rows)

    bridge.batch_update(ids, {"keywords_zh": "丙"}, "append")
    assert all("丙" in row["keywords_zh"] for row in bridge.rows)

    replaced = bridge.batch_replace(ids, "fx_name", "new", "BEST")
    assert replaced["hits"] == 3
    assert all(row["fx_name"] == "BEST FX" for row in bridge.rows)

    bridge.batch_enabled(ids, False)
    assert all(not row["enabled"] for row in bridge.rows)
    assert bridge.batch_remove([ids[0]])["removed"] == 1
    assert len(bridge.rows) == 2


def test_batch_by_folder_matches_exact_parent(wav_factory):
    bridge = Bridge()
    first = _row(wav_factory(name="a.wav"))
    second = _row(wav_factory(name="b.wav"))
    bridge.rows = [first, second]
    result = bridge.batch_by_folder(
        str(Path(first["path"]).parent), {"fx_name": "Folder FX"}
    )
    assert result["changed"] == 1
    assert first["fx_name"] == "Folder FX"
    assert second["fx_name"] != "Folder FX"


def test_scan_grouped_reports_each_root_and_deduplicates(tmp_path):
    first = tmp_path / "folderA"
    second = tmp_path / "folderB"
    first.mkdir()
    second.mkdir()
    make_wav(first / "1.wav")
    make_wav(first / "2.wav")
    make_wav(second / "3.wav")
    rows, groups = scan.build_rows_grouped([str(first), str(second)])
    assert len(rows) == 3
    assert len(groups) == 2
    assert sorted(group["found"] for group in groups) == [1, 2]
    groups_by_folder = scan.folder_groups(rows)
    assert len(groups_by_folder) == 2

    repeated, repeat_groups = scan.build_rows_grouped([str(first), str(first)])
    assert len(repeated) == 2
    assert len(repeat_groups) == 2


def test_execution_journal_carries_metadata_snapshot_for_review(wav_factory):
    path = wav_factory(
        bext=make_bext("OLD"), ixml=make_ixml("  <SW_UCS>OLD_UCS</SW_UCS>\n")
    )
    row = _row(path)
    bridge = Bridge()
    bridge.rows = [row]

    result = execute.execute([row], "L7", "SFX", True)
    assert result["ok"] is True

    items = bridge.execution_items("")
    assert items["ok"] is True
    assert len(items["items"]) == 1
    assert items["items"][0]["has_snapshot"] is True

    review = bridge.execution_diff(items["journal"], row["id"])
    assert review["ok"] is True
    assert review["has_snapshot"] is True
    states = {item["key"]: item["state"] for item in review["diff"]["fields"]}
    assert states["iXML.SW_UCS"] == "overwrite"
    assert states["bext.description"] == "overwrite"
