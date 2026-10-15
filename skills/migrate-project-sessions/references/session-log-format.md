# Session log file format

Reference for reading session logs when you need to inspect a session's contents —
for example to help the user decide whether a specific session should be migrated.

## ⚠️ These files are large — do not read them whole

`ui_messages.json` and `api_conversation_history.json` routinely grow to several
megabytes and thousands of entries. Reading one in full floods context, is slow,
and is rarely necessary. Prefer, in order:

1. `list_sessions.py` — first user message of every matching session, already
   trimmed. This is enough to pick sessions most of the time.
2. `read_session.py --parts` — a compact structural overview (counts + per-index
   previews).
3. `read_session.py --grep "<regex>"` / `--first N` / `--last N` / `--get IDX` —
   targeted extraction with a bounded result.
4. Only if a script cannot express what you need, use the `search_files` tool
   (regex over the workspace) or a small one-off `grep`/`rg` command. Never
   `read_file` these logs without a `limit`/offset.

## Directory contents

Zoo Code task directory:

```text
tasks/<taskId>/
  history_item.json              # compact session metadata (safe to read fully)
  ui_messages.json               # rendered conversation (large)
  api_conversation_history.json  # model conversation history (large)
  task_metadata.json             # file-context tracking (small; absolute paths)
  checkpoints/.git/              # shadow git repo (binary/objects)
```

Roo Code task directory — **no `history_item.json`**. Roo keeps its session
metadata (including the first user message and the `workspace` binding) in the
global state database, not in the task directory, so the first user message is
read from `ui_messages.json` and the metadata comes from the database:

```text
tasks/<taskId>/
  ui_messages.json               # rendered conversation (large)
  api_conversation_history.json  # model conversation history (large)
  task_metadata.json             # file-context tracking (small; usually RELATIVE paths)
  checkpoints/.git/              # shadow git repo (binary/objects)
```

## history_item.json

Compact metadata. Safe to read in full. The first user message is available here
cheaply as `task`.

```json
{
  "id": "1750000000000",
  "number": 1,
  "ts": 1750000000000,
  "task": "the first user message text",
  "workspace": "d:\\Develop\\AI\\Zoo-Code",
  "mode": "code",
  "status": "completed",
  "tokensIn": 0, "tokensOut": 0, "totalCost": 0,
  "parentTaskId": "optional",
  "rootTaskId": "optional"
}
```

Relevant fields: `task` (first user message), `workspace` (the project binding),
`ts` (start time), `id` (task ID / directory name), `status`.

Roo Code does not write this file. The same fields exist only inside the global
state database entry (`taskHistory[]`, see
[storage-layout.md](storage-layout.md)); when a Roo session is imported into Zoo,
a `history_item.json` is synthesized from that entry so the copy carries the
metadata Zoo expects.

## ui_messages.json

An array of `ClineMessage` objects — the conversation as shown in the webview.

```json
[
  { "ts": 1750000000000, "type": "say", "say": "task", "text": "first user message" },
  { "ts": 1750000000001, "type": "say", "say": "text", "text": "..." },
  { "ts": 1750000000002, "type": "ask", "ask": "tool", "text": "{\"tool\":\"read_file\",...}" },
  { "ts": 1750000000003, "type": "say", "say": "checkpoint_saved", "text": "<commit-hash>" }
]
```

Fields you will see:

- `ts` — millisecond timestamp.
- `type` — `"say"` (informational) or `"ask"` (expects user input).
- `say` — kind when `type: "say"`: `task`, `text`, `user_feedback`,
  `user_feedback_diff`, `command`, `command_output`, `completion_result`,
  `checkpoint_saved`, `api_req_started`, `api_req_finished`, `error`, `diff`,
  `condense_context`, ... (partial list).
- `ask` — kind when `type: "ask"`: `followup`, `command`, `tool`,
  `completion_result`, `resume_task`, `api_req_failed`, ... (partial list).
- `text` — usually a string; for `ask: "tool"` it is a JSON string of the tool
  call; for `ask: "followup"` it may be a JSON array of question objects.
- `images` — optional array of data-URL strings.
- `partial` — true while a message is still streaming.

**First user message**: the first entry with `type: "say"` and `say` of `"task"`
or `"text"`. Older sessions used `"task"`; newer ones use `"text"`. `list_sessions.py`
and `read_session.py` handle both.

## api_conversation_history.json

An array of `ApiMessage` objects (Anthropic `MessageParam` plus extra fields) —
what was actually sent to the model. Structure differs from `ui_messages.json`.

```json
[
  {
    "role": "user",
    "content": "first user message, plus an <environment_details> block",
    "ts": 1750000000000
  },
  {
    "role": "assistant",
    "content": [
      { "type": "text", "text": "..." },
      { "type": "tool_use", "id": "...", "name": "read_file", "input": { "...": "..." } }
    ],
    "ts": 1750000000001
  },
  {
    "role": "user",
    "content": [ { "type": "tool_result", "tool_use_id": "...", "content": "..." } ],
    "ts": 1750000000002
  }
]
```

Fields you will see:

- `role` — `"user"` or `"assistant"`.
- `content` — **either** a plain string **or** an array of content blocks. Blocks
  can be `{"type":"text","text":...}`, `{"type":"tool_use","name","input"}`,
  `{"type":"tool_result","tool_use_id","content"}`, `{"type":"image",...}`.
- `ts` — millisecond timestamp.
- Optional metadata: `messageId`, `isSummary`, `condenseId`, `condenseParent`,
  `truncationId`, `truncationParent`, `isTruncationMarker`, `reasoning_content`.

**First user message**: the first entry with `role: "user"`. Note its `content`
also embeds an `<environment_details>` block containing the project path *as it
was at that time* — this is the historical path text the migration intentionally
does **not** rewrite.

## task_metadata.json

Small. File-context tracking for the session.

```json
{
  "files_in_context": [
    {
      "path": "src/a.ts",
      "record_state": "active",
      "record_source": "user_edited",
      "roo_read_date": null,
      "roo_edit_date": null,
      "user_edit_date": 1750000000000
    }
  ]
}
```

- `path` — the file's path. Zoo records it grounded in the project root (often
  absolute); Roo usually records it *relative* to the project root, so a move
  leaves Roo paths correct as-is.
- `record_state` — `"active"` or `"stale"`.
- `record_source` — how it entered context: `read_tool`, `user_edited`,
  `roo_edited`, or `file_mentioned`.
- `roo_read_date` / `roo_edit_date` — millisecond timestamps (nullable).
- `user_edit_date` — millisecond timestamp (optional).

This file is optional: an imported Roo session may omit it, and Zoo tolerates a
task without it.

## Reading tips

- To find whether a session mentions something, prefer
  `read_session.py --grep "<regex>"`; it returns only matching indices with short
  previews.
- To see the shape of an unfamiliar session, `read_session.py --parts` gives a
  per-index preview plus a text length, so you can spot the large entries without
  loading them.
- `--file api` switches to `api_conversation_history.json` when you specifically
  need the model-side history (for example to see tool calls/results).
- `--limit-text 0` disables truncation — use it only for a single targeted
  message (`--get`), never for `--parts`/`--first`/`--last` on a large session.
