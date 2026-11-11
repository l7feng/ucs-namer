"""UCS 8.2.1 lookup, search and per-file shortlist generation."""

from __future__ import annotations

import re
from dataclasses import dataclass

from .ucs_data import CATEGORIES as RAW_CATEGORIES

_CATID_PREFIX = re.compile(r"^([A-Za-z][A-Za-z0-9]{2,})[_\-\s]")


@dataclass(frozen=True, slots=True)
class Category:
    cat_id: str
    category: str
    subcategory: str
    category_zh: str
    subcategory_zh: str
    synonyms: str

    @property
    def label(self) -> str:
        return f"{self.category_zh}_{self.subcategory_zh}"

    @property
    def synonyms_list(self) -> list[str]:
        return [part.strip() for part in re.split(r"[、,，;；]", self.synonyms) if part.strip()]

    def to_dict(self) -> dict[str, str]:
        return {
            "cat_id": self.cat_id,
            "category": self.category,
            "subcategory": self.subcategory,
            "category_zh": self.category_zh,
            "subcategory_zh": self.subcategory_zh,
            "label": self.label,
            "synonyms": self.synonyms,
        }


CATEGORIES: tuple[Category, ...] = tuple(Category(*row) for row in RAW_CATEGORIES)
BY_ID = {category.cat_id: category for category in CATEGORIES}
BY_LOWER = {category.cat_id.lower(): category for category in CATEGORIES}
CATIDS = frozenset(BY_ID)

if len(CATEGORIES) != 753:
    raise RuntimeError(f"UCS table must contain 753 categories, got {len(CATEGORIES)}")

# Used only when local synonym matching finds nothing. Kept short on purpose:
# the model still has to choose a real CatID and the user can correct it.
_FALLBACK_IDS = (
    "AMBMisc",
    "OBJMisc",
    "FOLYMisc",
    "DSGNImpt",
    "DSGNWhsh",
    "MAGMisc",
    "MAGElem",
    "UIMisc",
    "UIMvmt",
    "GUNMisc",
    "EXPLMisc",
    "FOODMisc",
    "LIQMisc",
    "FEETMisc",
    "HMNMisc",
    "ANMLMisc",
    "DOORMisc",
    "VEHMisc",
    "ELECMisc",
    "WATRMisc",
)
FALLBACK_CATEGORIES: tuple[Category, ...] = tuple(
    BY_ID[cat_id] for cat_id in _FALLBACK_IDS if cat_id in BY_ID
)


def canonical(value: str) -> Category | None:
    """Return the canonical category for a case-insensitive CatID, or None."""
    if not value:
        return None
    text = str(value).strip()
    return BY_ID.get(text) or BY_LOWER.get(text.lower())


def category_prefix(text: str) -> Category | None:
    match = _CATID_PREFIX.match(text.strip())
    if not match:
        return None
    return canonical(match.group(1))


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").lower()).strip()


def _score(category: Category, text: str) -> int:
    score = 0
    if category.cat_id.lower() in text:
        score += 12
    for word in re.split(r"[^a-z0-9]+", category.category.lower()):
        if len(word) >= 3 and word in text:
            score += 2
    for word in re.split(r"[^a-z0-9]+", category.subcategory.lower()):
        if len(word) >= 3 and word in text:
            score += 2
    if category.category_zh and category.category_zh in text:
        score += 5
    if category.subcategory_zh and category.subcategory_zh in text:
        score += 7
    matches = 0
    for phrase in category.synonyms_list:
        if len(phrase) >= 2 and phrase.lower() in text:
            matches += 1
    score += min(matches, 5) * 3
    return score


def shortlist(text: str, limit: int = 16) -> list[Category]:
    """Return the most likely categories for a Chinese/English description.

    The list always includes the locked CatID prefix when the filename already
    follows UCS, and falls back to common MISC categories when nothing matches.
    """
    normalized = _normalize(text)
    locked = category_prefix(text)
    ranked = sorted(
        ((_score(category, normalized), category) for category in CATEGORIES),
        key=lambda item: (-item[0], item[1].cat_id),
    )
    result: list[Category] = []
    if locked is not None:
        result.append(locked)
    for score, category in ranked:
        if score <= 0:
            break
        if category not in result:
            result.append(category)
        if len(result) >= limit:
            break
    if len(result) < limit:
        for category in FALLBACK_CATEGORIES:
            if category not in result:
                result.append(category)
            if len(result) >= limit:
                break
    return result[:limit]


def search(query: str, limit: int = 50) -> list[Category]:
    """Search the full catalog for the manual category picker."""
    normalized = _normalize(query)
    if not normalized:
        return list(CATEGORIES[:limit])
    ranked = sorted(
        ((_score(category, normalized), category) for category in CATEGORIES),
        key=lambda item: (-item[0], item[1].cat_id),
    )
    hits = [category for score, category in ranked if score > 0]
    if not hits:
        hits = [
            category
            for category in CATEGORIES
            if normalized in category.cat_id.lower()
            or normalized in category.category.lower()
            or normalized in category.subcategory.lower()
            or normalized in category.category_zh
            or normalized in category.subcategory_zh
        ]
    return hits[:limit]


def all_categories() -> list[dict[str, str]]:
    return [category.to_dict() for category in CATEGORIES]
