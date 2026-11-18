"""Local rule-based naming engine: no model, no network, fully auditable.

The vocabulary is the UCS 8.2.1 table itself: every category already carries
Chinese names and Chinese synonyms, so a description can be classified by
matching those phrases. FXName is taken from the English words already present
in the filename when there are any (the common case for a library that was
named in English), otherwise from the matched Chinese terms.

Order of evidence, most deliberate first: a CatID prefix in the filename wins;
then the English words somebody typed into the name; only then the Chinese
phrases. Nothing is guessed silently: when no category can be justified the row
is left unassigned with an explanation, so the user can pick a CatID by hand.
"""

from __future__ import annotations

import re
from collections.abc import Callable

from . import ucs
from .config import load

Progress = Callable[[int, int, str], None]

_CJK = re.compile(r"[\u4e00-\u9fff]")
_ASCII_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*")
_EXT = re.compile(r"[.][A-Za-z0-9]{1,5}$")
_NUMBER_PREFIX = re.compile(r"^(\d{2,4})(?:[\s_\-.]+)")
_LEADING_INDEX = re.compile(r"^\d{1,6}[\s_\-.]+")
_ZH_SPLIT = re.compile(r"[、，,;；。:：!！?？\s_()（）\[\]【】]+")
_ALNUM_SPLIT = re.compile(r"[^a-z0-9]+")

_STOP = frozenset(
    """
    a an the of and with in on at to for from by or is are was were be been
    this that these those it its as into over under near there here some any
    sound sounds sfx fx audio wav file take takes taking record recorded
    """.split()
)

# Descriptors UCS does not model as categories, but that audio people write in
# Chinese descriptions all the time. Kept short and explicit on purpose.
DESCRIPTORS = {
    "近景": "Close",
    "远景": "Distant",
    "中景": "Mid",
    "室内": "Interior",
    "室外": "Exterior",
    "单次": "Single",
    "连续": "Multiple",
    "循环": "Loop",
    "潮湿": "Wet",
    "干燥": "Dry",
    "金属": "Metal",
    "木头": "Wood",
    "玻璃": "Glass",
    "柔软": "Soft",
    "响亮": "Loud",
    "轻微": "Light",
    "沉重": "Heavy",
    "快速": "Fast",
    "缓慢": "Slow",
    "白天": "Day",
    "夜晚": "Night",
    "清晨": "Morning",
    "傍晚": "Evening",
    "人群": "Crowd",
    "单人": "Single",
    "多人": "Multiple",
    "男声": "Male",
    "女声": "Female",
    "儿童": "Child",
    "动物": "Animal",
    "机械": "Machine",
    "电子": "Electronic",
    "水下": "Underwater",
    "户外": "Outdoor",
    "厨房": "Kitchen",
    "街道": "Street",
}


# Subcategory words that describe form, not subject matter. "Loop", "Variations"
# or "General" must never decide which category a sound belongs to.
_GENERIC_WORDS = frozenset(
    """
    misc other others general generic various mixed combination single multiple
    loop loops short long continuous sequence series variation variations
    processed raw clean stereo mono
    """.split()
)

_GENERIC_SUBS = frozenset({"MISC", "OTHER", "GENERAL", "VARIATIONS", "VARIOUS"})


def _stem(word: str) -> str:
    low = word.lower()
    if len(low) > 3 and low.endswith("s"):
        return low[:-1]
    return low


def _english_tokens(text: str) -> list[str]:
    return [word for word in _ASCII_WORD.findall(text or "")]


def _token_set(words) -> set[str]:
    result = set()
    for word in words:
        stemmed = _stem(word)
        if stemmed and stemmed not in _STOP:
            result.add(stemmed)
    return result


def _zh_segments(text: str) -> list[str]:
    result = []
    for part in _ZH_SPLIT.split(text or ""):
        part = _EXT.sub("", part.strip(" .-_"))
        part = _LEADING_INDEX.sub("", part).strip(" .-_")
        if part and _CJK.search(part):
            result.append(part)
    return result


def _category_words(category: ucs.Category) -> set[str]:
    words = set()
    for field in (category.category, category.subcategory):
        words.update(_stem(part) for part in _ALNUM_SPLIT.split(field.lower()) if part)
    words.discard("")
    return words


_CATEGORY_WORDS = {category.cat_id: _category_words(category) for category in ucs.CATEGORIES}


