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
  directory replaces the default as the base storage path.

`<user>` is `%APPDATA%/<App>` on Windows, `~/Library/Application Support/<App>` on
macOS, and `~/.config/<App>` on Linux, for each VS Code build (stable, insiders,
forks). The scripts scan all of these and also read any custom path from each
build's `settings.json`.

Directory layout under a storage root:

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

## The three path bindings

1. **`history_item.json` → `workspace`** (the binding key, exact string match).
   - Shown in the sidebar only when `item.workspace === cwd`.
   - Loaded verbatim as the task's `cwd` when the session resumes. A stale value
     means the session both disappears from history and resumes against a dead
     path.
   - Compared with `===`, i.e. case- and separator-sensitive — not the
     fuzzy `arePathsEqual` used elsewhere.

2. **`checkpoints/.git/config` → `core.worktree`** (absolute path).
   - Written when the shadow repo is created, pointing at the project root.
   - Restore compares it to the task's `cwd` and throws
     `Checkpoints can only be used in the original workspace` on mismatch.
   - Restore then runs `git clean -fd` + `git reset --hard` in that worktree.

3. **`task_metadata.json` → `files_in_context[].path`** (absolute paths).
   - Used only for file-context/staleness tracking. Not fatal if stale, but kept
     consistent for cleanliness.

Conversation content files are left untouched: they embed historical
`environment_details` from past turns, which does not affect the current turn.

## What the migration changes

Per matching task: rewrite `workspace`, set `core.worktree`, translate
`files_in_context[].path` prefixes. Nothing else.

## Backup and rollback model

- Zoo tasks are edited in place; before editing, the touched files are copied to
  `tasks/_migration_backup_<timestamp>/zoo/<taskId>/` alongside a `backup.json`
  manifest recording the original `workspace` and `core.worktree`.
- Roo tasks are copied into the Zoo store (originals untouched), so no backup is
  needed; the manifest records which directories were created.
- `restore_backup.py` copies the backed-up files back and deletes the imported
  Roo copies (unless `--keep-roo`).

The backup directory is prefixed with `_`, which Zoo Code's store ignores during
task scans, so it is never listed as a session.

## Why VS Code need not be closed

The VS Code window open at migration time belongs to the *new* project. The
sessions being edited belong to the *old* project, which is not open, so the
extension's in-memory cache and file lock do not touch those files. On next
startup (or within a few minutes, via the store's file watcher) the edited
metadata is read from disk.
