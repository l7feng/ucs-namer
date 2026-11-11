"""Console entry: `ucs-namer-cmd.exe mcp` starts the MCP server, anything else is the CLI."""

from __future__ import annotations

import multiprocessing
import sys


def main() -> int:
    argv = sys.argv[1:]
    if argv and argv[0].lower() == "mcp":
        from ucs_namer.mcp_server import main as mcp_main

        return mcp_main(argv[1:])
    from ucs_namer.cli import main as cli_main

    return cli_main(argv)


if __name__ == "__main__":
    multiprocessing.freeze_support()
    sys.exit(main())
