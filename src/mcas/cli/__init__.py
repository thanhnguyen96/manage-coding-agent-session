from __future__ import annotations

import sys

from mcas.cli.commands.app_cmds import format_top_level_help
from mcas.cli.parser import build_parser
from mcas.config import get_default_port
from mcas.constants import VERSION
from mcas.errors import SyncError


def _open_default_ui() -> int:
    try:
        from mcas.desktop.launcher import open_ui
    except ImportError as exc:
        raise SyncError("Desktop launcher support is not installed.") from exc
    print(open_ui(get_default_port(), False))
    return 0


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    args_list = list(sys.argv[1:] if argv is None else argv)

    try:
        if not args_list:
            return _open_default_ui()
        if args_list[0] in ("-h", "--help", "/?"):
            print(format_top_level_help())
            return 0
        if args_list[0] in ("-v", "--version"):
            print(VERSION)
            return 0

        parser = build_parser()
        args = parser.parse_args(args_list)
        return args.func(args)
    except SyncError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
