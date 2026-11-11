"""PyInstaller entry point for the UCS 命名翻译器 desktop app."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    from ucs_namer.gui import run

    return run()


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
