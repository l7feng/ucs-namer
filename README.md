# UCS 命名翻译器

把中文音效文件名翻成英文，生成合法的 UCS 8.2.1 英文名称与中文 UCS 文件名；确认后批量改名，并给 WAV 写入 BWF `bext` 与 iXML 元数据。翻译方式三选一：API 模型、纯本地规则、传统 MT。

- 英文 UCS（写入元数据）：`CatID_FXName_CreatorID_SourceID`
- 中文 UCS（作为文件名）：`【（中文分类_中文子类）关键词1，关键词2】` + 英文 UCS + 原扩展名
- 只改 `bext` 与 `iXML` 两个 chunk，其余音频数据逐字节保留；整批可撤销。

## 运行

开发环境：

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/ucs-namer
```

打包后的 Windows 版本见 `D:/Ai-Files/Tools/Exe/12-UCS Namer/`，双击 `UCS Namer.exe`。

## 使用流程

1. 一次拖入多个音效文件夹，或点「选择文件夹」（可多选）「添加文件」。支持 `.wav`、`.mp3`、`.flac`、`.aif`、`.aiff`、`.ogg`；后台递归扫描，按文件夹给出新增数量与重复跳过统计，不改动素材。
2. 打开设置 →「翻译方式」三选一：**API 模型**（填 OpenAI 兼容地址、模型与 Key，点「测试连接」）／**本地规则**（纯离线，用 UCS 中文词表 + 文件名英文归类，点「自检」）／**传统 MT**（Google 免费接口或离线 Argos，免 Key，点「自检」）。按钮文案与顶部 pill 会跟着变。
3. 点「…生成 UCS 名」（按钮名随引擎变化）。API 模型只从本地检索出的 16 个候选分类中选 CatID，不接触音频、绝对路径或用户名；本地规则与 MT 用的是同一张 UCS 8.2.1 表，没有任何请求外发。
4. 在预览表里核对、筛选、逐条修改；低置信度与缺失项会明确标出。文件名已以合法 CatID 开头时会锁定原分类。
5. 勾选表格最左「选中」列后，用批量工具条统一设字段、查找替换、批量启停、按文件夹套用；行末图标可查看元数据与前后对比。
6. 点「执行改名与写元数据」先做全量预检（目标重名、路径过长、不可写 WAV 都会拦截），确认后执行。
7. 执行完点「执行后回看元数据」检查本批实际写入了什么；需要回退时点「撤销上一批」。批次日志保存在 `%LOCALAPPDATA%/ucs-namer/journal/`。

非 WAV 文件只改名、不写元数据；`RF64` 拒绝写元数据，改名仍可执行并提示。

## 元数据

| 容器 | 字段 | 写入值 |
| --- | --- | --- |
| `bext` | `description` | 英文 UCS 名；超过 255 字节不写并提示 |
| iXML | `SW_UCS` | 英文 UCS 名 |
| iXML | `SW_CATEGORY` | CatID |
| iXML | `SW_KEYWORDS` | 英文 FXName + 中文关键词（中文逗号分隔） |
| iXML | `SW_CREATOR` / `SW_SOURCE` | 全局 CreatorID / SourceID（有则写） |

写入采用同目录临时文件 + `fsync` + 回读校验 + 原子替换；任何一步失败保留原文件。没有 `bext` 时插在 `fmt ` 之后，没有 iXML 时追加到末尾，RIFF 长度重算。

## 元数据查看与前后对比

点行末「元数据」图标打开查看窗口：列出 `bext` 标准字段（Description、Originator、OriginationDate、CodingHistory 等）与 iXML 的全部标签，空字段写「无（空）」。可逐条切换，也可点「弹出为独立窗口」把查看器拖到副屏常驻。

点「前后对比」图标打开预览窗口，左边是文件现有值、右边是将写入值，覆盖文件名、`bext.description` 与五个 `SW_*` 字段：

- 「将被覆盖」＝该字段已有值，执行会替换它。
- 「新增」＝原本没有，执行会写入。
- 窗口下方另列本次不会改动的其他已有元数据，例如 `bext.Originator`、`iXML.PROJECT`。

预览纯计算，不写盘。执行完可用「执行后回看元数据」按批次查看「执行前 → 实际写入」。

> **待裁决**：文件自带元数据的「保留 / 覆盖」策略尚未确定。当前执行逻辑仍会覆盖 `bext.description` 与五个 `SW_*` 字段；界面只做显示与提醒。想先保留某一条的原始元数据，可在对比窗口点「取消该条启用」跳过它；已执行的批次可用「撤销上一批」按字节恢复原始 chunk。

## 批量操作

- 拖入：一次拖多个文件夹，或「选择文件夹」多选；扫描在后台线程，按文件夹报告数量。
- 选中：表格最左列是「选中」（用于批量修改），第二列是「启用」（决定是否执行），两者互不干扰。
- 批量修改弹窗四个页签：统一设字段（只应用勾选的字段，勾选留空＝清空）、查找替换（支持中文与大小写开关）、批量启停与移除、按完整父目录分组套用。

## AI 接入（命令行 / MCP）

界面开着时，`ucs-namer-cmd.exe` 会连到同一个进程，**清单共享**——AI 拖进来的文件会出现在你的表格里；界面没开时自动降级为独立会话，并在 stderr 明确提示。

```bash
ucs-namer-cmd.exe scan "D:/音效库/3-BG"          # 扫描并加入清单
ucs-namer-cmd.exe list                            # 看清单
ucs-namer-cmd.exe diff                            # 全量预演：谁会被覆盖、谁改名
ucs-namer-cmd.exe apply                           # 只预演，一个字节都不写
ucs-namer-cmd.exe apply --confirm                 # 真写盘，返回批次号
ucs-namer-cmd.exe report                          # 回看这批的元数据前后快照
ucs-namer-cmd.exe undo                            # 按字节撤销上一批
ucs-namer-cmd.exe set --all --field cat_id --value RAIN
ucs-namer-cmd.exe replace --all --field fx_name --find Rain --replace RainHeavy
```

所有子命令都接受 `--json`（可放在子命令前后）。MCP 模式：

```json
{
  "mcpServers": {
    "ucs-namer": {
      "command": "D:/Ai-Files/Tools/Exe/12-UCS Namer/ucs-namer-cmd-v0.1.0/ucs-namer-cmd.exe",
      "args": ["mcp"]
    }
  }
}
```

13 个工具：`ucs_scan`、`ucs_list_rows`、`ucs_metadata`、`ucs_diff`、`ucs_set_row`、`ucs_replace`、`ucs_batch_enabled`、`ucs_preview`、`ucs_apply`、`ucs_undo`、`ucs_report`、`ucs_folders`、`ucs_categories`。

**写盘护栏**：`apply` / `ucs_apply` 默认只预演，必须显式传 `--confirm` 或 `confirm=true` 才动文件。原因是这工具改的是文件名——Pro Tools 工程、剪映草稿、库索引里存的是旧名引用，改完再撤销，名字能回、引用回不来。每批仍写 journal，可按批字节级撤销。

## 安全与隐私

- API Key 只保存在 `%APPDATA%/ucs-namer/settings.json`，不写入仓库、日志或项目记录；日志只记录是否已配置。
- **本地规则**方式零网络访问：只用内置 UCS 中文词表与文件名本身。
- **传统 MT**只把文件名（去扩展名与编号）送到所选服务（Google 免费接口或本机 Argos），不发文件夹、不发音频；文件名里没有中文时直接走本地规则，不消耗翻译额度。
- **API 模型**只发送原文件名、父文件夹名、Creator/Source 与候选分类，不上传音频。
- 默认 `https://api.deepseek.com/v1` + `deepseek-chat`，可在设置页替换为任意 OpenAI 兼容服务；优先请求 JSON 模式，供应商不支持时自动去掉该参数重试。

