# Session storage layout and path bindings

Reference for `migrate-project-sessions`. Read this when you need the exact
on-disk layout, the rules that bind a session to a project, or rollback details.

## Where sessions live

Both extensions use VS Code's global storage plus an optional user-configured
override:

- Zoo Code default: `<user>/User/globalStorage/zoocodeorganization.zoo-code`
  (domain = publisher `ZooCodeOrganization` + name `zoo-code`).
- Roo Code default: `<user>/User/globalStorage/rooveterinaryinc.roo-cline`.
- Custom override: the setting `<section>.customStoragePath`
  (`zoo-code.customStoragePath` or `roo-cline.customStoragePath`). When set, that
  directory replaces the default as the base storage path for that extension.

`<user>` is `%APPDATA%/<App>` on Windows, `~/Library/Application Support/<App>` on
macOS, and `~/.config/<App>` on Linux, for each VS Code build (stable, insiders,
forks). The scripts scan all of these and also read any custom path from each
build's `settings.json`.

Roo Code additionally keeps its per-session index in VS Code's *global state
database*, not in the task directory:

- Database: `<user>/User/globalStorage/state.vscdb` — a SQLite file, table
  `ItemTable`.
- Key: `RooVeterinaryInc.roo-cline` — the extension's publisher and name with
  their original casing. Note the deliberate mismatch: the storage *directory* is
  named `rooveterinaryinc.roo-cline` (lowercased), but this database key preserves
  `RooVeterinaryInc`.
- Value: a JSON document whose `taskHistory` array holds one entry per session,
  including that session's `workspace`.
- A `customStoragePath` moves the task directories but never the database; the
  database always lives at `<user>/User/globalStorage/state.vscdb`.

The database is read **read-only** and never written: the import consults it only
to learn each session's `workspace` and metadata.

## Directory layout under a storage root

Zoo Code task directory:

```text
<storage>/
  tasks/<taskId>/
    history_item.json              # session metadata; contains `workspace`
    ui_messages.json               # conversation UI log (historical text)
    api_conversation_history.json  # model conversation history (historical text)
    task_metadata.json             # file-context tracking (absolute paths)
    checkpoints/.git/              # this task's shadow git repo
  checkpoints/<hash>/              # legacy workspace-level shadow repo (usually unused)
```

Roo Code task directory — there is **no `history_item.json`**; the session
metadata lives in the global state database instead:

```text
<storage>/
  tasks/<taskId>/
    ui_messages.json               # conversation UI log (historical text)
    api_conversation_history.json  # model conversation history (historical text)
    task_metadata.json             # file-context tracking (usually RELATIVE paths)
    checkpoints/.git/              # this task's shadow git repo
```

## How a session is bound to a project

### Zoo Code — `history_item.json` → `workspace`

The binding key, compared as an exact string. The session is shown in the sidebar
only when `item.workspace === cwd`, and the value is reloaded verbatim as the
task's working directory when the session resumes. A stale value means the session
both disappears from history and resumes against a dead path. The comparison is
strict (case- and separator-sensitive).

### Roo Code — the global state database → `taskHistory[].workspace`

Roo stores the same `workspace` value inside the global state database entry
described above. The Roo sidebar filters with the same rule, keeping only entries
whose `workspace` equals the current workspace by strict equality. There is no
per-task file carrying this value — the database entry is its only home, which is
why a Roo session cannot be located by walking task directories alone.

A Roo session is importable only when *both* of these exist:

- a database entry whose `workspace` matches the old path, and
- the matching task directory `tasks/<id>`.

Some database entries are stale — their task directory is gone. Those, and task
directories that have no database entry (so no `workspace` to match on), are
skipped and counted in the plan report.

### Checkpoints (both) — `checkpoints/.git/config` → `core.worktree`

An absolute path written when the shadow repo is created, pointing at the project
root. Restore compares it to the task's working directory and refuses to run on
mismatch with `Checkpoints can only be used in the original workspace`. Restore
then runs `git clean -fd` and `git reset --hard` in that worktree.

### File-context tracking (both) — `task_metadata.json` → `files_in_context[].path`

Used only for file-context/staleness tracking.

- Zoo records absolute paths grounded in the project root, so a move invalidates
  them.
- Roo usually records *relative* paths (relative to the project root), so a move
  leaves them correct; only the occasional absolute path needs translating.

## What the migration changes

Zoo sessions are edited in place:

- rewrite `workspace` to the new path;
- set the checkpoint `core.worktree` to the new path;
- translate any absolute `files_in_context[].path` prefix from the old path to the
  new one.

Roo sessions are imported into the Zoo store; the Roo original is never touched:

- copy the task directory into the Zoo store, never overwriting an existing Zoo
  task ID;
- **synthesize `history_item.json`** for the copy, because Roo never wrote one (see
  below);
- set the copied checkpoint `core.worktree` to the new path;
- translate any absolute `files_in_context[].path` prefix; relative paths are left
  as-is.

Conversation content files (`ui_messages.json`, `api_conversation_history.json`)
are copied verbatim: they embed historical `environment_details` from past turns,
which does not affect the current turn.

## Synthesized `history_item.json` for imported Roo sessions

Zoo's history store requires each task directory to contain a `history_item.json`
whose `id` equals the directory name. Roo never wrote one, so the importer builds
it from the database entry:

- `id` — the task directory name (must match exactly, or Zoo rejects the session).
- `workspace` — the new project path: the canonical string already used by an
  existing Zoo session for the new path when one exists, otherwise the value the
  caller provided.
- `task`, `ts`, `tokensIn`, `tokensOut`, `cacheWrites`, `cacheReads`, `totalCost`,
  `size`, `mode` — copied from the database entry when present.
- delegation fields (`rootTaskId`, `parentTaskId`, `status`, `delegatedToId`,
  `childIds`, `awaitingChildId`, `completedByChildId`, `completionResultSummary`)
  — copied when present, so subtree relationships survive the import.
- `number` — Roo did not always persist it; when absent it is assigned from the
  session's rank by start time within the same project.

The synthesized document must satisfy Zoo's history schema and carry an `id` equal
to the directory name before the import is accepted.

## Backup and rollback model

- Zoo tasks are edited in place; before editing, the touched files are copied to
  `tasks/_migration_backup_<timestamp>/zoo/<taskId>/` alongside a `backup.json`
  manifest recording the original `workspace` and `core.worktree`.
- Roo tasks are read-only on the Roo side. The import creates new directories in
  the Zoo store, so nothing needs backing up; the manifest records which
  directories were created so rollback can delete them.
- `restore_backup.py` copies the backed-up Zoo files back and deletes the imported
  Roo copies (unless `--keep-roo`).

The backup directory is prefixed with `_`, which Zoo Code's store ignores during
task scans, so it is never listed as a session.

## Close the old project's VS Code window first

If a window still has the *old* project path open, close it before migrating. That
window keeps those sessions in an in-memory cache and holds per-file advisory
locks, so editing the files externally can conflict with a lock or be overwritten
by a later write. The extension watches its task files and reconciles by mtime
(and reads them fresh on startup), so once the old window is closed the edits are
picked up cleanly. The window for the *new* path (where this skill runs) can stay
open, because the sessions being edited belong to the old project.

Because the import reads Roo's database read-only and only writes new directories
into the Zoo store, a running VS Code instance holding those files is not
disturbed.
