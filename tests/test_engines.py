"""Tests for the three engines: local rules, traditional MT and the dispatcher."""

from __future__ import annotations

import pytest

from ucs_namer import config, engine, local_rules, mt_engine, ucs


def _row(original: str, folder: str = "") -> dict:
    return {
        "id": original,
        "original": original,
        "folder": folder,
        "stem": original.rsplit(".", 1)[0],
        "ext": "." + original.rsplit(".", 1)[-1],
        "translated": False,
        "error": "",
        "keywords_zh": [],
    }


# ---------------------------------------------------------------- local rules
def test_local_rule_classifies_chinese_description():
    row = _row("雨夜屋檐滴水.wav", "环境_雨")
    local_rules.apply_row(row, "L7", "SFX")

    assert row["cat_id"] == "RAINWatr"
    assert row["fx_name"]
    assert row["translated"] is True
    assert "雨夜屋檐滴水" in row["keywords_zh"]
    assert row["confidence"] > 0


def test_local_rule_locks_catid_prefix():
    row = _row("RAINWatr_Eave_Drip_Night.wav")
    local_rules.apply_row(row, "", "")

    assert row["cat_id"] == "RAINWatr"
    assert "锁" in row["notes"]
    assert row["fx_name"]


def test_local_rule_uses_english_filename():
    row = _row("CA1_Ambience_Airplane_Landing_Rolling_Stop.wav", "018 Collected Ambiences")
    local_rules.apply_row(row, "", "")

    assert row["cat_id"].startswith("AMB")
    assert "Ambience" in row["fx_name"] or "Airplane" in row["fx_name"]


def test_generic_words_do_not_decide_a_category():
    # "Loop" and "Birds" must not drag a file into MUSCLoop / WINGBird.
    row = _row("CA1_Ambience_Backyard_Birds_DistantCity_Loop.wav")
    local_rules.apply_row(row, "", "")

    assert row["cat_id"] not in {"MUSCLoop", "WINGBird"}


def test_local_rule_reports_unmatched_row():
    row = _row("QP09 1212 Wetlands thunderstorm 5.0.wav")
    local_rules.apply_row(row, "", "")

    assert row["cat_id"] == ""
    assert row["translated"] is False
    assert row["error"]


def test_keywords_use_ucs_subcategory_not_folder_title():
    folder = "018 Collected Ambiences 01【人类生活场景自然生态综合环境氛围音效-第1辑】"
    row = _row("CA1_Ambience_Backyard_Rain.wav", folder)
    local_rules.apply_row(row, "", "")

    category = ucs.BY_ID[row["cat_id"]]
    assert row["keywords_zh"] == [category.subcategory_zh]
    assert "人类生活场景" not in " ".join(row["keywords_zh"])


def test_folder_with_chinese_does_not_leak_into_keywords():
    # A Chinese folder must not become the keyword when the filename is English.
    row = _row("CA1_Ambience_City_Medium_Loop.wav", "04-AMB 【综合环境氛围】")
    local_rules.apply_row(row, "", "")

    assert row["keywords_zh"]
    assert all("综合环境氛围" not in word for word in row["keywords_zh"])


def test_local_rule_batch_and_self_check():
    rows = [_row("雨夜屋檐滴水.wav"), _row("RAINWatr_Drip.wav")]
    local_rules.translate_rows(rows, {"creator": "", "source": ""})

    assert all(row["cat_id"] for row in rows)
    assert all(row["cat_id"] in ucs.CATIDS for row in rows)
    check = local_rules.self_check()
    assert check["ok"] is True and "本地规则" in check["reply"]


# ------------------------------------------------------------------------- MT
@pytest.fixture(autouse=True)
def _clear_mt_cache():
    mt_engine._CACHE.clear()
    yield
    mt_engine._CACHE.clear()


@pytest.fixture()
def fake_google(monkeypatch):
    def _translate(text, timeout):
        return "Water dripping from the eaves on a rainy night"

    monkeypatch.setattr(mt_engine, "_google", _translate)
    yield


def test_mt_engine_uses_translation_for_fx(fake_google):
    row = _row("雨夜屋檐滴水.wav", "环境_雨")
    mt_engine.apply_row(row, {"mt_provider": "google"})

    assert row["cat_id"] == "RAINWatr"
    assert row["fx_name"].startswith("Dripping")
    assert row["translated"] is True
    assert "MT" in row["notes"]


