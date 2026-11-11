"""Build the Windows app and assemble a versioned delivery folder.

Examples:
    python packaging/build.py --both
    python packaging/build.py --onedir --out "D:/Ai-Files/Tools/Exe/12-UCS Namer（UCS 命名翻译）"

The delivery layout follows the existing Tools/Exe convention: an outer bilingual
tool folder (`number-English (Chinese)`), an English versioned folder with exe + _internal + README, and a
separate `-onefile` folder for the portable single-file build.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DIST = ROOT / "dist"
EXE_NAME = "UCS命名翻译器.exe"
CMD_EXE_NAME = "ucs-namer-cmd.exe"
VERSION = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
VERSION_DIR = f"ucs-namer-v{VERSION}"
CMD_DIR = f"ucs-namer-cmd-v{VERSION}"


def fail(message: str) -> None:
    raise SystemExit(message)


def check_versions() -> None:
    package = ROOT / "src" / "ucs_namer" / "__init__.py"
    text = package.read_text(encoding="utf-8")
    if f'__version__ = "{VERSION}"' not in text:
        fail(f"VERSION ({VERSION}) 与 src/ucs_namer/__init__.py 不一致")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    if f'version = "{VERSION}"' not in project:
        fail(f"VERSION ({VERSION}) 与 pyproject.toml 不一致")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_sums(folder: Path) -> Path:
    lines = []
    for path in sorted(folder.rglob("*")):
        if path.is_file() and path.name != "SHA256SUMS.txt":
            lines.append(f"{sha256(path)}  {path.relative_to(folder).as_posix()}")
    target = folder / "SHA256SUMS.txt"
    target.write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")
    return target


def run_pyinstaller(spec: str, flavor: str) -> Path:
    dist = DIST / flavor
    work = ROOT / "build" / flavor
    command = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        "--distpath",
        str(dist),
        "--workpath",
        str(work),
        str(ROOT / "packaging" / spec),
    ]
    print(">", " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT)
    return dist


def copy_readme(folder: Path) -> None:
    shutil.copy2(ROOT / "README.md", folder / "README.md")


def _archive(target: Path, inplace: bool) -> Path | None:
    """Rename an existing delivery folder aside so an in-place update stays reversible."""
    if not target.exists():
        return None
    if not inplace:
        fail(f"目标已存在，不覆盖：{target}")
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    backup = target.with_name(target.name + "-备份-" + stamp)
    if backup.exists():
        fail(f"备份已存在，不覆盖：{backup}")
    target.rename(backup)
    print(f"已留底：{backup}", flush=True)
    return backup


def assemble_onedir(out_root: Path, inplace: bool = False) -> Path:
    source = DIST / "onedir" / VERSION_DIR
    if not (source / EXE_NAME).exists():
        fail(f"未找到目录版产物：{source / EXE_NAME}")
    target = out_root / VERSION_DIR
    _archive(target, inplace)
    out_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    copy_readme(target)
    write_sums(target)
    return target


def assemble_onefile(out_root: Path, inplace: bool = False) -> Path:
    source = DIST / "onefile" / EXE_NAME
    if not source.exists():
        fail(f"未找到单文件产物：{source}")
    target = out_root / f"{VERSION_DIR}-onefile"
    _archive(target, inplace)
    out_root.mkdir(parents=True, exist_ok=True)
    target.mkdir(parents=True)
    shutil.copy2(source, target / EXE_NAME)
    copy_readme(target)
    write_sums(target)
    return target


def assemble_cmd(out_root: Path, inplace: bool = False) -> Path:
    source = DIST / "cmd" / CMD_DIR
    if not (source / CMD_EXE_NAME).exists():
        fail(f"未找到命令行产物：{source / CMD_EXE_NAME}")
    target = out_root / CMD_DIR
    _archive(target, inplace)
    out_root.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    copy_readme(target)
    write_sums(target)
    return target


def write_notice(out_root: Path, folders: list[Path], inplace: bool = False) -> Path:
    target = out_root / "交付说明.md"
    if target.exists():
        if not inplace:
            fail(f"交付说明已存在，不覆盖：{target}")
        stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
        backup = target.with_name("交付说明-备份-" + stamp + ".md")
        if backup.exists():
            fail(f"备份已存在，不覆盖：{backup}")
        target.rename(backup)
        print(f"已留底：{backup}", flush=True)
    lines = [
        "# UCS 命名翻译器交付说明",
        "",
        f"- 版本：{VERSION}",
        f"- 构建日期：{dt.date.today().isoformat()}",
        "- 源码仓：`D:/Ai-Files/Repos/ucs-namer`",
        "",
        "## 目录",
        "",
    ]
    for folder in folders:
        if "cmd" in folder.name:
            exe = folder / CMD_EXE_NAME
            label = "命令行 / MCP 版（AI 接入用，控制台程序）"
        else:
            exe = folder / EXE_NAME
            label = (
                "单文件版（便携，启动稍慢）"
                if "onefile" in folder.name
                else "目录版（推荐，启动更快）"
            )
        lines.append(f"- `{folder.name}/` — {label}")
        lines.append(f"  - 主程序：`{exe.name}`，{exe.stat().st_size:,} 字节，SHA256 `{sha256(exe)}`")
    lines += [
        "",
        "界面两个版本功能相同；目录版必须保留整个文件夹（含 `_internal`），单文件版可单独拷走 exe。",
        "运行需要 Microsoft Edge WebView2 运行时（Windows 10/11 通常已自带）。",
        "就地更新时旧目录与旧交付说明会保留为 `*-备份-<时间戳>`，可随时回退。",
        "",
        "## AI 接入（可选）",
        "",
        "`ucs-namer-cmd.exe` 是控制台入口，同一个程序两种用法：",
        "",
        "- 命令行：`ucs-namer-cmd.exe scan \"D:/音效库/3-BG\"`、`ucs-namer-cmd.exe apply --confirm`。",
        "- MCP：在 AI 客户端里把命令配成 `ucs-namer-cmd.exe`、参数配成 `mcp`，即可让 AI 扫描、预演、写入、撤销。",
        "",
        "写盘默认只预演，必须显式传 `--confirm`（CLI）或 `confirm=true`（MCP）才会真的动文件；",
        "每批都写 journal，可按批字节级撤销（`undo`）。",
        "界面开着时命令行走同一个进程、清单共享；界面没开时自动降级为独立会话，并在 stderr 提示。",
        "",
        "## 首次使用",
        "",
        "1. 双击 `UCS命名翻译器.exe`。",
        "2. 在「设置」填写 OpenAI 兼容的 API 地址、模型与 Key，点「测试连接」。",
        "3. 拖入音效文件或文件夹，点「在线翻译并生成 UCS 名」，核对预览后执行。",
        "",
        "API Key 只写入 `%APPDATA%/ucs-namer/settings.json`；批次日志与撤销数据在 `%LOCALAPPDATA%/ucs-namer/`。",
        "命令行模式的状态文件在 `%LOCALAPPDATA%/ucs-namer/cli-session.json`，MCP 模式在 `mcp-session.json`。",
        "",
        "## 完整性校验",
        "",
        "在每个版本目录内执行 `sha256sum -c SHA256SUMS.txt`（Git Bash）核对；PowerShell 可用 `Get-FileHash` 逐项对照。",
        "",
    ]
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description="构建 UCS 命名翻译器")
    parser.add_argument("--onedir", action="store_true", help="只构建目录版")
    parser.add_argument("--onefile", action="store_true", help="只构建单文件版")
    parser.add_argument("--both", action="store_true", help="两种界面版都构建（默认）")
    parser.add_argument("--cmd", action="store_true", help="只构建命令行 / MCP 版")
    parser.add_argument("--all", action="store_true", help="目录版 + 单文件版 + 命令行版")
    parser.add_argument("--out", type=Path, default=DIST / "release", help="交付根目录")
    parser.add_argument("--assemble-only", action="store_true", help="跳过构建，仅重新组装已有 dist 产物")
    parser.add_argument("--inplace", action="store_true", help="就地更新已有交付目录，旧版自动改名留底")
    args = parser.parse_args()

    check_versions()
    explicit = bool(args.onedir or args.onefile or args.cmd or args.all)
    build_onedir = args.all or args.onedir or args.both or not explicit
    build_onefile = args.all or args.onefile or args.both or not explicit
    build_cmd = args.all or args.cmd

    if not args.assemble_only:
        if build_onedir:
            run_pyinstaller("ucs-namer-onedir.spec", "onedir")
        if build_onefile:
            run_pyinstaller("ucs-namer-onefile.spec", "onefile")
        if build_cmd:
            run_pyinstaller("ucs-namer-cmd.spec", "cmd")

    folders: list[Path] = []
    out_root = args.out.resolve()
    if build_onedir:
        folders.append(assemble_onedir(out_root, args.inplace))
    if build_onefile:
        folders.append(assemble_onefile(out_root, args.inplace))
    if build_cmd:
        folders.append(assemble_cmd(out_root, args.inplace))
    notice = write_notice(out_root, folders, args.inplace)

    print("\n交付完成：")
    for folder in folders:
        exe_name = CMD_EXE_NAME if "cmd" in folder.name else EXE_NAME
        exe = folder / exe_name
        print(f"  {folder}  ({exe.stat().st_size:,} 字节 exe)")
    print(f"  {notice}")


if __name__ == "__main__":
    main()
