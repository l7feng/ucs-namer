from __future__ import annotations

from ucs_namer import ucs


def test_catalog_has_753_unique_categories():
    assert len(ucs.CATEGORIES) == 753
    assert len(ucs.CATIDS) == 753
    assert len({item.cat_id.lower() for item in ucs.CATEGORIES}) == 753


def test_known_category_uses_authoritative_tsv():
    category = ucs.BY_ID["AMBNaut"]
    assert category.category == "AMBIENCE"
    assert category.subcategory == "NAUTICAL"
    assert category.label == "环境_航海"
    assert ucs.BY_ID["GUNAuto"].label == "枪炮_自动化武器"


def test_canonical_is_case_insensitive():
    assert ucs.canonical("gunauto") is ucs.BY_ID["GUNAuto"]
    assert ucs.canonical(" GUNAUTO ") is ucs.BY_ID["GUNAuto"]
    assert ucs.canonical("NOPE") is None
    assert ucs.canonical("") is None


def test_category_prefix_detects_existing_ucs_name():
    category = ucs.category_prefix("GUNAuto_Pistol_Cock_L7_SFX.wav")
    assert category is ucs.BY_ID["GUNAuto"]
    assert ucs.category_prefix("手枪上膛.wav") is None


def test_shortlist_pins_existing_prefix_first():
    result = ucs.shortlist("GUNAuto_Pistol_Cock")
    assert result[0].cat_id == "GUNAuto"


def test_shortlist_matches_chinese_category_and_synonyms():
    ids = [item.cat_id for item in ucs.shortlist("枪炮 自动化")]
    assert "GUNAuto" in ids
    ids = [item.cat_id for item in ucs.shortlist("航海 码头 船坞")]
    assert "AMBNaut" in ids


def test_shortlist_limit_and_fallback():
    result = ucs.shortlist("完全未知的声音", 16)
    assert len(result) == 16
    assert all(item.cat_id in ucs.BY_ID for item in result)
    assert {"AMBMisc", "OBJMisc", "FOLYMisc"} <= {item.cat_id for item in result}


def test_search_finds_catid_and_chinese_text():
    assert any(item.cat_id == "GUNAuto" for item in ucs.search("gunauto"))
    assert any(item.cat_id == "AMBNaut" for item in ucs.search("航海"))


def test_all_categories_payload_is_serializable():
    rows = ucs.all_categories()
    assert len(rows) == 753
    first = rows[0]
    assert set(first) == {
        "cat_id",
        "category",
        "subcategory",
        "category_zh",
        "subcategory_zh",
        "label",
        "synonyms",
    }
