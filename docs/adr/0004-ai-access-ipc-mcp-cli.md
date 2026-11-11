# ADR 0004：AI 接入 —— 本地 IPC 单一状态 + MCP / CLI 薄壳

- 状态：已采用（2026-11-11 实施完成）
- 日期：2026-11-11

## 背景

软件目前只有 GUI 一种入口。需要支持用 CLI、MCP 等方式让 AI 直接驱动：批量扫描、读元数据、看改名前后对比、改字段、执行写盘、撤销。

关键的现有事实：`Bridge` 的 52 个方法里，45 个是纯数据方法（只依赖 `self.rows` 与 settings），只有 3 个与窗口强绑定（`pick_files` / `pick_folders` / `detach_metadata`）。业务逻辑没有散落在界面里，具备直接对外暴露的条件，不需要重写业务代码。

## 决定

三条裁决（2026-11-11，人类拍板）：

1. **路线：MCP + CLI 都做**，共用同一层能力，不维护两套逻辑。
2. **写盘默认必须显式确认**：`apply` 默认等价 dry-run，必须带 `confirm=true` 才真正落笔；另留 `--yolo` 开关，在人工盯屏的批量场景临时放开。
3. **清单合并成一条**：AI 与界面共用同一份 `rows`，不做两套并行清单。

由此确定架构：**GUI 进程是唯一状态持有者**，MCP 与 CLI 都是转发薄壳。

```
AI 客户端 ──MCP stdio──▶ ucs-namer-mcp.exe ──┐
                                             ├─ 本地 IPC ─▶ GUI 进程（Bridge + rows + journal）
终端 / AI ──命令行──────▶ ucs-namer-cli.exe ──┘
```

IPC 约定：

- 监听 `127.0.0.1`，端口随机分配，写入 `%LOCALAPPDATA%\ucs-namer\ipc.json`（含 `pid` / `port` / `token` / `started_at`）。
- 每次请求带 `token`；只接受本机回环连接，不开放到局域网。
- 传输用 JSON 行协议（一行一请求一响应），方法名与 `Bridge` 同名，参数按关键字传。
- 失败语义与 GUI 一致：返回 `{"ok": false, "error": "..."}`，不抛协议层异常。

GUI 未运行时的降级：CLI / MCP 在进程内自带一个 headless `Bridge`（不建窗口），响应里带 `"standalone": true`，stderr 提示「当前是独立会话，清单不与界面共享」。这样「合并」是常态、降级是例外且显式可见，不会静默出现两套清单互相覆盖。

写盘护栏（对应裁决 2）：

- `apply` 不带 `confirm` 时只返回预演结果（与 `preview` 同数据），一个字节不写。
- 带 `confirm` 时仍走既有 preflight → 逐文件原子写入 → 写 journal。
- 返回体必带 `journal_id`，AI 可在同一会话里自行 `undo`。
- `--yolo` 只对本次进程生效，不写进 settings。

## 后果

- 正面：AI 与人在同一份清单上协作，界面上能看到 AI 拖入或扫描出来的文件；journal 仍是全局唯一账本，两边都能按批撤销；两个薄壳逻辑极薄，改一次两端同时生效。
- 负面：GUI 必须运行才能「共享」；IPC 引入一个本机监听端口，需要 token 校验。本机任何进程理论上都能读 `ipc.json`，这是「信任本机用户」的模型，与既有 settings 明文存 API key 的信任边界一致。
- 需要把 3 个窗口绑定方法隔离出 IPC 暴露面：`pick_*` 会卡在模态对话框上等待用户，AI 调用会直接挂死。
- onefile 版冷启动 1–2 秒，CLI 每次调用都付这个成本 → 文档里建议 CLI 用 onedir 版。
- `Bridge` 的 job 是异步的（线程 + `status()` 轮询），IPC 需要一层同步等待封装，否则 CLI 拿到的是「任务已启动」而不是结果。

## 施工顺序

1. 抽 `src/ucs_namer/api.py`：45 个纯方法从 `Bridge` 平移出来，`Bridge` 变薄封装（GUI 行为不变，现有 60 项测试须全绿）。
2. 加 `src/ucs_namer/ipc.py`：GUI 内起服务端 + 客户端 + 同步等待封装。前置项是先做单实例约束（见「未决」）。
3. `src/ucs_namer/cli.py`：子命令 `scan / list / metadata / diff / set / replace / preview / apply / undo / report`。
4. `src/ucs_namer/mcp_server.py`：stdio 工具集，工具名与 `api.py` 方法一一对应。
5. 打包：`build.py` 增加 `--cmd` / `--all`，新增控制台产物 `ucs-namer-cmd.exe`（`mcp` 子参数进 MCP，其余进 CLI）。onedir 为主，onefile 在文档里标注冷启动代价。
6. 文档：README 增「AI 接入」节；本 ADR 状态改「已采用」；CHANGELOG 并入同一次 `0.1.0 就地更新`（按交付口径不新建版本目录、不升版本号）。

## 实施结果（2026-11-11）

- 单实例按「后启动者把已有窗口顶到前台后退出」实现（`instance.py`，命名互斥体 `Local\ucs-namer-gui`），已作为 IPC 前置项先落地。
- MCP 侧没有引入官方 SDK，改为零依赖手写 stdio JSON-RPC（`mcp_server.py`）：`initialize` 回显客户端协议版本，支持 `tools/list`、`tools/call`、`ping`，未知方法回 -32601、未知工具回 -32602。理由是打包体积与离线可用性，工具集协议面很窄，自实现风险可控。
- CLI 独立模式加了状态文件 `%LOCALAPPDATA%/ucs-namer/cli-session.json`，否则 `scan` 完进程退出、下一条 `apply` 就看不到清单；MCP 用 `mcp-session.json`。
- 全局参数（`--json`、`--state` 等）用 `parents` + `default=SUPPRESS` 同时挂到主命令与各子命令，避免 argparse 只能写在子命令前面的坑。
- 测试从 60 项增到 88 项（IPC 19 + MCP 9），ruff 全绿。

## 未决

- MCP 是否需要 `pick_folder` 之类触发系统对话框的工具：倾向不做，AI 直接收路径更可控，也避免挂死。
- MCP 工具调用是否需要审计日志（谁在什么时候改了哪些文件）：journal 已记录文件级变更，但没有「调用方」字段。若要区分人与 AI，需在 journal 加 `origin` 字段。
- 界面内的进度条目前不反映 IPC 触发任务的实时进度（任务在服务端线程里跑，表格要等下一次刷新才更新），体验上够用但不算实时。
