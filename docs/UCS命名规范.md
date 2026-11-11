---
type: note
title: UCS 命名规范
created: 2026-11-09
updated: 2026-11-09
status: active
tags: [UCS, 音效, 命名规范, 声音后期]
source: Universal Category System 8.2.1 分类表与《UCS Filenaming Convention 2021》；2026-11-09 人类「给我UCS命名规范文件放进软件和个人知识库」；namecraft 0.3.0 随包规范 `D:/Ai-Files/Repos/GitHub/namecraft/docs/UCS命名规范.md`
---

# UCS 命名规范

> 依据：Universal Category System（UCS）8.2.1 分类表与《UCS Filenaming Convention 2021》。本文件是实践摘要，不是 UCS 官方原文；CatID 以 UCS 8.2.1 分类表为准。软件随包副本见 [namecraft 项目](../06-软件制作/89-文件批量重命名工具/Documentation.md) 的 `docs/UCS命名规范.md`。

## 文件名结构

UCS 文件名至少包含四个信息块，块之间用下划线 `_` 分隔。下划线只用于分隔信息块，不应出现在块内部。

```text
CatID_FXName_CreatorID_SourceID
```

| 信息块 | 含义 | 要求 |
| --- | --- | --- |
| CatID | 类别／子类别缩写 | 唯一必需项；必须来自 UCS 8.2.1 分类表，大小写不可自创 |
| FXName | 声音的简要描述或标题 | 建议 25 字符以内，不试听也能看懂 |
| CreatorID | 录音师、声音设计师或厂商 | 姓名或注册缩写 |
| SourceID | 项目、剧集或音效库名称 | 过长时使用缩写 |

完整结构可带三个可选项：

```text
CatID(-UserCategory)_(VendorCategory-)FXName_CreatorID_SourceID_UserData
```

- **UserCategory**：紧跟在 CatID 后，用 `-` 连接，例如 `GUNAuto-INT`，表示室内录制等用户自定义子类。
- **VendorCategory**：放在 FXName 前，用 `-` 连接，例如 `UZI 9mm-Rapid Fire`，表示厂商库内分类。
- **UserData**：末尾自由区，常用于编号、麦克风型号、机位或视角；此处允许再次出现下划线，但不鼓励。

## 官方示例

```text
GUNAuto_Uzi 9mm Rapid Fire Close Up Short Bursts_TN_DORY
GUNAuto_UZI 9mm Rapid Fire Close Up Short Bursts_TN_DORY_WideStereoMKH8020
GUNAuto-INT_UZI 9mm Rapid Fire Close Up Short Bursts_TN_DORY
GUNAuto_UZI 9mm-Rapid Fire Close Up Short Bursts_TN_DORY
GUNAuto-EXT_UZI 9mm-Rapid Fire Close Up Short Bursts_TN_NONE_416-MKH8040-DualMono
```

## namecraft 的 UCS 命名规则

在 namecraft 0.3.0 的规则栈中添加「UCS 命名」后填写：

| 字段 | 说明 |
| --- | --- |
| CatID | 必填，从随附 753 个分类中选择；输入小写也会规范成标准大小写 |
| FXName 来源 | ① 使用当前文件名；② 固定描述；③ 按行名单逐文件指定 |
| CreatorID | 可留空 |
| SourceID | 可留空 |
| UserCategory | 可选，接在 CatID 后 |
| VendorCategory | 可选，接在 FXName 前 |
| UserData | 可选，放在末尾 |
| 剥离已有 UCS 分类前缀 | 默认开启；去掉文件名开头已存在的 CatID 与 UserCategory，便于重复处理 |
| 规范字符 | 默认开启；把 `\ / : * ? " < > |` 等非法字符转空格并合并空白 |

空的信息块会被省略，不会留下多余的下划线。扩展名始终原样保留。CatID 不在分类表中时，预览会直接报错，不会生成猜测值。

### 处理流程

1. 把音效文件拖入 namecraft，或选择文件夹。
2. 添加「UCS 命名」规则，点「选择分类」按 CatID、英文类别或中文类别搜索。
3. 按素材情况选择 FXName 来源，填写 CreatorID／SourceID。
4. 查看预览；需要唯一编号时，在 UCS 规则前或后串联「序号」规则。
5. 确认后执行改名；可在改名日志中撤销最近一次批次。

## 分类使用原则

- CatID 只使用分类表内的值，不自行拼写或创造缩写。
- Foley（`FOLY*`）只用于对画面表演的官方干净拟音；户外、非正式、自录素材应使用脚步（`FEET*`）或物体（`OBJ*`）等类别。
- 一个文件只归入一个 Category/SubCategory 对；需要更细的用途差异时，用 UserCategory、VendorCategory、UserData 或关键词表达。
- FXName 使用英文描述；在同一库内保持词序和用词一致。
- 文件名中避免重复词语；同批出现同名时，用 UserData 编号或「序号」规则区分。

## 分类表

UCS 8.2.1 分类表共 753 个 CatID，列依次为：

```text
Category  SubCategory  CatID  CatShort  Category_zh  SubCategory_zh  Synonyms_zh
```

- 软件副本：`D:/Ai-Files/Repos/GitHub/namecraft/docs/UCS分类表-8.2.1.tsv`（随交付包 `docs/` 分发，界面分类选择器直接读取同一份数据）。
- 本机 skill 副本：`%USERPROFILE%\.agents\skills\ucs-sound-naming\references\ucs-zh-en.tsv`。
- 本库不另存表文件，避免第二份数据真源；查询以软件随包 TSV 为准。

## 版本与来源

- 分类版本：UCS 8.2.1。
- 结构依据：UCS Filenaming Convention 2021。
- 本地整理：namecraft 0.3.0，2026-11-09。
- 官方站点：`https://universalcategorysystem.com/`（外网访问情况未在本轮核验）。

## 相关入口

- 项目：[89 · 文件批量重命名工具](../06-软件制作/89-文件批量重命名工具/Documentation.md)。
- 软件交付：`D:/Ai-Files/Tools/Exe/11-批量重命名工具/namecraft-v0.3.0/`。
- 命名 skill：`ucs-sound-naming`（核对 CatID 与中英文分类时使用）。
