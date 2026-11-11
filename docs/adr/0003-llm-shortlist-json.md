# ADR 0003：本地短名单 + OpenAI 兼容 JSON

- 状态：已采用
- 日期：2026-11-10

## 背景

UCS 8.2.1 有 753 个 CatID。把整表发给模型既费 token，也容易让模型编造分类；不同供应商对 JSON 模式支持不一致。

## 决定

- 用分类表的中文分类、子分类与同义词做本地检索，按命中数取前 16 个候选；无命中时给常见类兜底。
- 每批默认 6 条，发送原文件名、父文件夹名、Creator/Source 与候选 CatID；要求严格 JSON。
- 模型只允许从候选 CatID 中选择；返回条目缺失时按 id 单独重试，仍缺失则记为该行失败，不静默编造。
- 优先 `response_format={"type":"json_object"}`；供应商返回 400 时去掉该参数重建请求体重试。
- 端点固定 `{base_url}/chat/completions`，`Authorization: Bearer <key>`，`temperature=0.2`。

## 后果

- 模型自由度受限，但分类始终合法；低置信度在界面显式提示。
- 供应商协议差异只影响 JSON 模式与重试，不绑定 DeepSeek。
- 真实限流、计费与跨机行为仍需实测。
