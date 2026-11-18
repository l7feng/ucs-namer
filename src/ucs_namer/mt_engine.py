"""Traditional machine-translation engine: no LLM API, no token billing.

Two providers are supported:

* ``google`` — the public translate endpoint (no key, needs network);
* ``argos`` — Argos Translate running locally (offline, needs the extra package).

MT only produces English text; classification and FXName are still built from
the UCS 8.2.1 table, so the same table governs every engine.
"""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

from . import local_rules, ucs
from .config import load

Progress = Callable[[int, int, str], None]

PROVIDERS = ("google", "argos")
PROVIDER_LABELS = {"google": "Google 翻译（联网，免密钥）", "argos": "Argos Translate（离线）"}

_CJK = re.compile(r"[\u4e00-\u9fff]")
_GOOGLE_URL = "https://translate.googleapis.com/translate_a/single"
_CACHE: dict[tuple[str, str], str] = {}
# The public endpoint throttles bursts: keep a gap between calls and retry 429.
_MIN_GAP = 0.6
_RETRIES = 3
_last_call = [0.0]


def _pace() -> None:
    gap = _MIN_GAP - (time.monotonic() - _last_call[0])
    if gap > 0:
        time.sleep(gap)
    _last_call[0] = time.monotonic()



class MTError(RuntimeError):
    """A user-facing MT provider error."""


def available() -> dict:
    info = {"google": True, "argos": False}
    try:  # pragma: no cover - depends on the optional package
        import argostranslate.translate  # noqa: F401

        info["argos"] = True
    except Exception:
        info["argos"] = False
    return info


def _google(text: str, timeout: float) -> str:
    query = urllib.parse.urlencode(
        {"client": "gtx", "sl": "auto", "tl": "en", "dt": "t", "q": text}
    )
    request = urllib.request.Request(
        f"{_GOOGLE_URL}?{query}",
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) ucs-namer"},
    )
    payload = None
    for attempt in range(_RETRIES):
        _pace()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8", "replace"))
            break
        except urllib.error.HTTPError as error:
            if error.code == 429 and attempt < _RETRIES - 1:
                time.sleep(1.2 * (attempt + 1))
                continue
            if error.code == 429:
                raise MTError(
                    "Google 翻译接口限流（429）：停一会儿再试，或在设置里改用「本地规则」离线翻译"
                ) from error
            raise MTError(f"Google 翻译请求失败：{error}") from error
        except Exception as error:  # transport or JSON
            if attempt < _RETRIES - 1:
                time.sleep(0.8 * (attempt + 1))
                continue
            raise MTError(f"Google 翻译请求失败：{error}") from error
    try:
        segments = payload[0]
        return "".join(str(segment[0]) for segment in segments if segment and segment[0])
    except Exception as error:
        raise MTError("Google 翻译返回格式异常") from error


def _argos(text: str) -> str:
    try:  # pragma: no cover - depends on the optional package
        from argostranslate.translate import get_installed_languages, translate
    except Exception as error:
        raise MTError(
            "未安装 Argos Translate：先执行 pip install argostranslate，再在图形界面安装中→英模型包"
        ) from error
    source = target = None
    for language in get_installed_languages():
        if language.code in ("zh", "zt"):
            source = language
        if language.code == "en":
            target = language
    if source is None or target is None:
        raise MTError("Argos 缺少中→英模型包，请先安装")
    translation = source.get_translation(target)
    if translation is None:
        raise MTError("Argos 缺少中→英模型包，请先安装")
    return str(translation.translate(text))


def translate_text(text: str, settings: dict | None = None) -> str:
    """Return English for a Chinese description, using the configured provider."""
    settings = settings or load()
    provider = str(settings.get("mt_provider") or "google").lower()
    if provider not in PROVIDERS:
        provider = "google"
    clean = " ".join(str(text or "").split())
    if not clean or not _CJK.search(clean):
        return clean
    key = (provider, clean)
    if key in _CACHE:
        return _CACHE[key]
    timeout = float(settings.get("timeout", 120) or 120)
    result = _argos(clean) if provider == "argos" else _google(clean[:1500], min(timeout, 30))
    result = " ".join(result.split())
    if result:
        _CACHE[key] = result
    return result


def _fx_from_translation(english: str, category: ucs.Category) -> str:
    words: list[str] = []
    seen: set[str] = set()
    for raw in local_rules._english_tokens(english):
        low = raw.lower()
        if low in local_rules._STOP or low in seen:
            continue
        if low in local_rules._CATEGORY_WORDS[category.cat_id]:
            continue
        if re.search(r"[0-9]{3,}", raw):
            continue
        seen.add(low)
        words.append(raw[:1].upper() + raw[1:])
        if len(words) >= 5:
            break
    return " ".join(words)


