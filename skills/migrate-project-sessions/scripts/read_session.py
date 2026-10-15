#!/usr/bin/env python3
"""Read part of a session's log without loading the whole (potentially huge) file.

Session logs can be very large (many MB, thousands of messages). Reading one in
full pollutes context and is slow. This script extracts only what you ask for and
prints a bounded JSON result. Use it to help the user decide whether a specific
session should be migrated when the first user message is not enough.

Modes (choose one):
  --parts            Structural overview: message count, roles, say/ask kinds,
                     and a per-index preview (no full text loaded).
  --first N          First N messages, text-limited.
  --last N           Last N messages, text-limited.
  --grep PATTERN     Indices + previews of messages whose text matches (regex).
  --get IDX          The single message at index IDX (text-limited).

Options:
  --file {ui,api}    Which log to read (default: ui).
  --limit-text N     Max chars of each text field (default 300; 0 = full).

Usage:
  python read_session.py --task-dir <dir> --parts
  python read_session.py --task-id <id> --old-path <old> --grep "login bug"
  python read_session.py --task-dir <dir> --first 5
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

from _common import (
    ROO_CONFIG_SECTION,
    ROO_EXTENSION_DOMAIN,
    ZOO_CONFIG_SECTION,
    ZOO_EXTENSION_DOMAIN,
    collect_tasks,
    emit,
    find_storage_roots,
    normalize_path,
    read_api_messages,
    read_roo_task_history,
    read_ui_messages,
    tasks_root,
    truncate,
)


def locate_task_dir(task_id: str, old_path: str) -> Path | None:
    """Find a task directory by ID across both stores (optionally restricted by path).

    Zoo tasks are located by scanning their `history_item.json`. Roo tasks are
    located through the global-state database (their only source of `workspace`),
    falling back to a direct directory check when no entry records a workspace.
    """
    want = normalize_path(old_path) if old_path else ""

    for root in find_storage_roots(ZOO_EXTENSION_DOMAIN, ZOO_CONFIG_SECTION):
        for task_dir, item in collect_tasks(tasks_root(root)):
            tid = item.get("id") or task_dir.name
            if tid != task_id:
                continue
            if want and normalize_path(item.get("workspace") or "") != want:
                continue
            return task_dir

    roo_entries = {entry["id"]: entry for entry in read_roo_task_history()}
    for root in find_storage_roots(ROO_EXTENSION_DOMAIN, ROO_CONFIG_SECTION):
        task_dir = tasks_root(root) / task_id
        if not task_dir.is_dir():
            continue
        entry = roo_entries.get(task_id)
        if want:
            # A matching entry with a different workspace is not the one we want.
            if entry is not None and normalize_path(entry.get("workspace") or "") != want:
                continue
            if entry is None:
                continue
        return task_dir
    return None


def load_messages(task_dir: Path, which: str):
    return read_api_messages(task_dir) if which == "api" else read_ui_messages(task_dir)


def message_kind(message: dict) -> dict:
    if message.get("type") == "say":
        return {"type": "say", "say": message.get("say")}
    if message.get("type") == "ask":
        return {"type": "ask", "ask": message.get("ask")}
    if "role" in message:
        return {"role": message.get("role")}
    return {"type": message.get("type")}


def preview(message: dict, limit_text: int) -> dict:
    out = dict(message_kind(message))
    text = message.get("text")
    if isinstance(text, str):
        out["text"] = truncate(text, limit_text)
    content = message.get("content")
    if isinstance(content, str):
        out["content"] = truncate(content, limit_text)
    elif isinstance(content, list):
        out["content_items"] = len(content)
    return out


def find_text(message: dict) -> str:
    """Flatten a message's textual fields for regex matching."""
    parts = []
    for key in ("text", "content"):
        value = message.get(key)
        if isinstance(value, str):
            parts.append(value)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict) and isinstance(item.get("text"), str):
                    parts.append(item["text"])
    return "\n".join(parts)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-dir", help="Absolute path to the task directory")
    parser.add_argument("--task-id", help="Task ID (resolved across stores)")
    parser.add_argument("--old-path", default="", help="Restrict --task-id lookup to this workspace")
    parser.add_argument("--file", choices=["ui", "api"], default="ui")
    parser.add_argument("--parts", action="store_true", help="Structural overview only")
    parser.add_argument("--first", type=int, help="First N messages")
    parser.add_argument("--last", type=int, help="Last N messages")
    parser.add_argument("--grep", help="Regex to match against message text")
    parser.add_argument("--get", type=int, help="Single message index")
    parser.add_argument("--limit-text", type=int, default=300, help="Max chars per text field (0 = full)")
    args = parser.parse_args()

    task_dir = Path(args.task_dir) if args.task_dir else None
    if task_dir is None and args.task_id:
        task_dir = locate_task_dir(args.task_id, args.old_path)
    if task_dir is None:
        emit({"error": "Could not locate the task directory. Pass --task-dir or a resolvable --task-id."})
        return 2

    messages = load_messages(task_dir, args.file)
    if messages is None:
        emit({"error": f"No readable {'api_conversation_history.json' if args.file == 'api' else 'ui_messages.json'} in {task_dir}"})
        return 2

    total = len(messages)
    if args.parts or (args.first is None and args.last is None and args.grep is None and args.get is None):
        index = []
        for i, message in enumerate(messages):
            if isinstance(message, dict):
                entry = dict(message_kind(message))
                entry["index"] = i
                text = find_text(message)
                entry["preview"] = truncate(text, 80)
                entry["len"] = len(text)
                index.append(entry)
        emit({"task_dir": str(task_dir), "file": args.file, "message_count": total, "index": index})
        return 0

    if args.get is not None:
        if 0 <= args.get < total and isinstance(messages[args.get], dict):
            emit({"task_dir": str(task_dir), "message": {**preview(messages[args.get], args.limit_text), "index": args.get}})
        else:
            emit({"error": f"Index {args.get} out of range (0..{total - 1})"})
            return 2
        return 0

    if args.first is not None:
        selected = [(i, messages[i]) for i in range(min(args.first, total))]
    elif args.last is not None:
        start = max(0, total - args.last)
        selected = [(i, messages[i]) for i in range(start, total)]
    else:
        pattern = re.compile(args.grep, re.IGNORECASE)
        selected = [(i, m) for i, m in enumerate(messages) if isinstance(m, dict) and pattern.search(find_text(m))]

    results = [{**preview(m, args.limit_text), "index": i} for i, m in selected if isinstance(m, dict)]
    emit({"task_dir": str(task_dir), "file": args.file, "message_count": total, "matched": len(results), "messages": results})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
