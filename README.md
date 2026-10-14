# migrate-project-sessions

A skill that migrates Zoo Code / Roo Code AI session history — conversation logs
and checkpoints — to a project's new location.

[中文说明](README.zh-CN.md)

When a project folder is moved or renamed, its past sessions stop appearing in the
task history and can no longer be resumed correctly, because each session is bound
to a project by a path string. This project provides an AI skill that re-points
those sessions to the project's new path.

## Background: why this is not a simple file copy

Sessions are **not** stored per project. Every project's sessions live together in
one global store, and each session is bound to a project by an exact string in its
metadata:

- `tasks/<taskId>/history_item.json` → the `workspace` field. The webview and the
  backend compare it with strict equality (case- and separator-sensitive), and it
  is reloaded verbatim as the task's working directory when the session resumes.
  If it still points at the old path, the session neither appears in history nor
  resumes against the right directory.
- `tasks/<taskId>/checkpoints/.git/config` → `core.worktree`, an absolute path.
  Restoring a checkpoint refuses to run if it does not match the task's working
  directory.

So the work is: **rewrite the `workspace` binding of each matching session, and
re-point its checkpoint worktree** to the new path. The conversation content files
(`ui_messages.json`, `api_conversation_history.json`) are historical text and are
left unchanged.

## Repository structure

```text
.
├── README.md
├── README.zh-CN.md
└── skills/
    └── migrate-project-sessions/
        ├── SKILL.md                          # Skill entry point: workflow and trigger description
        ├── references/
        │   ├── storage-layout.md             # Storage layout, path bindings, backup/rollback model
        │   └── session-log-format.md         # Session log file format reference
        └── scripts/
            ├── _common.py                    # Shared logic: root discovery, task scan, atomic write, path/metadata rewrite, planning
            ├── discover_sessions.py          # Scan Zoo+Roo, aggregate workspace values, rank old-path candidates
            ├── list_sessions.py              # List matching sessions with first-message previews
            ├── plan_migration.py             # Produce a migration plan (dry-run); supports --ids
            ├── apply_migration.py            # Apply the migration (pinpoint backup + edits); needs --execute
            ├── restore_backup.py             # Roll back using the backup manifest
            └── read_session.py               # Safe, selective log reading (never read a whole large log)
```

## Installation

Place the skill directory under the skills directory used by Zoo Code / Roo Code.
The skill follows the standard structure (`SKILL.md` plus optional `scripts/` and
`references/`), so copying `skills/migrate-project-sessions/` into the target
skills directory is enough:

```powershell
Copy-Item -Recurse skills\migrate-project-sessions "$env:USERPROFILE\.roo\skills\"
```

Once installed, the skill is triggered when the user indicates intent such as
"I moved/renamed my project and want to migrate the session history".

## Usage

### Via the AI (recommended)

Open a session under the project's new path and tell the agent you need to migrate
the session history. The skill guides the process (see `SKILL.md`):

1. Treat the current project path as the migration target.
2. Determine the old path: the user provides it, or a script scans every recorded
   path and ranks candidates by similarity for the user to pick.
3. Produce a migration plan and report the matched Zoo / Roo session counts and
   the `workspace` value each will be rewritten to.
4. Confirm the scope (Zoo / Roo / both; default both).
5. For a partial migration, first list sessions with their first user message and
   let the user select.
6. Apply the migration; roll back from the backup if needed.

### Running the scripts directly

The scripts are pure filesystem operations and run from any terminal (their output
is JSON). Note that `scripts/` is the directory containing them.

```powershell
# 1) Discover candidate old paths (when the old path is unknown)
python skills/migrate-project-sessions/scripts/discover_sessions.py --new-path "d:\Develop\AI\proj"

# 2) List the old path's sessions with first-message previews (to pick a subset)
python skills/migrate-project-sessions/scripts/list_sessions.py --old-path "d:\old\proj"

# 3) Produce a migration plan (dry-run; modifies nothing)
python skills/migrate-project-sessions/scripts/plan_migration.py --old-path "d:\old\proj" --new-path "d:\Develop\AI\proj"

# 4) Apply the migration (--ids optional, to migrate only selected sessions)
python skills/migrate-project-sessions/scripts/apply_migration.py --old-path "d:\old\proj" --new-path "d:\Develop\AI\proj" --mode both --execute

# 5) Roll back if needed
python skills/migrate-project-sessions/scripts/restore_backup.py --backup-dir "<backup dir>" --execute
```

Key parameters:

- `--old-path` / `--new-path`: old and new project paths; `--new-path` defaults to
  the current working directory.
- `--mode {zoo,roo,both}`: which side to migrate; default `both`.
- `--ids id1,id2,...`: migrate only the listed sessions; omit to migrate all
  matches.
- `--execute`: `apply_migration.py` / `restore_backup.py` only write when this is
  passed; otherwise they preview.
- `--side {both,zoo,roo}`: filter for `list_sessions.py`.
- `--parts` / `--first N` / `--last N` / `--grep <regex>` / `--get IDX` /
  `--file {ui,api}`: reading modes for `read_session.py`.

## Migration behavior and safety

- **Zoo sessions** are edited in place. Before editing, the files touched
  (`history_item.json`, `task_metadata.json`, and the checkpoint `config`) are
  backed up under `tasks/_migration_backup_<timestamp>/`, together with the
  original `workspace` and `core.worktree` values. The directory name starts with
  `_`, which Zoo Code ignores during task scans, so it never appears as a task.
- **Roo sessions** are copied into the Zoo store and the copy is edited; the
  originals are untouched, so they need no backup. Existing Zoo task IDs are never
  overwritten.
- **No need to close VS Code**: the sessions being migrated belong to the old
  project (not open), so they are not held by the current window's in-memory cache
  or file lock.
- **Atomic writes**: JSON files are written via a temp file plus an atomic rename;
  checkpoints are updated with `git config` when git is available, otherwise by
  editing `.git/config` directly.

## Known limitations

- **Exact match**: a session appears only when its `workspace` string equals the
  project's character for character (including drive-letter case and separators).
  The scripts prefer the exact string already used by existing sessions for the
  new path, which VS Code itself produced.
- **Checkpoint restore is destructive**: restore runs `git clean -fd` and
  `git reset --hard`, discarding untracked files in the new location.
- **Nested git repositories**: if a nested `.git` exists at the new location,
  checkpoints are disabled.
- **Same-machine moves only**: to move to another machine, first copy the relevant
  `tasks/<taskId>/` directories, then run the same steps there.

## Requirements

- Python 3.8+.
- Git is optional: with git, checkpoint worktrees are updated via `git config`;
  without it, the config file is edited directly.