## 开发

```bash
.venv/Scripts/python -m pytest      # 107 项测试
.venv/Scripts/ruff check .
.venv/Scripts/python packaging/build.py --onedir      # 目录版
.venv/Scripts/python packaging/build.py --onefile     # 单文件版
.venv/Scripts/python packaging/build.py --cmd         # 命令行 / MCP 版
.venv/Scripts/python packaging/build.py --all --inplace --out "D:/Ai-Files/Tools/Exe/12-UCS Namer"   # 就地更新 exe，旧版自动留底
```

结构说明见 [STRUCTURE.md](STRUCTURE.md)，变更见 [CHANGELOG.md](CHANGELOG.md)，设计取舍见 `docs/adr/`。

## 已知边界

- 真实供应商的限流、计费与 JSON 能力未在本机验证；默认用 DeepSeek，可自行更换。
- Google 免密钥接口有频率限制：短时间批量翻译会返回 429。程序内置 0.6 秒间隔与 3 次退避重试，仍被限流时该条会明确报错（换「本地规则」或过一会儿再跑）。
- Argos 离线分支需自行 `pip install argostranslate` 并安装中→英模型包，未装时设置页会提前拦截。
- 本地规则与 MT 的归类都受 UCS 词表覆盖度限制；真实素材 307 条实测本地规则自动归类 303 条（98.7%），剩下 4 条会明确报「未匹配」让用户手选。
- 大模型可能给出不合适的分类或英文表达，预览表是最后闸门；置信度低时应人工复核。
- 非 WAV 格式不写元数据；如需跨格式写入需另立阶段。
- 自带元数据的保留 / 覆盖策略待裁决；本次版本只做显示与提醒，未改动写入逻辑。
- IPC 只监听本机回环，token 落在 `%LOCALAPPDATA%/ucs-namer/ipc.json`，信任边界等同本机用户；不要在多用户共用机器上把该目录设为全局可读。
- MCP 是独立进程，界面关掉时它自己跑一份清单；重启 MCP 进程后靠 `mcp-session.json` 恢复，不保证与界面最后一次状态一致。
- 独立元数据窗口依赖 pywebview 运行时创建窗口；若被环境拦截，页面内查看窗口仍可用。
- 运行需要 Microsoft Edge WebView2 运行时（Windows 10/11 通常已自带）。