def _stem_only(row: dict) -> str:
    """The filename alone, extension and leading catalogue number stripped."""
    stem = str(row.get("stem") or "").strip()
    if not stem:
        stem = re.sub(r"[.][A-Za-z0-9]{1,5}$", "", str(row.get("original") or ""))
    stem = re.sub(r"^[0-9]{1,6}[\s_\-.]+", "", stem)
    return " ".join(stem.replace("_", " ").replace("-", " ").split())


def _clean_source(row: dict) -> str:
    """Translate the filename only; the folder is a fallback, never extra noise."""
    stem = _stem_only(row)
    if not _CJK.search(stem):
        folder = str(row.get("folder") or "").replace("_", " ")
        if _CJK.search(folder):
            stem = f"{stem} {folder}".strip()
    return stem


def apply_row(row: dict, settings: dict, creator: str = "", source: str = "") -> None:
    if not _CJK.search(_stem_only(row)):
        # Nothing to translate: an English filename is already the evidence the
        # local engine needs, and sending it to MT only burns quota into 429s.
        local_rules.apply_row(row, creator, source)
        note = str(row.get("notes") or "")
        row["notes"] = "传统 MT · 文件名无中文，直接沿用本地规则" + (f" · {note}" if note else "")
        row["confidence"] = min(0.7, float(row.get("confidence") or 0.0))
        return
    text = _clean_source(row)
    try:
        english = translate_text(text, settings)
    except MTError as error:
        row["error"] = str(error)
        row["translated"] = False
        return
    if not english:
        row["error"] = "MT 没有返回译文"
        row["translated"] = False
        return
    chinese = local_rules._chinese_category(text)
    classified = local_rules._english_category(english)
    if chinese is not None:
        # The original Chinese is the better evidence: MT output vocabulary is
        # too loose to decide a category on its own.
        score, category, hits = chinese
        confidence = 0.78 if score >= 9 else 0.68
        why = f"按原文中文归类（{'、'.join(hits) or category.label}）"
    elif classified is not None:
        score, category = classified
        confidence = 0.7 if score >= 12 else 0.62
        why = f"按译文归类（{category.subcategory}，得分 {score}）"
    else:
        row["cat_id"] = ""
        row["fx_name"] = ""
        row["keywords_zh"] = local_rules._keywords_zh(text, ucs.CATEGORIES[0])
        row["translated"] = False
        row["error"] = "MT 译文未能归类：请手动选择 CatID"
        row["notes"] = "传统 MT · 未匹配到 UCS 分类"
        return
    fx = _fx_from_translation(english, category)
    keywords = local_rules._keywords_zh(text, category)
    provider = str(settings.get("mt_provider") or "google").lower()
    row["cat_id"] = category.cat_id
    row["fx_name"] = fx or category.subcategory.replace("_", " ").title()
    row["keywords_zh"] = keywords
    row["confidence"] = max(0.0, min(1.0, confidence))
    row["notes"] = f"传统 MT（{provider}）· {why} · 译文：{english[:60]}"
    row["translated"] = True
    row.pop("error", None)


def translate_rows(rows: list[dict], settings: dict | None = None, progress: Progress | None = None) -> None:
    """Fill rows in place using the configured MT provider."""
    settings = settings or load()
    creator = str(settings.get("creator") or "")
    source = str(settings.get("source") or "")
    targets = [row for row in rows if not row.get("error")]
    total = len(targets)
    for index, row in enumerate(targets, start=1):
        if progress:
            progress(index - 1, total, f"MT 翻译 {index} / {total}")
        apply_row(row, settings, creator, source)
    if progress:
        progress(total, total, f"MT 完成 {total} / {total}")


def self_check(settings: dict | None = None) -> dict:
    settings = settings or load()
    provider = str(settings.get("mt_provider") or "google").lower()
    if provider == "argos" and not available()["argos"]:
        raise MTError("未安装 Argos Translate，请先 pip install argostranslate 并安装中→英模型包")
    english = translate_text("雨夜屋檐滴水", settings)
    return {
        "ok": True,
        "engine": "mt",
        "reply": f"{PROVIDER_LABELS.get(provider, provider)} · 试译「雨夜屋檐滴水」→ {english[:60]}",
    }
