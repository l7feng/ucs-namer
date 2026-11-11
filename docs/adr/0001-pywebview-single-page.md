# ADR 0001：pywebview + 单页 HTML

- 状态：已采用
- 日期：2026-11-10

## 背景

需要 Windows 桌面批处理工具：拖放、表格预览、行内编辑、弹窗；同时避免引入前端构建链与 Electron 体积。

## 决定

界面用 pywebview（WebView2 `edgechromium`）承载单个 `index.html`，CSS/JS 全部内联；Python 侧通过 `js_api` 暴露 `Bridge`，耗时任务在线程中运行，前端以 320ms 轮询 `status` 获取进度，`ack_job` 收尾。

## 后果

- 拖放需要单独接 Windows OLE（`gui/drop.py`），关闭 WebView2 自带的 `AllowExternalDrop` 避免重复处理。
- 前端没有构建步骤，改界面即改一个文件；也意味着代码量大时需要自律分节。
- 依赖 WebView2 运行时；Win10/11 通常已存在。
- 任务队列限制为单并发，避免同时改名与翻译互相干扰。
