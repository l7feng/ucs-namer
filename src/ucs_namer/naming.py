"""Compose the English UCS metadata string and the Chinese UCS filename."""

from __future__ import annotations

import re
import unicodedata

from . import ucs

_INVALID = re.compile(r'[\\/:*?"<>|\x00-\x1f]')
_TEMPO = re.compile(r"^[A-Ga-g](?:#|b)?m?$")
_LEADING_NUMBER = re.compile(r"^(\d{2,4})(?:[\s_\-.]+)")
_KEY_SPLIT = re.compile(r"[、,，;；]+")


class NamingError(ValueError):
    """Raised when a row cannot be turned into a valid UCS name."""


def clean_text(value: str) -> str:
    text = _INVALID.sub(" ", str(value or ""))
    return re.sub(r"\s+", " ", text).strip()


def clean_block(value: str) -> str:
    """Clean one UCS underscore-delimited block; underscores become spaces."""
    text = clean_text(value).replace("_", " ")
    return re.sub(r"\s+", " ", text).strip(" .")


def clean_keyword(value: str) -> str:
    text = clean_text(value).replace("_", " ").strip(" .")
    text = text.replace("【", "").replace("】", "").strip()
    return text


def ascii_text(value: str) -> str:
    text = unicodedata.normalize("NFKD", value or "")
    return text.encode("ascii", "ignore").decode("ascii")


def split_keywords(value) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (list, tuple)):
        parts: list[str] = []
        for item in value:
            parts.extend(_KEY_SPLIT.split(str(item)))
    else:
        parts = _KEY_SPLIT.split(str(value))
    result: list[str] = []
    seen: set[str] = set()
    for part in parts:
        keyword = clean_keyword(part)
        if not keyword:
            continue
        key = keyword.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(keyword)
    return result


def leading_number(stem: str) -> str | None:
    match = _LEADING_NUMBER.match(stem or "")
    return match.group(1) if match else None


def _insert_before_tempo(items: list[str], value: str) -> list[str]:
    for index, item in enumerate(items):
        if _TEMPO.fullmatch(item.strip()):
            items.insert(index, value)
            return items
    items.append(value)
    return items


def _dedupe_words(words: list[str]) -> list[str]:
    result: list[str] = []
    for word in words:
        if result and result[-1].casefold() == word.casefold():
            continue
        result.append(word)
    return result


def _ensure_number(keywords: list[str], fx_words: list[str], number: str | None) -> None:
    if not number:
        return
    if number not in keywords:
        _insert_before_tempo(keywords, number)
    if number not in fx_words:
        _insert_before_tempo(fx_words, number)


def compose(row: dict, creator: str = "", source: str = "") -> dict:
    """Return {english_ucs, chinese_name, warnings, category} for one row."""
    category = ucs.canonical(str(row.get("cat_id", "")))
    if category is None:
        raise NamingError(f"CatID 不在 UCS 8.2.1 分类表中：{row.get('cat_id') or '（空）'}")

    fx = clean_block(str(row.get("fx_name") or ""))
    if not fx:
        fx = category.subcategory.replace("_", " ").title()
    if fx.casefold().startswith(category.cat_id.casefold() + " "):
        fx = fx[len(category.cat_id) :].strip()
    fx_words = _dedupe_words([word for word in fx.split(" ") if word])

    keywords = split_keywords(row.get("keywords_zh"))
    number = leading_number(str(row.get("stem") or ""))
    _ensure_number(keywords, fx_words, number)
    if not keywords:
        keywords = [clean_keyword(fx) or category.subcategory_zh]

    fx = " ".join(fx_words).strip()
    if not fx:
        raise NamingError("FXName 为空")

    blocks = [category.cat_id, fx]
    creator_block = clean_block(creator)
    source_block = clean_block(source)
    if creator_block:
        blocks.append(creator_block)
    if source_block:
        blocks.append(source_block)
    english = "_".join(blocks)

    label = clean_text(str(row.get("label_override") or ""))
    if not label:
        label = category.label
    chinese = f"【（{label}）{'，'.join(keywords)}】{english}{row.get('ext') or ''}"

    warnings: list[str] = []
    if len(english) > 180:
        warnings.append("英文 UCS 名超过 180 字符，部分工具可能截断")
    if len(chinese) > 200:
        warnings.append("中文文件名超过 200 字符，注意 Windows 路径长度")

    return {
        "english_ucs": english,
        "english_metadata": ascii_text(english),
        "chinese_name": chinese,
        "category": category.to_dict(),
        "keywords_zh": keywords,
        "fx_name": fx,
        "warnings": warnings,
    }


def compose_rows(rows: list[dict], creator: str = "", source: str = "") -> list[dict]:
    """Compose every row in place and return the same list with updated fields."""
    for row in rows:
        try:
            result = compose(row, creator, source)
        except NamingError as error:
            row["english_ucs"] = ""
            row["english_metadata"] = ""
            row["chinese_name"] = ""
            row["warnings"] = [str(error)]
            row["error"] = str(error)
            continue
        row.update(result)
        row.pop("error", None)
    return rows
