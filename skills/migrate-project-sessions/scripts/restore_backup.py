#!/usr/bin/env python3
"""Roll back a migration performed by `apply_migration.py` using its backup manifest.

  Zoo sessions     The backed-up `history_item.json`, `task_metadata.json` and
                   checkpoint `config` are copied back over their originals,
                   restoring the previous `workspace` and `core.worktree`.
  Roo sessions     The task directories that were copied into the Zoo store are
                   deleted (the Roo originals were never touched). Pass
                   `--keep-roo` to leave the copies in place.

Use `--list` to inspect what a manifest contains without changing anything.
Nothing is restored without `--execute`.

Usage:
  python restore_backup.py --backup-dir <dir> [--keep-roo] [--execute]
  python restore_backup.py --manifest <dir>/backup.json [--keep-roo] [--execute]
  python restore_backup.py --backup-dir <dir> --list
"""

from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from _common import emit


def load_manifest(backup_dir: Path | None, manifest_path: Path | None) -> tuple[dict, Path]:
    if manifest_path is None:
        if backup_dir is None:
            raise SystemExit("Provide --backup-dir or --manifest")
        manifest_path = backup_dir / "backup.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    return manifest, manifest_path.parent


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--backup-dir", help="Directory created by apply_migration.py (contains backup.json)")
    parser.add_argument("--manifest", help="Explicit path to backup.json")
    parser.add_argument("--keep-roo", action="store_true", help="Do not delete imported Roo copies")
    parser.add_argument("--list", action="store_true", help="Only list the manifest contents")
    parser.add_argument("--execute", action="store_true", help="Actually restore")
    args = parser.parse_args()

    manifest, backup_dir = load_manifest(
        Path(args.backup_dir) if args.backup_dir else None,
        Path(args.manifest) if args.manifest else None,
    )

    summary = {
        "manifest": str(backup_dir / "backup.json"),
        "old_path": manifest.get("old_path"),
        "new_path": manifest.get("new_path"),
        "zoo_modifications": [m.get("task_id") for m in manifest.get("zoo_modifications", [])],
        "roo_imported": [r.get("task_id") for r in manifest.get("roo_imported", [])],
    }

    if args.list:
        emit(summary)
        return 0

    restore_actions: list[dict] = []
    roo_actions: list[dict] = []

    if args.execute:
        # Restore Zoo files.
        for entry in manifest.get("zoo_modifications", []):
            task_id = entry.get("task_id")
            task_dir = Path(entry.get("task_dir", ""))
            src = backup_dir / "zoo" / str(task_id)
            if not task_dir.is_dir():
                continue
            for name, target in (
                ("history_item.json", task_dir / "history_item.json"),
                ("task_metadata.json", task_dir / "task_metadata.json"),
            ):
                source = src / name
                if source.is_file():
                    shutil.copy2(source, target)
                    restore_actions.append({"task_id": task_id, "restored": str(target)})
            ckpt = src / "checkpoints.config"
            if ckpt.is_file():
                target = task_dir / "checkpoints" / ".git" / "config"
                if target.parent.is_dir():
                    shutil.copy2(ckpt, target)
                    restore_actions.append({"task_id": task_id, "restored": str(target)})

        # Remove imported Roo copies unless asked to keep them.
        if not args.keep_roo:
            for entry in manifest.get("roo_imported", []):
                if not entry.get("created"):
                    continue
                dest = Path(entry.get("dest_task_dir", ""))
                if dest.is_dir():
                    shutil.rmtree(dest)
                    roo_actions.append({"task_id": entry.get("task_id"), "removed": str(dest)})

    emit({**summary, "execute": args.execute, "keep_roo": args.keep_roo, "restored": restore_actions, "roo_removed": roo_actions})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