def _en_score(category: ucs.Category, tokens: set[str], raw_words: set[str]) -> int:
    score = 0
    if category.cat_id.lower() in tokens:
        score += 10
    sub_parts = [
        _stem(part)
        for part in _ALNUM_SPLIT.split(category.subcategory.lower())
        if part and _stem(part) not in _STOP
    ]
    if any(part in _GENERIC_WORDS for part in sub_parts) or category.subcategory in _GENERIC_SUBS:
        sub_parts = []
    cat_parts = [_stem(part) for part in _ALNUM_SPLIT.split(category.category.lower()) if part]
    if sub_parts:
        if len(sub_parts) > 1:
            # "BIRD OF PREY" must not fire on the word "bird" alone.
            if all(part in tokens for part in sub_parts):
                score += 5 + 2 * (len(sub_parts) - 1)
        elif len(sub_parts[0]) >= 3:
            part = sub_parts[0]
            if part in tokens:
                score += 5
            elif len(part) >= 5 and any(
                len(token) >= 4 and part.startswith(token) for token in tokens
            ):
                # "BIRDSONG" should answer to "Birds" — UCS keeps compound
                # subcategories that a filename naturally splits.
                score += 5
    for part in cat_parts:
        if len(part) >= 3 and part in tokens:
            score += 4
    return score


def _english_category(text: str) -> tuple[int, ucs.Category] | None:
    tokens = _token_set(_english_tokens(text))
    if not tokens:
        return None
    best_score = 0
    best: ucs.Category | None = None
    for category in ucs.CATEGORIES:
        score = _en_score(category, tokens, tokens)
        if score > best_score or (score == best_score and score and best is not None and category.cat_id < best.cat_id):
            best_score = score
            best = category
    if best is None or best_score < 4:
        return None
    if best_score == 4:
        # Only the category name matched (e.g. "ambience"): that is no reason to
        # pick an arbitrary child, so route it to that category's MISC entry.
        for category in ucs.CATEGORIES:
            if category.category.lower() == best.category.lower() and category.cat_id.endswith("Misc"):
                return 4, category
    return best_score, best


def _zh_score(category: ucs.Category, text: str) -> tuple[int, list[str]]:
    """Chinese evidence with phrase length weighted, so 人类 alone cannot win."""
    score = 0
    hits: list[str] = []
    phrases: list[tuple[str, int]] = []
    if category.category_zh:
        phrases.append((category.category_zh, 3))
    if category.subcategory_zh:
        phrases.append((category.subcategory_zh, 4))
    for phrase in category.synonyms_list:
        phrases.append((phrase, min(len(phrase), 6)))
    for phrase, weight in phrases:
        if len(phrase) < 1 or phrase not in text:
            continue
        score += weight
        if phrase not in hits:
            hits.append(phrase)
    # A single two-character subcategory word is not evidence on its own.
    if len(hits) <= 1 and max((len(hit) for hit in hits), default=0) <= 2:
        score -= 3
    return score, hits


def _chinese_category(text: str) -> tuple[int, ucs.Category, list[str]] | None:
    if not _CJK.search(text):
        return None
    candidates = ucs.shortlist(text, 8)
    best_score = 0
    best: ucs.Category | None = None
    best_hits: list[str] = []
    for category in candidates:
        score, hits = _zh_score(category, text)
        if score > best_score:
            best_score = score
            best = category
            best_hits = hits
    if best is None or best_score < 5:
        return None
    return best_score, best, best_hits[:4]


def _fx_from_filename(row: dict, category: ucs.Category, creator: str, source: str) -> str:
    """Reuse the English words already in the name; no translation involved."""
    stem = str(row.get("stem") or row.get("original") or "")
    stem = _EXT.sub("", stem)
    stem = _NUMBER_PREFIX.sub("", stem)
    stem = _LEADING_INDEX.sub("", stem)
    tokens = _english_tokens(stem)
    if tokens and category and tokens[0].lower() == category.cat_id.lower():
        tokens = tokens[1:]
    drop = (
        _STOP
        | _category_words(category)
        | _token_set(_ALNUM_SPLIT.split(creator.lower()))
        | _token_set(_ALNUM_SPLIT.split(source.lower()))
    )
    kept: list[str] = []
    for word in tokens:
        low = _stem(word)
        if low in drop or len(low) < 2 or re.search(r"[0-9]", word):
            continue
        if kept and _stem(kept[-1]) == low:
            continue
        kept.append(word)
    if len(kept) < 2:
        # A lone leftover word ("Collected") says nothing useful about the sound.
        return ""
    return " ".join(kept[:5])


