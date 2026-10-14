#!/usr/bin/env python3
"""Apply the session migration (Zoo in-place edits + Roo import), with a backup.

Behaviour by target:

  Zoo sessions
    Edited in place. Before any edit, the exact files touched are copied into a
    timestamped backup directory together with the original `workspace` and
    `core.worktree` values, so the change can be rolled back precisely
    (`restore_backup.py`). Three things change: `history_item.json`'s `workspace`,
    the task's checkpoint `core.worktree`, and absolute paths in
    `task_metadata.json`.

  Roo sessions
    Copied into the Zoo store under a new task directory, then the *copy* is
    edited the same way. The Roo originals are never modified, so they need no
    backup. Existing Zoo task IDs are never overwritten.

Nothing is written without `--execute`. Without it, the script prints the exact
action list (dry-run) so the agent can confirm with the user first.

Usage:
  python apply_migration.py --old-path <old> [--new-path <new>] \
      [--mode {zoo,roo,both}] [--ids id1,id2,...] [--execute]
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
from pathlib import Path

from _common import (
    CHECKPOINT_DIRNAME,
    GIT_CONFIG,
    HISTORY_ITEM,
    ROO_CONFIG_SECTION,
    ROO_EXTENSION_DOMAIN,
    TASK_METADATA,
    ZOO_CONFIG_SECTION,
    ZOO_EXTENSION_DOMAIN,
    checkpoint_git_dir,
    emit,
    find_storage_roots,
    read_core_worktree,
    read_history_item,
    rewrite_metadata_paths,
    set_core_worktree,
    tasks_root,
    write_json_atomic,
)

BACKUP_PREFIX = "_migration_backup_"


def make_backup_dir(zoo_root: Path) -> Path:
    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    return tasks_root(zoo_root) / f"{BACKUP_PREFIX}{stamp}"


def backup_zoo_task(task_dir: Path, item: dict, backup_dir: Path) -> dict:
    """Copy the exact files we will touch and record their original bindings."""
    task_id = item.get("id") or task_dir.name
    entry = {
        "task_id": task_id,
        "task_dir": str(task_dir),
        "old_workspace": item.get("workspace"),
        "old_core_worktree": read_core_worktree(task_dir),
        "backed_up": [],
    }
    dest = backup_dir / "zoo" / task_id
    dest.mkdir(parents=True, exist_ok=True)

    history_src = task_dir / HISTORY_ITEM
    if history_src.is_file():
        shutil.copy2(history_src, dest / HISTORY_ITEM)
        entry["backed_up"].append(HISTORY_ITEM)

    meta_src = task_dir / TASK_METADATA
    if meta_src.is_file():
        shutil.copy2(meta_src, dest / TASK_METADATA)
        entry["backed_up"].append(TASK_METADATA)

    git_dir = checkpoint_git_dir(task_dir)
    if git_dir and (git_dir / GIT_CONFIG).is_file():
        shutil.copy2(git_dir / GIT_CONFIG, dest / "checkpoints.config")
        entry["backed_up"].append("checkpoints.config")

    return entry


def edit_task(task_dir: Path, new_workspace_value: str, old_path: str, actions: list[dict]) -> None:
    """Rewrite the workspace binding, checkpoint worktree and metadata paths."""
    item = read_history_item(task_dir)
    if item is not None:
        item["workspace"] = new_workspace_value
        write_json_atomic(task_dir / HISTORY_ITEM, item)
        actions.append({"file": str(task_dir / HISTORY_ITEM), "change": "workspace", "to": new_workspace_value})

    if checkpoint_git_dir(task_dir):
        method = set_core_worktree(task_dir, new_workspace_value)
        actions.append({"file": str(task_dir / CHECKPOINT_DIRNAME), "change": "core.worktree", "to": new_workspace_value, "method": method})

    meta_path = task_dir / TASK_METADATA
    if meta_path.is_file():
        try:
            import json

            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            metadata = None
        if isinstance(metadata, dict):
            changed = rewrite_metadata_paths(metadata, old_path, new_workspace_value)
            if changed:
                write_json_atomic(meta_path, metadata)
                actions.append({"file": str(meta_path), "change": "files_in_context paths", "count": changed})


def copy_roo_task(source_dir: Path, dest_dir: Path) -> None:
    shutil.copytree(source_dir, dest_dir, copy_function=shutil.copy2)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--old-path", required=True, help="Previous project path to migrate FROM")
    parser.add_argument("--new-path", default=os.getcwd(), help="Current project path (default: cwd)")
    parser.add_argument(
        "--mode",
        choices=["zoo", "roo", "both"],
        default="both",
        help="Which sessions to migrate (default: both)",
    )
    parser.add_argument("--ids", help="Comma-separated session IDs to migrate (omit for all)")
    parser.add_argument("--execute", action="store_true", help="Actually perform the migration")
    args = parser.parse_args()

    # Imported lazily so `--help` works even if _common changes shape.
    from _common import resolve_plan

    ids = {i.strip() for i in args.ids.split(",") if i.strip()} if args.ids else None
    plan = resolve_plan(args.old_path, args.new_path, ids)
    zoo_roots = [Path(p) for p in plan["zoo_roots"]]
    roo_roots = [Path(p) for p in plan["roo_roots"]]

    if not zoo_roots and args.mode in ("zoo", "roo", "both"):
        emit({"error": "No existing Zoo storage root found. Open this project in Zoo Code once, then retry."})
        return 2

    zoo_root = zoo_roots[0]
    new_value = plan["new_workspace_value"]
    do_zoo = args.mode in ("zoo", "both")
    do_roo = args.mode in ("roo", "both")

    result = {
        "execute": args.execute,
        "old_path": args.old_path,
        "new_path": args.new_path,
        "new_workspace_value": new_value,
        "new_workspace_source": plan["new_workspace_source"],
        "mode": args.mode,
        "ids_specified": plan.get("ids_specified", False),
        "ids_matched": plan.get("ids_matched", []),
        "ids_missing": plan.get("ids_missing", []),
        "zoo": {"selected": 0, "edited": 0, "actions": []},
        "roo": {"selected": 0, "imported": 0, "skipped_collisions": [], "actions": []},
        "backup_dir": None,
        "manifest": None,
    }

    selected_zoo = plan["zoo_tasks"] if do_zoo else []
    selected_roo = plan["roo_tasks"] if do_roo else []
    result["zoo"]["selected"] = len(selected_zoo)
    result["roo"]["selected"] = len(selected_roo)

    if not args.execute:
        # Dry-run: describe intended work without touching disk.
        result["zoo"]["actions"] = [
            {"task_id": t["task_id"], "would_edit": ["workspace", "core.worktree", "metadata"]}
            for t in selected_zoo
        ]
        result["roo"]["actions"] = [
            {"task_id": t["task_id"], "would_copy_to": str(tasks_root(zoo_root) / t["task_id"])}
            for t in selected_roo
        ]
        emit(result)
        return 0

    backup_dir = make_backup_dir(zoo_root)
    backup_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "version": 1,
        "created_at": dt.datetime.now().isoformat(),
        "old_path": args.old_path,
        "new_path": args.new_path,
        "new_workspace_value": new_value,
        "zoo_root": str(zoo_root),
        "zoo_modifications": [],
        "roo_imported": [],
    }

    # ── Zoo: back up first, then edit in place. ──────────────────────────────
    for task in selected_zoo:
        task_dir = Path(task["task_dir"])
        item = read_history_item(task_dir)
        if item is None:
            continue
        manifest["zoo_modifications"].append(backup_zoo_task(task_dir, item, backup_dir))

    for task in selected_zoo:
        task_dir = Path(task["task_dir"])
        before = len(result["zoo"]["actions"])
        edit_task(task_dir, new_value, args.old_path, result["zoo"]["actions"])
        if len(result["zoo"]["actions"]) > before:
            result["zoo"]["edited"] += 1

    # ── Roo: copy into Zoo store, then edit the copy. ────────────────────────
    for task in selected_roo:
        task_id = task["task_id"]
        source_dir = Path(task["task_dir"])
        dest_dir = tasks_root(zoo_root) / task_id
        if dest_dir.exists():
            result["roo"]["skipped_collisions"].append(task_id)
            continue
        copy_roo_task(source_dir, dest_dir)
        edit_task(dest_dir, new_value, args.old_path, result["roo"]["actions"])
        manifest["roo_imported"].append(
            {"task_id": task_id, "source_task_dir": str(source_dir), "dest_task_dir": str(dest_dir), "created": True}
        )
        result["roo"]["imported"] += 1

    manifest_path = backup_dir / "backup.json"
    write_json_atomic(manifest_path, manifest)
    result["backup_dir"] = str(backup_dir)
    result["manifest"] = str(manifest_path)
    emit(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
