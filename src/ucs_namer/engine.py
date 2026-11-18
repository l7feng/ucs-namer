"""Pick the translation engine and keep a single entry point for callers."""

from __future__ import annotations

from collections.abc import Callable

from . import llm, local_rules, mt_engine
from .config import load

Progress = Callable[[int, int, str], None]

ENGINES = ("api", "local", "mt")
LABELS = {"api": "API 模型", "local": "本地规则", "mt": "传统 MT"}
HINTS = {
    "api": "调用 OpenAI 兼容接口，术语最灵活，需要 Key 与网络",
    "local": "只用 UCS 8.2.1 词表与文件名英文，不联网、不花钱，覆盖率取决于词表",
    "mt": "传统机器翻译出英文，再按 UCS 词表归类，免 API Key，术语可能不准",
}


def current(settings: dict | None = None) -> str:
    settings = settings if settings is not None else load()
    engine = str(settings.get("engine") or "api").strip().lower()
    return engine if engine in ENGINES else "api"


def label(engine: str) -> str:
    return LABELS.get(engine, LABELS["api"])


def readiness(settings: dict) -> dict:
    """What would stop the chosen engine from running right now."""
    engine = current(settings)
    if engine == "api":
        ok = bool(str(settings.get("api_key", "")).strip())
        return {"ok": ok, "message": "" if ok else "尚未填写 API Key，请先在设置中配置"}
    if engine == "mt":
        provider = str(settings.get("mt_provider") or "google").lower()
        if provider == "argos" and not mt_engine.available()["argos"]:
            return {"ok": False, "message": "未安装 Argos Translate：pip install argostranslate 后安装中→英模型包"}
        return {"ok": True, "message": ""}
    return {"ok": True, "message": ""}


def translate_rows(rows: list[dict], settings: dict | None = None, progress: Progress | None = None) -> str:
    """Run the configured engine; returns the engine name actually used."""
    settings = settings or load()
    engine = current(settings)
    if engine == "local":
        local_rules.translate_rows(rows, settings, progress)
    elif engine == "mt":
        mt_engine.translate_rows(rows, settings, progress)
    else:
        llm.translate_rows(rows, settings, progress)
    return engine


def test(settings: dict | None = None) -> dict:
    settings = settings or load()
    engine = current(settings)
    if engine == "local":
        return local_rules.self_check()
    if engine == "mt":
        return mt_engine.self_check(settings)
    return llm.test_connection(settings)
