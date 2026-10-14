---
name: migrate-project-sessions
description: Re-point Zoo Code (and Roo Code) AI session history — conversation logs and checkpoints — to a project that was moved or renamed on disk. Use whenever a user says they moved/renamed/relocated a project folder and want their old chat sessions, task history, or checkpoints to show up under the new location; whenever history is missing after a path change; whenever they ask to migrate/import/restore sessions "to the new path"; or whenever they ask where their Zoo/Roo sessions are stored and how to carry them across a move.
---

# Migrate Project Sessions

Move a project's Zoo Code (and Roo Code) session history to the project's new
location after the folder was moved or renamed.

## Why this is not a file copy

Sessions are **not** stored per project. Every project's sessions live together
in one global store, and each session is bound to a project by an exact string
in its metadata:

- `tasks/<taskId>/history_item.json` → the `workspace` field. The webview and
  backend compare it with `===` against the current workspace, and it is reloaded
  **verbatim** as the task's working directory when the session is resumed. If it
  still points at the old path, the session disappears from the sidebar *and*
  resumes against a dead path.
- `tasks/<taskId>/checkpoints/.git/config` → `core.worktree`, an absolute path.
  Restore refuses to run if it does not match the task's workspace.

So the work is: **re-label each matching session's `workspace`, and re-point its
checkpoint worktree** to the new path. The conversation content itself
(`ui_messages.json`, `api_conversation_history.json`) is historical text and is
left unchanged.

Read [`references/storage-layout.md`](references/storage-layout.md) when you need
the full directory layout, the exact path-binding rules, or rollback details.
Read [`references/session-log-format.md`](references/session-log-format.md) when
you need to inspect a session's contents — and heed its warning that the log
files are large and should never be read whole.

## Workflow

Run these steps in order. Use the `update_todo_list` tool to track them. Each
script prints JSON; parse it and translate the results into concise messages for
the user (counts and paths, not raw JSON).

### 1. Establish the current project path

The session's `workspace` value is compared as an exact string, so the value we
write must match what VS Code stores for this project. You usually have it: the
agent's working directory. Prefer passing the *current project root* explicitly
via `--new-path` when the working directory is a subfolder.

The scripts automatically prefer the exact `workspace` string already used by
existing sessions for this new path (that value was produced by VS Code itself,
so it is guaranteed to match); only when none exists do they fall back to the
provided path. `plan_migration.py` reports which one it used — surface that.

### 2. Determine the old path

Three routes, in order of preference:

1. **The user told you the old path.** Use it directly.
2. **Ask the user.** If they are unsure, go to route 3 first to offer a shortlist.
3. **Discover candidates.** When the user does not know the old path, scan every
   recorded `workspace` across both stores and rank the ones likely to be this
   project:

   ```bash
   python scripts/discover_sessions.py --new-path "<current project path>"
   ```

   Present the top candidates (each shows how many Zoo and Roo sessions it holds,
   a similarity score, whether the folder name matches, and whether the path still
   exists). Explaining the ranking helps the user trust it: same folder name at a
   different parent, or a sibling path, ranks highest. Let the user pick exactly
   one — never guess on their behalf.

### 3. Plan and report (dry-run)

```bash
python scripts/plan_migration.py --old-path "<old>" --new-path "<current>" [--ids <id1,id2,...>]
```

Report to the user, in plain language:

- How many **Zoo** sessions and how many **Roo** sessions match the old path.
- The exact `workspace` value each will be rewritten to, and where that value
  came from (an existing session for the new path → safest; or the provided path
  → ask the user to confirm it).
- Any `warnings` from the output (no matches, collisions with existing Zoo task
  IDs, tasks without checkpoints, old path no longer on disk, and — when `--ids`
  is used — any requested IDs that were not found).

### 4. Confirm scope

