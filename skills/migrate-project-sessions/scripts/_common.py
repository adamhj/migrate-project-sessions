#!/usr/bin/env python3
"""Shared helpers for the migrate-project-sessions skill scripts.

These scripts operate directly on the on-disk session stores of the Zoo Code and
Roo Code VS Code extensions. The behaviour mirrors the extension's own rules so
that edited files are recognised after restart:

  - A session is bound to a project by the exact `workspace` string stored in
    `tasks/<taskId>/history_item.json` (see packages/types/src/history.ts). The
    webview and backend compare it with `===`, and the path is reloaded verbatim
    as the task's cwd when the session is resumed (ClineProvider.ts).
  - Checkpoints for a task live in `tasks/<taskId>/checkpoints` and are bound to
    an absolute path via that shadow git repo's `core.worktree`
    (ShadowCheckpointService.ts). Restore compares it against task.cwd and
    refuses to run if they differ.
  - `tasks/<taskId>/task_metadata.json` stores absolute file paths used only for
    file-context tracking.

Nothing here talks to VS Code; it is pure filesystem manipulation so it can run
from a terminal. Steps that only need to *report* are safe under dry-run; only
`apply_migration.py --execute` mutates anything.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

# Storage identifiers, matching the extension package metadata.
# Zoo: Package.publisher + Package.name == "ZooCodeOrganization" + "zoo-code".
# Roo: the fixed legacy domain the importer reads from.
ZOO_EXTENSION_DOMAIN = "zoocodeorganization.zoo-code"
ROO_EXTENSION_DOMAIN = "rooveterinaryinc.roo-cline"
ZOO_CONFIG_SECTION = "zoo-code"
ROO_CONFIG_SECTION = "roo-cline"

# File/directory names inside a task directory.
TASKS_DIRNAME = "tasks"
HISTORY_ITEM = "history_item.json"
TASK_METADATA = "task_metadata.json"
UI_MESSAGES = "ui_messages.json"
API_HISTORY = "api_conversation_history.json"
CHECKPOINT_DIRNAME = "checkpoints"
GIT_DIRNAME = ".git"
GIT_CONFIG = "config"

# `say` kinds that carry the initial user prompt in ui_messages.json.
INITIAL_USER_SAY = ("text", "task")


def is_windows() -> bool:
    return sys.platform.startswith("win")


# ──────────────────────────── path normalisation ────────────────────────────


def normalize_path(value: str) -> str:
    """Normalise a path for comparison the way the migration needs it.

    Converts separators to `/`, collapses duplicate slashes, drops a trailing
    slash (except a drive root like `c:/`), and lowercases on Windows. This is
    used only for *matching*; the exact original string is what gets written.
    """
    if not value:
        return ""
    text = value.strip().replace("\\", "/")
    text = re.sub(r"/+", "/", text)
    if len(text) > 3 and text.endswith("/"):
        text = text[:-1]
    if is_windows():
        text = text.lower()
    return text


def path_basename(value: str) -> str:
    parts = [seg for seg in normalize_path(value).split("/") if seg]
    return parts[-1] if parts else ""


def dedupe_paths(paths: Iterable[Path]) -> list[Path]:
    seen: set[str] = set()
    result: list[Path] = []
    for p in paths:
        key = normalize_path(str(p))
        if key in seen:
            continue
        seen.add(key)
        result.append(p)
    return result


# ────────────────────── storage root discovery ──────────────────────


def candidate_user_dirs() -> list[Path]:
    """Return plausible `<AppName>/User` parent directories across VS Code builds.

    Covers stable/insiders and common forks by scanning every subdirectory of the
    known base locations that actually contains a `User/globalStorage` folder.
    """
    home = Path.home()
    bases: list[Path] = []
    appdata = os.environ.get("APPDATA")
    if appdata:
        bases.append(Path(appdata))
    if sys.platform == "darwin":
        bases.append(home / "Library" / "Application Support")
    bases.append(home / ".config")

    user_dirs: list[Path] = []
    for base in bases:
        if not base.is_dir():
            continue
        try:
            children = sorted(base.iterdir())
        except OSError:
            continue
        for child in children:
            if (child / "User" / "globalStorage").is_dir():
                user_dirs.append(child)
    return dedupe_paths(user_dirs)


def strip_jsonc(text: str) -> str:
    """Remove `//` and `/* */` comments from JSONC (VS Code settings.json)."""
    out: list[str] = []
    i = 0
    n = len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            j = text.find("\n", i)
            if j == -1:
                break
            i = j
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            j = text.find("*/", i + 2)
            if j == -1:
                break
            i = j + 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def read_custom_storage_path(settings_path: Path, config_section: str) -> str | None:
    """Read `<section>.customStoragePath` from a VS Code settings.json, if set."""
    if not settings_path.is_file():
        return None
    try:
        data = json.loads(strip_jsonc(settings_path.read_text(encoding="utf-8")))
    except Exception:
        return None
    section = data.get(config_section)
    if isinstance(section, dict):
        value = section.get("customStoragePath")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def find_storage_roots(extension_domain: str, config_section: str) -> list[Path]:
    """Locate every storage root an extension may use on this machine.

    A root is either the default `<userDir>/User/globalStorage/<domain>` (only
    included when it exists) or a `customStoragePath` configured for the
    extension. Custom paths may point anywhere, so they are trusted as-is.

    When `MIGRATE_SESSIONS_ZOO_ROOTS` / `MIGRATE_SESSIONS_ROO_ROOTS` is set
    (os.pathsep-separated), those paths are used verbatim instead of auto-detection.
    This covers portable/non-standard installs and lets the pipeline be exercised
    in isolation.
    """
    override_var = (
        "MIGRATE_SESSIONS_ZOO_ROOTS" if extension_domain == ZOO_EXTENSION_DOMAIN else "MIGRATE_SESSIONS_ROO_ROOTS"
    )
    override = os.environ.get(override_var)
    if override:
        return dedupe_paths([Path(p) for p in override.split(os.pathsep) if p and Path(p).is_dir()])

    roots: list[Path] = []
    for user_dir in candidate_user_dirs():
        gs = user_dir / "User" / "globalStorage"
        default_root = gs / extension_domain
        if default_root.is_dir():
            roots.append(default_root)
        custom = read_custom_storage_path(user_dir / "User" / "settings.json", config_section)
        if custom:
            custom_path = Path(custom)
            if custom_path.is_dir():
                roots.append(custom_path)
    return dedupe_paths(roots)


# ──────────────────────────── task directory IO ────────────────────────────


def tasks_root(storage_root: Path) -> Path:
    return storage_root / TASKS_DIRNAME


def iter_task_dirs(tasks: Path):
    """Yield visible task directories, skipping reserved `_`/`.` prefixed names.

    The store itself ignores these prefixes during reconciliation, so skipping
    them here avoids treating our own backup/staging directories as tasks.
    """
    if not tasks.is_dir():
        return
    for child in sorted(tasks.iterdir()):
        if not child.is_dir():
            continue
        if child.name.startswith("_") or child.name.startswith("."):
            continue
        yield child


def read_history_item(task_dir: Path) -> dict | None:
    path = task_dir / HISTORY_ITEM
    if not path.is_file():
        return None
    try:
        item = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return item if isinstance(item, dict) else None


def collect_tasks(tasks: Path) -> list[tuple[Path, dict]]:
    result: list[tuple[Path, dict]] = []
    for task_dir in iter_task_dirs(tasks):
        item = read_history_item(task_dir)
        if item:
            result.append((task_dir, item))
    return result


def task_id_of(task_dir: Path, item: dict) -> str:
    value = item.get("id")
    return value if isinstance(value, str) and value else task_dir.name


def _read_json_array(path: Path):
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    return data if isinstance(data, list) else None


def read_ui_messages(task_dir: Path):
    """Return `ui_messages.json` as a list, or None when absent/unreadable."""
    return _read_json_array(task_dir / UI_MESSAGES)


def read_api_messages(task_dir: Path):
    """Return `api_conversation_history.json` as a list, or None when absent/unreadable."""
    return _read_json_array(task_dir / API_HISTORY)


def first_user_message(task_dir: Path) -> str | None:
    """Return the session's initial user prompt from ui_messages.json, if present.

    The first interactive turn is a `say` with kind `text` or `task`; older
    sessions also recorded it as `say: "task"`. Reading this is cheaper and more
    precise than scanning the whole log.
    """
    for message in read_ui_messages(task_dir) or []:
        if not isinstance(message, dict):
            continue
        if message.get("type") == "say" and message.get("say") in INITIAL_USER_SAY:
            text = message.get("text")
            if isinstance(text, str) and text.strip():
                return text
    return None


def truncate(text, limit: int):
    """Trim a string to `limit` chars, appending a size hint. `limit<=0` disables."""
    if not isinstance(text, str) or limit <= 0 or len(text) <= limit:
        return text
    return text[:limit] + f"... [+{len(text) - limit} chars]"


# ───────────────────────────── atomic JSON write ─────────────────────────────


def write_json_atomic(path: Path, data) -> None:
    """Write JSON via a same-directory temp file + os.replace (atomic rename).

    Mirrors the extension's own safe-write intent so a crash mid-write cannot
    leave a half-written history_item.json behind.
    """
    tmp = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    text = json.dumps(data, ensure_ascii=False, indent=2)
    with open(tmp, "w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


# ─────────────────────────────── checkpoint IO ───────────────────────────────


def checkpoint_git_dir(task_dir: Path) -> Path | None:
    git_dir = task_dir / CHECKPOINT_DIRNAME / GIT_DIRNAME
    return git_dir if git_dir.is_dir() else None


def parse_git_core_worktree(config_text: str) -> str | None:
    section = None
    for raw in config_text.splitlines():
        line = raw.strip()
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1].strip().lower()
            continue
        if section == "core" and "=" in line:
            key, value = line.split("=", 1)
            if key.strip().lower() == "worktree":
                return value.strip()
    return None


def read_core_worktree(task_dir: Path) -> str | None:
    git_dir = checkpoint_git_dir(task_dir)
    if not git_dir:
        return None
    config = git_dir / GIT_CONFIG
    if not config.is_file():
        return None
    return parse_git_core_worktree(config.read_text(encoding="utf-8", errors="replace"))


def rewrite_git_core_worktree_text(config_path: Path, new_path: str) -> bool:
    """Fallback rewrite of `worktree = ...` in a git config, used when git is absent."""
    text = config_path.read_text(encoding="utf-8")
    lines = text.splitlines(keepends=True)
    out: list[str] = []
    section = None
    replaced = False
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1].strip().lower()
            out.append(line)
            continue
        if section == "core" and not replaced and "=" in stripped:
            key, _ = stripped.split("=", 1)
            if key.strip().lower() == "worktree":
                indent = line[: len(line) - len(line.lstrip())]
                newline = "\r\n" if line.endswith("\r\n") else "\n"
                out.append(f"{indent}worktree = {new_path}{newline}")
                replaced = True
                continue
        out.append(line)
    if not replaced:
        return False
    config_path.write_text("".join(out), encoding="utf-8")
    return True


def set_core_worktree(task_dir: Path, new_path: str) -> str:
    """Point a task's checkpoint shadow repo at `new_path`.

    Prefers `git config` (respects git's own parsing); falls back to a textual
    rewrite of `.git/config` when git is not on PATH. Returns the method used.
    """
    git_dir = checkpoint_git_dir(task_dir)
    if not git_dir:
        return "no-checkpoints"
    git = shutil.which("git")
    if git:
        try:
            result = subprocess.run(
                [git, "-C", str(task_dir / CHECKPOINT_DIRNAME), "config", "core.worktree", new_path],
                capture_output=True,
                text=True,
            )
            if result.returncode == 0:
                return "git"
        except OSError:
            pass
    config = git_dir / GIT_CONFIG
    if config.is_file() and rewrite_git_core_worktree_text(config, new_path):
        return "text"
    return "failed"


# ────────────────────────── task_metadata path rewrite ──────────────────────────


def rewrite_metadata_paths(metadata: dict, old_workspace: str, new_workspace: str) -> int:
    """Rewrite absolute `files_in_context[].path` values under the old project.

    Only the project-root prefix is translated; unknown/relative paths are left
    untouched. Separators in the value are matched case-insensitively (Windows)
    but the value's ORIGINAL separator style is preserved so the file is not
    gratuitously rewritten. Returns the number of entries changed.
    """
    old_norm = normalize_path(old_workspace)
    if not old_norm:
        return 0
    changed = 0
    entries = metadata.get("files_in_context")
    if not isinstance(entries, list):
        return 0
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        value = entry.get("path")
        if not isinstance(value, str) or not value:
            continue
        value_norm = normalize_path(value)
        if value_norm != old_norm and not value_norm.startswith(old_norm + "/"):
            continue
        remainder_norm = value_norm[len(old_norm):]  # "" or "/..." in normalised form
        if "\\" in value:
            new_base = new_workspace.replace("/", "\\")
            remainder = remainder_norm.replace("/", "\\")
        else:
            new_base = new_workspace.replace("\\", "/")
            remainder = remainder_norm
        entry["path"] = new_base + remainder
        changed += 1
    return changed


# ──────────────────────────────── planning ────────────────────────────────


def find_canonical_workspace(zoo_task_roots: list[Path], new_path: str, exclude_value: str) -> str | None:
    """Find the exact `workspace` string VS Code actually stores for `new_path`.

    The binding is an exact string comparison, so the safest value to write is
    one already produced by VS Code for this same project. We look for any
    existing Zoo history item whose normalised workspace equals the target and
    whose raw value is not the one we are replacing.
    """
    target = normalize_path(new_path)
    for root in zoo_task_roots:
        for _task_dir, item in collect_tasks(root):
            value = item.get("workspace")
            if not isinstance(value, str) or not value:
                continue
            if value == exclude_value:
                continue
            if normalize_path(value) == target:
                return value
    return None


def _task_entry(task_dir: Path, item: dict) -> dict:
    return {
        "task_id": task_id_of(task_dir, item),
        "task_dir": str(task_dir),
        "workspace": item.get("workspace") or "",
        "task": truncate(item.get("task"), 160),
        "ts": item.get("ts"),
        "has_checkpoints": checkpoint_git_dir(task_dir) is not None,
        "has_ui_messages": (task_dir / UI_MESSAGES).is_file(),
    }


def resolve_plan(old_path: str, new_path: str, ids: set[str] | None = None) -> dict:
    """Compute the migration plan: which tasks move, and to which workspace value.

    When `ids` is provided, only tasks whose task ID is in the set are included.
    The result reports which requested IDs matched and which did not, so a partial
    migration can be confirmed accurately.
    """
    zoo_roots = find_storage_roots(ZOO_EXTENSION_DOMAIN, ZOO_CONFIG_SECTION)
    roo_roots = find_storage_roots(ROO_EXTENSION_DOMAIN, ROO_CONFIG_SECTION)
    old_norm = normalize_path(old_path)
    new_norm = normalize_path(new_path)

    zoo_tasks: list[dict] = []
    zoo_ids: set[str] = set()
    for root in zoo_roots:
        for task_dir, item in collect_tasks(tasks_root(root)):
            zoo_ids.add(task_id_of(task_dir, item))
            if old_norm and normalize_path(item.get("workspace") or "") == old_norm:
                zoo_tasks.append(_task_entry(task_dir, item))

    roo_tasks: list[dict] = []
    for root in roo_roots:
        for task_dir, item in collect_tasks(tasks_root(root)):
            if old_norm and normalize_path(item.get("workspace") or "") == old_norm:
                roo_tasks.append(_task_entry(task_dir, item))

    matched_ids: list[str] = []
    missing_ids: list[str] = []
    if ids is not None:
        available = {t["task_id"] for t in zoo_tasks} | {t["task_id"] for t in roo_tasks}
        matched_ids = sorted(i for i in ids if i in available)
        missing_ids = sorted(i for i in ids if i not in available)
        zoo_tasks = [t for t in zoo_tasks if t["task_id"] in ids]
        roo_tasks = [t for t in roo_tasks if t["task_id"] in ids]

    canonical = find_canonical_workspace([tasks_root(r) for r in zoo_roots], new_path, old_path)
    new_workspace_value = canonical if canonical else new_path

    collisions = sorted(t["task_id"] for t in roo_tasks if t["task_id"] in zoo_ids)

    return {
        "old_path": old_path,
        "old_normalized": old_norm,
        "new_path": new_path,
        "new_normalized": new_norm,
        "new_workspace_value": new_workspace_value,
        "new_workspace_source": "existing-zoo-task" if canonical else "provided-path",
        "zoo_roots": [str(r) for r in zoo_roots],
        "roo_roots": [str(r) for r in roo_roots],
        "zoo_tasks": zoo_tasks,
        "roo_tasks": roo_tasks,
        "zoo_count": len(zoo_tasks),
        "roo_count": len(roo_tasks),
        "roo_dest_collisions": collisions,
        "ids_specified": ids is not None,
        "ids_matched": matched_ids,
        "ids_missing": missing_ids,
    }


# ──────────────────────────────── output helper ────────────────────────────────


def emit(data) -> None:
    """Print a JSON document to stdout for the calling agent to parse."""
    json.dump(data, sys.stdout, ensure_ascii=False, indent=2)
    sys.stdout.write("\n")
