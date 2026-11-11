# 代码结构

```text
ucs-namer/
├── VERSION                      # 版本号真源
├── docs/
│   ├── UCS分类表-8.2.1.tsv      # 753 条分类数据（构建期生成 ucs_data.py）
│   ├── UCS命名规范.md           # 规范摘录与命名裁决
│   └── adr/                     # 架构取舍记录
├── packaging/
│   ├── entry.py                 # PyInstaller 入口（界面版）
│   ├── entry_cmd.py             # PyInstaller 入口（命令行 / MCP 版）
│   ├── version_info.txt         # Windows 文件版本信息
│   ├── build.py                 # 一键构建与交付目录组装
│   └── *.spec                   # 目录版 / 单文件版 / 命令行版
├── scripts/build_ucs.py         # 由 TSV 生成 Python 数据表
├── src/ucs_namer/
│   ├── __init__.py              # __version__
│   ├── config.py                # AppData 设置与原子保存
│   ├── ucs.py                   # 分类查询、短名单、搜索
│   ├── ucs_data.py              # 753 条分类静态数据
│   ├── naming.py                # 双名合成、消毒、编号与调性
│   ├── metadata.py              # 只读元数据查看、前后对比、执行后回看快照
│   ├── llm.py                   # OpenAI 兼容客户端、提示词、解析
│   ├── scan.py                  # 扫描与行模型
│   ├── execute.py               # 预检、改名、元数据、撤销
│   ├── journal.py               # 批次日志与原始 chunk
│   ├── api.py                   # CoreApi：无界面的能力层（界面 / CLI / MCP 共用）
│   ├── ipc.py                   # 本机回环 IPC 服务端、客户端与降级会话
│   ├── cli.py                   # 命令行入口
│   ├── mcp_server.py            # MCP stdio 服务器与工具集
│   ├── instance.py              # 单实例互斥体与窗口前置
│   ├── audio/wav_meta.py        # RIFF / bext / iXML
│   └── gui/
│       ├── __init__.py          # 窗口入口、DPI、日志、IPC 启停
│       ├── bridge.py            # js_api：仅窗口相关动作（对话框、独立元数据窗口）
│       ├── drop.py              # Windows OLE 拖放
│       └── assets/
│           ├── index.html       # 单页界面（内联 CSS/JS）
│           └── app.ico          # 应用图标
└── tests/                       # 合成 WAV、mock LLM、端到端执行、IPC / CLI / MCP
```

数据流：

```text
文件/文件夹 → scan → 行模型 → 本地短名单 → LLM 翻译 → naming.compose
     → 预览表（人工修改）→ execute.preflight → wav_meta 写元数据 → 改名 → journal
```

设置与运行数据：

- `%APPDATA%/ucs-namer/settings.json`：API 地址、模型、密钥、Creator/Source、批大小等。
- `%LOCALAPPDATA%/ucs-namer/journal/`：每批 JSON，含旧/新路径与原始 `bext`/`iXML`。
- `%LOCALAPPDATA%/ucs-namer/logs/ucs-namer.log`：运行日志，不含密钥。