Ask the user whether to migrate Zoo, Roo, or both. **Default to both.** Present
the choices with the counts from step 3 so the decision is informed (for example:
"Found 12 Zoo sessions and 3 Roo sessions for `D:/work/foo`. Migrate both, or
only one side?"). Do not proceed until they confirm.

### 4b. Partial migration (optional)

The user may want only some sessions. If so, list them with a preview of each
first user message so the user can choose:

```bash
python list_sessions.py --old-path "<old>" [--side both|zoo|roo]
```

Present the list as a numbered selection (task ID + first-message preview + date).
Let the user mark which to include, then carry the chosen IDs forward to the plan
and apply steps via `--ids <id1,id2,...>`.

If the first message is not enough for the user to decide, inspect that session
with the targeted reader rather than reading the log wholesale:

```bash
python read_session.py --task-id <id> --old-path "<old>" --parts          # structural overview
python read_session.py --task-id <id> --old-path "<old>" --grep "keyword" # matched entries
python read_session.py --task-id <id> --old-path "<old>" --first 5        # first few messages
```

Explain to the user that the log files are large, which is why these scripts
extract only what is needed. See
[`references/session-log-format.md`](references/session-log-format.md) for the
file formats.

### 5. Apply

Repeat the run shown in step 3 as a dry-run preview (it prints the exact action
list), confirm it matches the agreed scope, then execute:

```bash
python scripts/apply_migration.py --old-path "<old>" --new-path "<current>" --mode both [--ids <id1,id2,...>] --execute
```

With `--ids`, only those sessions are migrated; omit it to migrate all matches.
The `--ids` list applies to both sides (Zoo and Roo) — the `--mode` flag still
selects which side.

What happens (and why it is safe):

- **Zoo sessions** are edited in place. Before editing, the exact files touched
  (the session's `history_item.json`, `task_metadata.json`, and its checkpoint
  `config`) are copied into a timestamped backup under
  `tasks/_migration_backup_<timestamp>/`, together with the original `workspace`
  and `core.worktree` values. The backup directory name starts with `_`, which
  Zoo Code ignores during task scans, so it never appears as a task.
- **Roo sessions** are copied into the Zoo store and the *copy* is edited. The
  Roo originals are untouched, so they need no backup. Existing Zoo task IDs are
  never overwritten.

Close any VS Code window that still has the *old* project path open, if one is
open: that window keeps those sessions in an in-memory cache and holds per-file
locks, so editing them externally can cause lock conflicts or be overwritten. The
window for the *new* path (where this skill runs) can stay open, because the
sessions being edited belong to the old project.

### 6. Verify

- Re-run `plan_migration.py` — the matched counts should now be 0 for the
  migrated sessions (they no longer carry the old path). For a partial migration,
  the remaining un-migrated matches will still appear; that is expected.
- Optionally list the new binding to confirm the sessions now target the new path.
- Tell the user how to see the result: reopen the new project and open the task
  history; the migrated sessions should be listed, and their checkpoints should
  restore.
- Mention the rollback command (step 7) and the backup directory location.

### 7. Rollback (if needed)

```bash
python scripts/restore_backup.py --backup-dir "<backup dir>" --execute
```

This restores the Zoo files from the backup and deletes the Roo copies that were
imported (pass `--keep-roo` to keep them). Use `--list` first to preview.

## Edge cases and limits

- **Exact-match reality.** Sessions only appear under a project whose `workspace`
  string equals the session's, character for character (including drive-letter
  case and separators). This is the single most common reason a migration "does
  not work", so always confirm the target value in step 3.
- **Subfolders.** If the project was opened as a subfolder (for example VS Code
  workspace root differs from the git root), the recorded `workspace` is the
  workspace root, not the repo root. Discover scans the actual recorded values,
  so it surfaces the true strings to match.
- **Checkpoint restore is destructive.** Restoring a checkpoint runs
  `git clean -fd` and `git reset --hard` against the new worktree, which discards
  untracked files in the new location. Warn the user before they restore.
- **Nested git repos.** Checkpoint initialization is disabled when a nested `.git`
  exists at the new location. If checkpoints stop working after a move, this is
  usually why.
- **Different machine.** This skill handles a same-machine move. To carry
  sessions to another machine, first copy the relevant `tasks/<taskId>/`
  directories, then run the same steps there.
- **No matches found.** If step 3 finds nothing, the old path string does not
  match exactly. Run `discover_sessions.py` and compare the recorded strings with
  what the user typed — a separator or drive-letter-case difference is the usual
  culprit.

## Requirements

- Python 3.8+ on PATH.
- Git is optional: checkpoint `core.worktree` is updated via `git config` when git
  is available, and via a direct edit of `.git/config` otherwise.
