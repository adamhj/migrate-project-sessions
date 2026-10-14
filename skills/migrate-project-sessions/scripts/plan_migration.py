#!/usr/bin/env python3
"""Plan (dry-run) the migration of sessions from an old project path to the current one.

Given the old path (from the user or from `discover_sessions.py`) and the current
path, report exactly which Zoo and Roo tasks would be migrated and the exact
`workspace` string each would be rewritten to. Nothing is modified.

Pass `--ids` to plan a partial migration of only the listed session IDs.

Usage:
  python plan_migration.py --old-path <old> [--new-path <new>] [--ids id1,id2,...]

Output (JSON on stdout) includes the full plan from `resolve_plan` plus:
  - "new_workspace_source": where the exact target string came from
  - "warnings": human-oriented notes to raise with the user before migrating
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from _common import emit, normalize_path, resolve_plan


def build_warnings(plan: dict) -> list[str]:
    warnings: list[str] = []
    if plan.get("ids_missing"):
        warnings.append(
            "These requested session IDs were not found under the old path (ignored): "
            f"{', '.join(plan['ids_missing'])}"
        )
    if plan["new_workspace_source"] == "provided-path":
        warnings.append(
            "No existing session uses the current project path, so the workspace value will be "
            f"written verbatim as '{plan['new_workspace_value']}'. Confirm it matches what VS Code "
            "shows for this project (drive-letter case and separators), or the sessions may not appear."
        )
    if plan["zoo_count"] == 0 and plan["roo_count"] == 0:
        warnings.append("No sessions found for the old path in either store. Double-check the path.")
    if plan["old_normalized"] == plan["new_normalized"]:
        warnings.append("Old and new paths are identical; nothing to migrate.")
    if plan["roo_dest_collisions"]:
        warnings.append(
            "These Roo task IDs already exist in the Zoo store and will be skipped during import "
            f"(not overwritten): {', '.join(plan['roo_dest_collisions'])}"
        )
    if not Path(plan["old_path"]).exists():
        warnings.append("The old path no longer exists on disk (expected after a move; informational).")
    no_ckpt_zoo = [t["task_id"] for t in plan["zoo_tasks"] if not t["has_checkpoints"]]
    if no_ckpt_zoo:
        warnings.append(
            f"{len(no_ckpt_zoo)} Zoo task(s) have no checkpoint repository; only metadata will change."
        )
    return warnings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-path", required=True, help="Previous project path to migrate FROM")
    parser.add_argument("--new-path", default=os.getcwd(), help="Current project path (default: cwd)")
    parser.add_argument("--ids", help="Comma-separated session IDs to migrate (omit for all)")
    args = parser.parse_args()

    ids = {i.strip() for i in args.ids.split(",") if i.strip()} if args.ids else None
    plan = resolve_plan(args.old_path, args.new_path, ids)
    plan["warnings"] = build_warnings(plan)
    emit(plan)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
