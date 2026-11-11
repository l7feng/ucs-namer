from __future__ import annotations

import pytest

from ucs_namer import naming


def test_compose_builds_english_and_chinese_ucs():
    row = {
        "cat_id": "gunauto",
        "fx_name": "Pistol Cock",
        "keywords_zh": ["手枪上膛", "金属撞击"],
        "stem": "0001_手枪上膛",
        "ext": ".wav",
    }
    result = naming.compose(row, creator="L7", source="SFX")
    assert result["english_ucs"] == "GUNAuto_Pistol Cock 0001_L7_SFX"
    assert result["english_metadata"] == "GUNAuto_Pistol Cock 0001_L7_SFX"
    assert (
        result["chinese_name"]
        == "【（枪炮_自动化武器）手枪上膛，金属撞击，0001】GUNAuto_Pistol Cock 0001_L7_SFX.wav"
    )
    assert result["keywords_zh"] == ["手枪上膛", "金属撞击", "0001"]
    assert result["category"]["cat_id"] == "GUNAuto"
    assert result["warnings"] == []


def test_number_is_inserted_before_tempo():
    row = {
        "cat_id": "GUNAuto",
        "fx_name": "Pistol Cock A",
        "keywords_zh": ["手枪上膛", "A"],
        "stem": "05_手枪 A",
        "ext": ".wav",
    }
    result = naming.compose(row)
    assert result["english_ucs"] == "GUNAuto_Pistol Cock 05 A"
    assert result["keywords_zh"] == ["手枪上膛", "05", "A"]
    assert "手枪上膛，05，A" in result["chinese_name"]


def test_clean_sanitizes_illegal_characters_and_underscores():
    row = {
        "cat_id": "GUNAuto",
        "fx_name": "Pistol/Cock_Metal",
        "keywords_zh": "手枪上膛、金属撞击; 手枪上膛",
        "stem": "file",
        "ext": ".wav",
    }
    result = naming.compose(row)
    assert result["fx_name"] == "Pistol Cock Metal"
    assert result["keywords_zh"] == ["手枪上膛", "金属撞击"]
    assert ":" not in result["chinese_name"]


def test_existing_ucs_prefix_is_stripped_from_fx_name():
    row = {
        "cat_id": "GUNAuto",
        "fx_name": "GUNAuto Pistol Cock",
        "keywords_zh": ["手枪上膛"],
        "stem": "x",
        "ext": ".wav",
    }
    assert naming.compose(row)["english_ucs"].startswith("GUNAuto_Pistol Cock")


def test_missing_fx_name_falls_back_to_subcategory():
    row = {
        "cat_id": "GUNAuto",
        "fx_name": "",
        "keywords_zh": ["枪声"],
        "stem": "x",
        "ext": ".wav",
    }
    result = naming.compose(row)
    assert result["fx_name"] == "Automatic"
    assert result["english_ucs"].startswith("GUNAuto_Automatic")


def test_invalid_catid_raises():
    row = {"cat_id": "GUN_NOPE", "fx_name": "X", "keywords_zh": ["x"], "stem": "x", "ext": ".wav"}
    with pytest.raises(naming.NamingError):
        naming.compose(row)


def test_compose_rows_records_error_without_raising():
    rows = [
        {"id": "a", "cat_id": "NOPE", "fx_name": "X", "keywords_zh": ["x"], "stem": "x", "ext": ".wav"},
        {"id": "b", "cat_id": "GUNAuto", "fx_name": "X", "keywords_zh": ["x"], "stem": "x", "ext": ".wav"},
    ]
    naming.compose_rows(rows)
    assert rows[0]["error"]
    assert rows[0]["chinese_name"] == ""
    assert not rows[1].get("error")
    assert rows[1]["chinese_name"].startswith("【（枪炮_自动化武器）")


def test_leading_number_rules():
    assert naming.leading_number("0001_手枪") == "0001"
    assert naming.leading_number("12-A") == "12"
    assert naming.leading_number("12345") is None
    assert naming.leading_number("A01_x") is None


def test_ascii_text_strips_non_ascii():
    assert naming.ascii_text("Gun_枪声_L7") == "Gun__L7"


def test_long_names_emit_warnings():
    row = {
        "cat_id": "AMBMisc",
        "fx_name": "Very Long " * 30,
        "keywords_zh": ["很长"] * 5,
        "stem": "x",
        "ext": ".wav",
    }
    warnings = naming.compose(row)["warnings"]
    assert any("180" in message for message in warnings)
