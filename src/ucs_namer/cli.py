"""Command-line front end so an AI agent (or a script) can drive the batch logic.

Talks to the running GUI over loopback IPC; when the GUI is not running it falls
back to an in-process CoreApi and says so on stderr. `apply` never writes unless
`--confirm` is passed -- see docs/adr/0004.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys

from . import ipc

STATE_MARK = {"same": "=", "add": "+", "overwrite": "!", "rename": "→"}


def force_utf8_stdio() -> None:
    """Pipe-friendly output: UTF-8 when redirected, console code page when attached.

    An AI or script reads us through a pipe, where the default is the ANSI code
    page and Chinese JSON would be undecodable. In an interactive console the
    local code page is the correct choice, so leave it alone.
    """
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            if stream is sys.stdin or not stream.isatty():
                stream.reconfigure(encoding="utf-8")


def _emit(payload: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return
    for line in _render(payload):
        print(line)


def _render(payload: dict) -> list[str]:
    if not payload.get("ok"):
        lines = ["失败：" + str(payload.get("error") or "未知错误")]
        for item in payload.get("results") or []:
            if item.get("status") != "ok":
                lines.append(
                    f"  ERR {item.get('original') or item.get('id')}  {item.get('error') or ''}"
                )
        for issue in (payload.get("preflight") or {}).get("issues") or []:
            if issue.get("level") == "block":
                lines.append(f"  阻止：{issue.get('message')}")
        return lines
    lines: list[str] = []
    if "added" in payload:
        lines.append(
            f"扫描完成：新增 {payload.get('added', 0)} 个，"
            f"跳过重复 {payload.get('duplicates', 0)} 个，清单共 {payload.get('total', 0)} 个"
        )
        for group in payload.get("groups") or []:
            flag = "目录" if group.get("is_dir") else "文件"
            lines.append(f"  · {flag} {group.get('root')} → {group.get('found')} 个")
        return lines
    if "items" in payload and "journal" in payload:
        items = payload.get("items") or []
        lines.append(
            f"批次 {payload.get('journal')}（{payload.get('created') or '未知时间'}）共 {len(items)} 条"
        )
        for item in items:
            mark = "OK" if item.get("status") == "ok" else item.get("status") or "?"
            snap = "  [有元数据快照]" if item.get("has_snapshot") else ""
            lines.append(f"  {mark} {item.get('original')} → {item.get('new_name')}{snap}")
        return lines
    if "items" in payload and "counts" in payload:
        counts = payload.get("counts") or {}
        lines.append(
            f"预演 {payload.get('total', 0)} 个："
            f"不变 {counts.get('same', 0)}，新增 {counts.get('add', 0)}，"
            f"覆盖 {counts.get('overwrite', 0)}，改名 {counts.get('rename', 0)}"
        )
        for item in payload.get("items") or []:
            overwrite = item.get("overwrite_count") or 0
            flag = f"  ! 将覆盖 {overwrite} 个字段" if overwrite else ""
            target = item.get("chinese_name") or "(未命名)"
            lines.append(f"  · {item.get('name')} → {target}{flag}")
        if payload.get("note"):
            lines.append(payload["note"])
        return lines
    if "rows" in payload and isinstance(payload["rows"], list):
        rows = payload["rows"]
        lines.append(f"清单共 {len(rows)} 个")
        for row in rows:
            mark = "启用" if row.get("enabled", True) else "跳过"
            suffix = (row.get("english_ucs") or "").strip()
            lines.append(
                f"  [{mark}] {row.get('original')} → {row.get('chinese_name') or '(未命名)'}"
                + (f"  |  {suffix}" if suffix else "")
            )
        return lines
    if "info" in payload and isinstance(payload.get("info"), dict):
        lines.append(f"{payload.get('name')}  ({payload.get('path')})")
        info = payload["info"]
        if info.get("note"):
            lines.append("  " + str(info["note"]))
        for row in info.get("rows") or []:
            lines.append(f"  {row.get('label')}：{row.get('value')}")
        return lines
    if "diff" in payload and isinstance(payload.get("diff"), dict):
        diff = payload["diff"]
        title = diff.get("name") or payload.get("name") or ""
        path = diff.get("path") or payload.get("path") or ""
        if title or path:
            lines.append(f"{title}  ({path})" if path else str(title))
        for field in diff.get("fields") or []:
            mark = STATE_MARK.get(field.get("state"), "?")
            lines.append(
                f"  {mark} {field.get('label')}：{field.get('after')} ← {field.get('before')}"
            )
        return lines
    if "results" in payload and isinstance(payload.get("results"), list):
        results = payload["results"]
        done = sum(1 for item in results if item.get("status") == "ok")
        lines.append(f"处理 {done} / {len(results)} 个")
        for item in results:
            mark = "OK" if item.get("status") == "ok" else "ERR"
            detail = item.get("error") or item.get("new_name") or item.get("path") or ""
            lines.append(f"  {mark} {item.get('original') or item.get('id')}  {detail}")
        if payload.get("journal"):
            lines.append(f"批次：{payload['journal']}")
        return lines
    if "removed" in payload or "changed" in payload:
        lines.append(
            f"已修改 {payload.get('changed', payload.get('removed', 0))} 个"
            + (f"，命中 {payload['hits']} 处" if payload.get("hits") is not None else "")
        )
        return lines
    if "groups" in payload and isinstance(payload.get("groups"), list):
        for group in payload["groups"]:
            lines.append(f"  · {group.get('root')} → {group.get('count')} 个")
        return lines
    if "categories" in payload:
        for item in payload["categories"]:
            lines.append(f"  {item.get('cat_id')}  {item.get('name_zh')} / {item.get('name_en')}")
        return lines
    if "settings" in payload:
        lines.append("设置已保存")
        return lines
    return [json.dumps(payload, ensure_ascii=False)]


def _ids(args) -> list[str]:
    if getattr(args, "all", False):
        return []
    raw = getattr(args, "ids", "") or ""
    return [item.strip() for item in raw.split(",") if item.strip()]


def _all_ids(session: ipc.Session) -> list[str]:
    rows = session.call("get_rows").get("rows") or []
    return [str(row.get("id")) for row in rows]


def _common_flags() -> argparse.ArgumentParser:
    """Flags accepted both before and after the subcommand.

    `default=SUPPRESS` keeps the subparser from resetting a value the user
    already supplied in front of the subcommand.
    """
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--no-ipc", action="store_true", default=argparse.SUPPRESS, help="不连界面，直接在本进程里跑"
    )
    common.add_argument(
        "--no-state", action="store_true", default=argparse.SUPPRESS, help="独立模式不读写状态文件"
    )
    common.add_argument("--state", default=argparse.SUPPRESS, help="独立模式的状态文件路径")
    common.add_argument("--json", action="store_true", default=argparse.SUPPRESS, help="输出原始 JSON")
    common.add_argument(
        "--timeout", type=float, default=argparse.SUPPRESS, help="等待秒数（默认 1800）"
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _common_flags()
    parser = argparse.ArgumentParser(
        prog="ucs-namer-cli",
        parents=[common],
        description="UCS 命名翻译器的命令行入口：默认转发给运行中的界面，界面没开时在本进程里跑。",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", parents=[common], help="扫描文件或文件夹并加入清单")
    scan.add_argument("paths", nargs="+")

    listing = sub.add_parser("list", parents=[common], help="列出当前清单")
    listing.add_argument("--limit", type=int, default=0, help="只显示前 N 条")

    metadata = sub.add_parser("metadata", parents=[common], help="查看某个文件现有的元数据")
    metadata.add_argument("row_id")

    diff = sub.add_parser("diff", parents=[common], help="改名前后对比；不给 row_id 则对比全部")
    diff.add_argument("row_id", nargs="?", default="")

    preview = sub.add_parser("preview", parents=[common], help="全量预演，不写盘（等同 apply 不带 --confirm）")
    preview.add_argument("--limit", type=int, default=0)

    apply_cmd = sub.add_parser("apply", parents=[common], help="写入磁盘；不带 --confirm 只预演")
    apply_cmd.add_argument("--confirm", action="store_true", help="确认写入")
    apply_cmd.add_argument("--yolo", action="store_true", help="跳过确认（仅本次进程）")

    undo = sub.add_parser("undo", parents=[common], help="撤销一批写盘")
    undo.add_argument("--journal", default="", help="批次 id，默认撤最新一批")

    report = sub.add_parser("report", parents=[common], help="回看某批执行的元数据前后快照")
    report.add_argument("--journal", default="")
    report.add_argument("--row", default="")

    setter = sub.add_parser("set", parents=[common], help="批量设置字段")
    setter.add_argument("--ids", default="", help="逗号分隔；与 --all 二选一")
    setter.add_argument("--all", action="store_true")
    setter.add_argument(
        "--field",
        required=True,
        choices=["cat_id", "fx_name", "label_override", "notes", "keywords_zh"],
    )
    setter.add_argument("--value", required=True)
    setter.add_argument("--append", action="store_true", help="关键词追加而不是替换")

    replace = sub.add_parser("replace", parents=[common], help="批量查找替换")
    replace.add_argument("--ids", default="")
    replace.add_argument("--all", action="store_true")
    replace.add_argument(
        "--field",
        required=True,
        choices=["cat_id", "fx_name", "label_override", "notes", "keywords_zh"],
    )
    replace.add_argument("--find", required=True)
    replace.add_argument("--replace", required=True, dest="replace_value")
    replace.add_argument("--case", action="store_true", help="区分大小写")

    enable = sub.add_parser("enable", parents=[common], help="批量启用或跳过")
    enable.add_argument("--ids", default="")
    enable.add_argument("--all", action="store_true")
    enable.add_argument("--off", action="store_true", help="改为跳过")

    remove = sub.add_parser("remove", parents=[common], help="从清单移出")
    remove.add_argument("--ids", default="")
    remove.add_argument("--all", action="store_true")

    sub.add_parser("folders", parents=[common], help="按文件夹统计清单")

    sub.add_parser("clear", parents=[common], help="清空清单")

    categories = sub.add_parser("categories", parents=[common], help="查询 UCS 分类")
    categories.add_argument("--query", default="")
    categories.add_argument("--limit", type=int, default=60)

    return parser


def main(argv: list[str] | None = None) -> int:
    force_utf8_stdio()
    args = build_parser().parse_args(argv)
    as_json = bool(getattr(args, "json", False))
    no_ipc = bool(getattr(args, "no_ipc", False))
    timeout = float(getattr(args, "timeout", 1800.0))
    state_path = None
    if not getattr(args, "no_state", False):
        from pathlib import Path

        raw_state = str(getattr(args, "state", "") or "")
        state_path = Path(raw_state) if raw_state else ipc.default_state_path()
    session = ipc.Session(prefer_ipc=not no_ipc, timeout=timeout, state_path=state_path)

    if args.command == "scan":
        payload = session.call("scan_now", paths=[str(p) for p in args.paths])
    elif args.command == "list":
        payload = session.call("get_rows")
        if args.limit and payload.get("rows"):
            payload["rows"] = payload["rows"][: args.limit]
    elif args.command == "metadata":
        payload = session.call("metadata_view", row_id=args.row_id)
    elif args.command == "diff":
        payload = session.call("preview_diff", row_id=args.row_id) if args.row_id else (
            session.call("preview_all")
        )
    elif args.command == "preview":
        payload = session.call("preview_all")
        if args.limit and payload.get("items"):
            payload["items"] = payload["items"][: args.limit]
    elif args.command == "apply":
        payload = session.call("apply_now", confirm=bool(args.confirm or args.yolo))
    elif args.command == "undo":
        payload = session.call("undo_now", journal_id=args.journal)
    elif args.command == "report":
        payload = session.call("execution_items", journal_id=args.journal)
        if payload.get("ok") and args.row:
            payload = session.call(
                "execution_diff", journal_id=payload.get("journal", ""), row_id=args.row
            )
    elif args.command == "set":
        ids = _ids(args) or _all_ids(session)
        mode = "append" if args.append else "replace"
        payload = session.call(
            "batch_update", ids=ids, patch={args.field: args.value}, mode=mode
        )
    elif args.command == "replace":
        ids = _ids(args) or _all_ids(session)
        payload = session.call(
            "batch_replace",
            ids=ids,
            field=args.field,
            find=args.find,
            replace=args.replace_value,
            case_sensitive=bool(args.case),
        )
    elif args.command == "enable":
        ids = _ids(args) or _all_ids(session)
        payload = session.call("batch_enabled", ids=ids, enabled=not args.off)
    elif args.command == "remove":
        ids = _ids(args) or _all_ids(session)
        payload = session.call("batch_remove", ids=ids)
    elif args.command == "folders":
        payload = session.call("folder_groups")
    elif args.command == "clear":
        payload = session.call("clear_rows")
    elif args.command == "categories":
        payload = session.call("search_categories", query=args.query, limit=args.limit)
    else:  # pragma: no cover - argparse rejects unknown commands
        print("未知命令", file=sys.stderr)
        return 2

    standalone = payload.get("standalone")
    if standalone and not as_json:
        reason = payload.get("fallback_reason") or "界面未运行"
        print(f"[独立会话] {reason}：清单不与界面共享，改完记得在界面里重新确认。", file=sys.stderr)

    _emit(payload, as_json)
    return 0 if payload.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