def test_mt_engine_skips_english_only_input(monkeypatch):
    settings = {"mt_provider": "google"}

    def _explode(*_args, **_kwargs):
        raise AssertionError("MT should not be called for pure English input")

    monkeypatch.setattr(mt_engine, "_google", _explode)
    assert mt_engine.translate_text("Ambience Backyard Rain", settings) == "Ambience Backyard Rain"


def test_mt_engine_provider_error_is_reported(monkeypatch):
    def _fail(*_args, **_kwargs):
        raise mt_engine.MTError("网络不通")

    monkeypatch.setattr(mt_engine, "_google", _fail)
    row = _row("雨夜屋檐滴水.wav")
    mt_engine.apply_row(row, {"mt_provider": "google"})

    assert row["error"] == "网络不通"
    assert row["translated"] is False


def test_mt_engine_falls_back_to_local_for_english_filename(monkeypatch):
    # An English filename has nothing to translate; the folder being Chinese
    # must not drag it into MT and burn quota.
    def _explode(*_args, **_kwargs):
        raise AssertionError("MT should not be called for an English filename")

    monkeypatch.setattr(mt_engine, "_google", _explode)
    row = _row("CA1_Ambience_Backyard_Rain.wav", "04-AMB 【综合环境氛围】")
    mt_engine.apply_row(row, {"mt_provider": "google"})

    assert row["cat_id"].startswith("AMB") or row["cat_id"].startswith("RAIN")
    assert "本地规则" in row["notes"]
    assert row["translated"] is True


def test_google_retries_then_reports_rate_limit(monkeypatch):
    calls = {"n": 0}

    def _always_429(*_args, **_kwargs):
        calls["n"] += 1
        raise mt_engine.urllib.error.HTTPError("url", 429, "Too Many Requests", {}, None)

    monkeypatch.setattr(mt_engine.urllib.request, "urlopen", _always_429)
    monkeypatch.setattr(mt_engine.time, "sleep", lambda *_a: None)
    with pytest.raises(mt_engine.MTError) as info:
        mt_engine._google("雨夜", 5)

    assert calls["n"] == mt_engine._RETRIES
    assert "429" in str(info.value)


# -------------------------------------------------------------------- engine
def test_engine_defaults_to_api_and_validates():
    assert engine.current({}) == "api"
    assert engine.current({"engine": "local"}) == "local"
    assert engine.current({"engine": "nonsense"}) == "api"
    assert engine.label("mt") == "传统 MT"


def test_readiness_blocks_api_without_key():
    assert engine.readiness({"engine": "api", "api_key": ""})["ok"] is False
    assert engine.readiness({"engine": "api", "api_key": "sk-x"})["ok"] is True
    assert engine.readiness({"engine": "local"})["ok"] is True


def test_dispatcher_runs_local_engine():
    rows = [_row("雨夜屋檐滴水.wav")]
    used = engine.translate_rows(rows, {"engine": "local", "creator": "", "source": ""})

    assert used == "local"
    assert rows[0]["cat_id"] == "RAINWatr"


def test_engine_test_reports_self_check(monkeypatch):
    monkeypatch.setattr(engine, "llm", engine.llm)
    result = engine.test({"engine": "local"})
    assert result["ok"] is True and result["engine"] == "local"


def test_second_local_run_recovers_a_row_that_failed_before():
    # A row that failed last time still has row["error"] set; the batch entry
    # must not skip it forever.
    row = _row("CA1_Ambience_Backyard_Rain.wav", "04-AMB 【综合环境氛围】")
    row["error"] = "本地规则未匹配到分类：请手动选择或用其它引擎"
    local_rules.translate_rows([row], {"creator": "", "source": ""})

    assert row.get("cat_id", "") == ""
    # ... and once the error is cleared by the next run, it must classify again.
    row["error"] = ""
    local_rules.translate_rows([row], {"creator": "", "source": ""})
    assert row["cat_id"]


# -------------------------------------------------------------------- config
def test_settings_roundtrip_keeps_engine(isolated_appdata):
    saved = config.update_from_ui({"engine": "mt", "mt_provider": "argos"})
    assert saved["engine"] == "mt"
    assert saved["mt_provider"] == "argos"
    assert config.public_settings()["engine"] == "mt"

    saved = config.update_from_ui({"engine": "bogus", "mt_provider": "bogus"})
    assert saved["engine"] == "mt"
    assert saved["mt_provider"] == "argos"