def _fx_from_terms(text: str, category: ucs.Category) -> str:
    """Build the English FXName from the matched category plus descriptor words."""
    words: list[str] = []
    own_sources = [category.category, category.subcategory]
    for field in own_sources:
        for part in _ALNUM_SPLIT.split(field.lower()):
            if len(part) < 3 or part in ("misc", "other"):
                continue
            title = part.title()
            if title not in words:
                words.append(title)
    positions = []
    for label, english in DESCRIPTORS.items():
        index = text.find(label)
        if index >= 0:
            positions.append((index, english))
    for _index, english in sorted(positions):
        for part in english.split(" "):
            if part not in words:
                words.append(part)
        if len(words) >= 4:
            break
    return " ".join(words[:5])


def _pick_category(row: dict, text: str) -> tuple[ucs.Category | None, float, str]:
    locked = ucs.category_prefix(str(row.get("original") or ""))
    if locked is not None:
        return locked, 0.95, "本地规则 · 文件名已带 CatID 前缀，锁定原分类"
    english = _english_category(text)
    if english is not None:
        score, category = english
        confidence = 0.8 if score >= 10 else 0.74
        return category, confidence, f"本地规则 · 按文件名英文归类（{category.subcategory}，得分 {score}）"
    chinese = _chinese_category(text)
    if chinese is not None:
        score, category, hits = chinese
        confidence = 0.88 if score >= 14 else 0.8 if score >= 9 else 0.7
        note = f"本地规则 · 命中中文词「{'、'.join(hits)}」" if hits else f"本地规则 · 命中中文分类词（{category.label}）"
        return category, confidence, note
    return None, 0.0, "本地规则 · 未匹配到任何中英文分类词"


def _keywords_zh(stem: str, category: ucs.Category) -> list[str]:
    """Chinese keywords come from the filename only.

    A folder name is never used as a keyword: it repeats the whole set title on
    every row. When the filename carries no Chinese, the UCS sub-category name
    is the honest placeholder.
    """
    segments = _zh_segments(stem)
    result: list[str] = []
    seen: set[str] = set()
    for segment in segments:
        key = segment.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(segment)
    if not result:
        result = [category.subcategory_zh]
    return result[:5]


def apply_row(row: dict, creator: str = "", source: str = "") -> None:
    """Fill one row from local rules; never raises, explains instead."""
    stem = str(row.get("stem") or _EXT.sub("", str(row.get("original") or "")))
    text = f"{stem} {row.get('folder', '')}".strip()
    category, confidence, note = _pick_category(row, text)
    if category is None:
        row["cat_id"] = ""
        row["fx_name"] = ""
        row["keywords_zh"] = _zh_segments(stem)[:5]
        row["confidence"] = 0.0
        row["translated"] = False
        row["error"] = "本地规则未匹配到分类：请手动选择或用其它引擎"
        row["notes"] = note
        return
    fx = _fx_from_filename(row, category, creator, source)
    source_note = "文件名英文直接沿用"
    if not fx:
        fx = _fx_from_terms(text, category)
        source_note = "由中文词表拼装"
    if not fx:
        fx = category.subcategory.replace("_", " ").title()
        source_note = "退回子类英文名"
    keywords = _keywords_zh(stem, category)
    row["cat_id"] = category.cat_id
    row["fx_name"] = fx
    row["keywords_zh"] = keywords
    row["confidence"] = max(0.0, min(1.0, confidence))
    row["notes"] = f"{note} · {source_note}"
    row["translated"] = True
    row.pop("error", None)


def translate_rows(rows: list[dict], settings: dict | None = None, progress: Progress | None = None) -> None:
    """Fill rows in place using local rules only."""
    settings = settings or load()
    creator = str(settings.get("creator") or "")
    source = str(settings.get("source") or "")
    targets = [row for row in rows if not row.get("error")]
    total = len(targets)
    for index, row in enumerate(targets, start=1):
        if progress:
            progress(index - 1, total, f"本地规则 {index} / {total}")
        apply_row(row, creator, source)
    if progress:
        progress(total, total, f"本地规则完成 {total} / {total}")


def self_check() -> dict:
    """Report what the engine can do without touching the network."""
    samples = ("雨夜屋檐滴水.wav", "Ambience_Airplane_Landing.wav", "枪械_手枪上膛.wav")
    assigned = 0
    for original in samples:
        row = {"original": original, "folder": "", "stem": _EXT.sub("", original)}
        apply_row(row, "", "")
        if row.get("cat_id"):
            assigned += 1
    return {
        "ok": True,
        "engine": "local",
        "reply": f"本地规则可用 · {len(ucs.CATEGORIES)} 个分类 / {len(DESCRIPTORS)} 条描述词 · 自检样例命中 {assigned}/{len(samples)}（不联网）",
    }
