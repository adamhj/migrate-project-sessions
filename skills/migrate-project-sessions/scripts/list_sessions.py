#!/usr/bin/env python3
"""List sessions matching an old path, with a preview of each first user message.

Use this when the user wants to migrate only *some* sessions: show the list so
they can pick which ones to include. The first user message is taken cheaply from
`ui_messages.json` (falling back to the `task` field of `history_item.json`), so
the whole log is never read.

Usage:
  python list_sessions.py --old-path <old> [--side {both,zoo,roo}] [--limit-text N]

Output (JSON on stdout):
  {
    "old_path": "...",
    "zoo": [ {"task_id", "first_message", "ts", "has_checkpoints", "message_source"} ],
    "roo": [ ... ]
  }
"""

from __future__ import annotations

import argparse
from pathlib import Path

from _common import (
    emit,
    first_user_message,
    read_history_item,
    resolve_plan,
    truncate,
)


def describe(task: dict, limit_text: int) -> dict:
    task_dir = Path(task["task_dir"])
    message = first_user_message(task_dir)
    source = "ui_messages"
    if not message:
        item = read_history_item(task_dir) or {}
        fallback = item.get("task")
        message = fallback if isinstance(fallback, str) and fallback.strip() else None
        source = "history_item.task" if message else "none"
    return {
        "task_id": task["task_id"],
        "first_message": truncate(message, limit_text),
        "ts": task.get("ts"),
        "has_checkpoints": task.get("has_checkpoints", False),
        "message_source": source,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-path", required=True, help="Old project path whose sessions to list")
    parser.add_argument("--new-path", default="", help="Current project path (unused; accepted for symmetry)")
    parser.add_argument("--side", choices=["both", "zoo", "roo"], default="both")
    parser.add_argument("--limit-text", type=int, default=200, help="Max chars of the first message (0 = full)")
    args = parser.parse_args()

    plan = resolve_plan(args.old_path, args.new_path)

    zoo = [describe(t, args.limit_text) for t in plan["zoo_tasks"]] if args.side in ("both", "zoo") else []
    roo = [describe(t, args.limit_text) for t in plan["roo_tasks"]] if args.side in ("both", "roo") else []

    emit(
        {
            "old_path": args.old_path,
            "zoo_count": len(zoo),
            "roo_count": len(roo),
            "zoo": zoo,
            "roo": roo,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
