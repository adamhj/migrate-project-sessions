#!/usr/bin/env python3
"""Discover session records whose `workspace` may be the moved project's old path.

When the user cannot recall the previous path, we read every `workspace` value
recorded across both the Zoo and Roo task stores and rank the ones most likely to
belong to the current project (by path similarity and matching folder name). The
agent then presents the shortlist to the user to pick from.

This script only reads; it never modifies anything.

Usage:
  python discover_sessions.py [--new-path <current project path>] [--top N] [--json]

Output (JSON on stdout):
  {
    "new_path": "...",
    "zoo_roots": [...], "roo_roots": [...],
    "candidates": [ {"workspace": "...", "zoo_count": N, "roo_count": N,
                     "similarity": 0.0-1.0, "basename_match": true, "exists": bool} ],
    "all_workspaces": [ {"workspace": "...", "zoo_count": N, "roo_count": N} ]
  }
"""

from __future__ import annotations

import argparse
import difflib
import os
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
    path_basename,
    read_roo_task_history,
    tasks_root,
)


def aggregate(roots: list[Path], label: str) -> dict[str, dict]:
    stats: dict[str, dict] = {}
    for root in roots:
        for _task_dir, item in collect_tasks(tasks_root(root)):
            value = item.get("workspace")
            if not isinstance(value, str) or not value:
                continue
            bucket = stats.setdefault(value, {"zoo_count": 0, "roo_count": 0})
            bucket[label] += 1
    return stats


def aggregate_roo() -> dict[str, dict]:
    """Bucket Roo sessions by `workspace`, sourced from the global-state database.

    Roo task directories carry no `workspace`, so discovery reads every recorded
    value from the database instead of scanning directories. Ranking only needs
    the recorded strings and their counts; a directory need not exist yet.
    """
    stats: dict[str, dict] = {}
    for entry in read_roo_task_history():
        value = entry.get("workspace")
        if not isinstance(value, str) or not value:
            continue
        bucket = stats.setdefault(value, {"zoo_count": 0, "roo_count": 0})
        bucket["roo_count"] += 1
    return stats


def similarity(new_norm: str, candidate_norm: str) -> float:
    """Blend whole-string and folder-name similarity into a single score.

    Folder-name equality matters because a project that moved between unrelated
    parents (e.g. `D:/work/foo` -> `D:/archive/foo`) still shares a basename;
    whereas a same-parent rename shares the parent. Blending avoids over-trusting
    either signal.
    """
    if not new_norm or not candidate_norm:
        return 0.0
    ratio = difflib.SequenceMatcher(None, new_norm, candidate_norm).ratio()
    name_ratio = difflib.SequenceMatcher(
        None, path_basename(new_norm), path_basename(candidate_norm)
    ).ratio()
    return round(0.6 * ratio + 0.4 * name_ratio, 4)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--new-path", default=os.getcwd(), help="Current project path (default: cwd)")
    parser.add_argument("--top", type=int, default=15, help="Max candidates to return")
    args = parser.parse_args()

    new_norm = normalize_path(args.new_path)
    zoo_roots = find_storage_roots(ZOO_EXTENSION_DOMAIN, ZOO_CONFIG_SECTION)
    roo_roots = find_storage_roots(ROO_EXTENSION_DOMAIN, ROO_CONFIG_SECTION)

    zoo_stats = aggregate(zoo_roots, "zoo_count")
    roo_stats = aggregate_roo()
    combined: dict[str, dict] = {}
    for value, count in zoo_stats.items():
        combined.setdefault(value, {"zoo_count": 0, "roo_count": 0})["zoo_count"] = count["zoo_count"]
    for value, count in roo_stats.items():
        combined.setdefault(value, {"zoo_count": 0, "roo_count": 0})["roo_count"] = count["roo_count"]

    candidates = []
    for value, counts in combined.items():
        value_norm = normalize_path(value)
        if value_norm == new_norm:
            continue  # the project's own current records; not an old path
        score = similarity(new_norm, value_norm)
        candidates.append(
            {
                "workspace": value,
                "zoo_count": counts["zoo_count"],
                "roo_count": counts["roo_count"],
                "total": counts["zoo_count"] + counts["roo_count"],
                "similarity": score,
                "basename_match": path_basename(value_norm) == path_basename(new_norm),
                "exists": Path(value).exists(),
            }
        )
    candidates.sort(key=lambda c: (c["similarity"], c["total"]), reverse=True)

    all_workspaces = [
        {"workspace": value, "zoo_count": c["zoo_count"], "roo_count": c["roo_count"]}
        for value, c in sorted(combined.items(), key=lambda kv: kv[1]["zoo_count"] + kv[1]["roo_count"], reverse=True)
    ]

    emit(
        {
            "new_path": args.new_path,
            "new_normalized": new_norm,
            "zoo_roots": [str(r) for r in zoo_roots],
            "roo_roots": [str(r) for r in roo_roots],
            "candidates": candidates[: args.top],
            "all_workspaces": all_workspaces,
        }
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
