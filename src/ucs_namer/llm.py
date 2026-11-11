"""OpenAI-compatible chat client and the UCS translation prompt."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from collections.abc import Callable

from . import ucs
from .config import load
from .naming import clean_block, split_keywords

Progress = Callable[[int, int, str], None]


class LLMError(RuntimeError):
    """A user-facing model or transport error."""


SYSTEM_PROMPT = """你是 UCS（Universal Category System）8.2.1 音效命名助手。
你的任务：根据给定的中文文件名与候选分类，为每条素材选择唯一 CatID，并生成英文 FXName 与中文关键词。

硬性规则：
1. 只能从该条素材给出的候选 CatID 中选择，禁止创造或拼写候选之外的 CatID。
2. FXName 是英文描述性短语，2-6 个单词，用空格分词，不使用下划线，尽量不超过 25 个字符；不要重复 CatID 里的类别词。
3. keywords_zh 是 2-5 个中文关键词，覆盖声音主体、动作、材质或场景；原文件名开头的编号必须保留，放在关键词末尾、调性之前。
4. 文件名已经带 UCS 前缀时，保留原 CatID 与英文部分含义，中文关键词翻译英文部分，不改写 CreatorID/SourceID。
5. 只输出 JSON，不要 Markdown 代码块，不要解释。格式：
{"items":[{"id":1,"cat_id":"GUNAuto","fx_name":"Pistol Cock Metal","keywords_zh":["手枪上膛","金属撞击"],"confidence":0.9,"notes":""}]}
6. confidence 为 0-1；信息不足时降低置信度并在 notes 用中文说明，但仍要给出最接近的候选。
7. 不要在 fx_name 中写中文；不要输出文件扩展名。"""


def _endpoint(base_url: str) -> str:
    base = (base_url or "").strip().rstrip("/")
    if not base:
        raise LLMError("未设置 API 地址")
    if base.endswith("/chat/completions"):
        return base
    return base + "/chat/completions"


def _post(settings: dict, payload: dict, retries: int = 2) -> dict:
    api_key = str(settings.get("api_key", "")).strip()
    if not api_key:
        raise LLMError("尚未填写 API Key，请先在设置中配置")
    url = _endpoint(str(settings.get("base_url", "")))
    headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
    }
    timeout = float(settings.get("timeout", 120) or 120)
    last_error = ""
    for attempt in range(retries + 1):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8", "replace")
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise LLMError("模型接口返回的不是 JSON 对象")
            return data
        except urllib.error.HTTPError as error:
            detail = ""
            try:
                detail = error.read().decode("utf-8", "replace")[:600]
            except OSError:
                pass
            last_error = f"HTTP {error.code}：{detail or error.reason}"
            if error.code == 400 and "response_format" in detail:
                # Some OpenAI-compatible providers do not implement JSON mode.
                payload = dict(payload)
                payload.pop("response_format", None)
                continue
            if error.code in (429, 500, 502, 503, 504) and attempt < retries:
                time.sleep(1.5 * (attempt + 1))
                continue
            raise LLMError(last_error) from error
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = f"网络错误：{error}"
            if attempt < retries:
                time.sleep(1.2 * (attempt + 1))
                continue
            raise LLMError(last_error) from error
        except ValueError as error:
            raise LLMError(f"模型接口返回无法解析：{error}") from error
    raise LLMError(last_error or "模型请求失败")


def _extract_json(text: str) -> dict:
    text = (text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise LLMError("模型没有返回 JSON 对象")
    try:
        data = json.loads(text[start : end + 1])
    except ValueError as error:
        raise LLMError(f"模型 JSON 解析失败：{error}") from error
    if not isinstance(data, dict):
        raise LLMError("模型 JSON 顶层不是对象")
    return data


def _items_from(data: dict) -> list[dict]:
    items = data.get("items")
    if isinstance(items, list):
        return [item for item in items if isinstance(item, dict)]
    if isinstance(data.get("results"), list):
        return [item for item in data["results"] if isinstance(item, dict)]
    return []


def _candidate_lines(candidates: list[ucs.Category]) -> str:
    lines = []
    for category in candidates:
        synonyms = "、".join(category.synonyms_list[:12])
        lines.append(f"- {category.cat_id}｜{category.category_zh}｜{category.subcategory_zh}｜{synonyms}")
    return "\n".join(lines)


def _row_block(index: int, row: dict) -> str:
    text = f"{row.get('original', '')} {row.get('folder', '')}"
    candidates = ucs.shortlist(text, 16)
    existing = str(row.get("existing_ucs") or "").strip()
    existing_line = f"\n已有 UCS：{existing}（保留其 CatID 与英文含义）" if existing else ""
    return (
        f"\n[{index}] 原文件名：{row.get('original', '')}\n"
        f"父文件夹：{row.get('folder', '') or '（无）'}"
        f"{existing_line}\n候选：\n{_candidate_lines(candidates)}"
    )


def _prompt(rows: list[dict]) -> str:
    blocks = "".join(_row_block(index, row) for index, row in enumerate(rows, start=1))
    return (
        "请处理以下音效素材。只输出一个 JSON 对象，items 中每条都必须带 id。\n"
        + blocks
        + "\n\n再次强调：cat_id 只能从该条候选里选；fx_name 用英文；keywords_zh 为中文；只输出 JSON。"
    )


def _apply(row: dict, item: dict) -> None:
    category = ucs.canonical(str(item.get("cat_id", "")))
    if category is None:
        raise LLMError(f"模型返回非法 CatID：{item.get('cat_id') or '（空）'}")
    fx_name = clean_block(str(item.get("fx_name") or ""))
    if not fx_name:
        raise LLMError("模型没有返回 FXName")
    keywords = split_keywords(item.get("keywords_zh"))
    if not keywords:
        raise LLMError("模型没有返回中文关键词")
    try:
        confidence = float(item.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    row["cat_id"] = category.cat_id
    row["fx_name"] = fx_name
    row["keywords_zh"] = keywords
    row["confidence"] = max(0.0, min(1.0, confidence))
    row["notes"] = str(item.get("notes") or "").strip()
    row["translated"] = True


def _call(settings: dict, rows: list[dict]) -> list[dict]:
    payload = {
        "model": settings.get("model", "deepseek-chat"),
        "temperature": float(settings.get("temperature", 0.2) or 0.2),
        "max_tokens": 4000,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": _prompt(rows)},
        ],
        "response_format": {"type": "json_object"},
    }
    data = _post(settings, payload)
    try:
        content = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as error:
        raise LLMError("模型响应缺少 choices[0].message.content") from error
    parsed = _extract_json(str(content))
    items = _items_from(parsed)
    if not items:
        raise LLMError("模型 JSON 中没有 items")
    return items


def translate_rows(rows: list[dict], settings: dict | None = None, progress: Progress | None = None) -> None:
    """Translate rows in place, raising only when no useful result was produced."""
    settings = settings or load()
    pending = [row for row in rows if not row.get("error")]
    if not pending:
        return
    batch_size = max(1, min(20, int(settings.get("batch_size", 6) or 6)))
    total = len(pending)
    done = 0
    failures: list[str] = []
    for start in range(0, total, batch_size):
        batch = pending[start : start + batch_size]
        if progress:
            progress(done, total, f"正在翻译 {start + 1}-{start + len(batch)} / {total}")
        try:
            items = _call(settings, batch)
        except LLMError as error:
            failures.append(str(error))
            for row in batch:
                row["error"] = str(error)
            done += len(batch)
            continue
        by_id = {}
        for item in items:
            try:
                by_id[int(item.get("id"))] = item
            except (TypeError, ValueError):
                continue
        missing = []
        for index, row in enumerate(batch, start=1):
            item = by_id.get(index)
            if item is None:
                missing.append((index, row))
                continue
            try:
                _apply(row, item)
            except LLMError as error:
                row["error"] = str(error)
                failures.append(str(error))
        # One-by-one retry for rows the model omitted.
        for index, row in missing:
            try:
                items = _call(settings, [row])
                if not items:
                    raise LLMError("模型没有返回该条结果")
                _apply(row, items[0])
            except LLMError as error:
                row["error"] = str(error)
                failures.append(str(error))
        done += len(batch)
        if progress:
            progress(done, total, f"已完成 {done} / {total}")
    if failures and not any(row.get("translated") for row in pending):
        raise LLMError(failures[0])


def test_connection(settings: dict | None = None) -> dict:
    settings = settings or load()
    payload = {
        "model": settings.get("model", "deepseek-chat"),
        "temperature": 0,
        "max_tokens": 20,
        "messages": [
            {"role": "system", "content": "只输出 JSON，不要解释。"},
            {"role": "user", "content": '输出 {"ok":true}'},
        ],
        "response_format": {"type": "json_object"},
    }
    data = _post(settings, payload, retries=0)
    content = data["choices"][0]["message"]["content"]
    return {"ok": True, "model": settings.get("model", ""), "reply": str(content)[:80]}
