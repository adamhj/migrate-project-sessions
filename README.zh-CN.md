# migrate-project-sessions

迁移 Zoo Code / Roo Code 的 AI 会话记录（对话日志与 checkpoint）到项目的新路径。

当一个项目目录被移动或重命名后，其历史会话会因路径绑定失效而从侧边栏消失、且无法正确续接。本项目提供一个 AI 技能（skill），用于在新路径下把这些会话重新指向新的项目路径。

## 背景：为什么不是简单的文件复制

会话并不按项目分目录存放，所有项目的会话都集中在同一个全局存储目录中，每条会话通过元数据里的一个精确字符串与项目绑定：

- `tasks/<taskId>/history_item.json` 的 `workspace` 字段。界面与后端都使用精确相等比较（区分大小写与分隔符）；恢复会话时该值会被原样用作任务的工作目录。若仍指向旧路径，会话既不会出现在历史列表中，恢复后也会用错误的工作目录运行。
- `tasks/<taskId>/checkpoints/.git/config` 的 `core.worktree`，是一个绝对路径。恢复 checkpoint 时若与任务工作目录不一致，会直接拒绝执行。

因此迁移的本质是：**为重命中的会话改写 `workspace` 绑定，并重定向其 checkpoint 的工作树**指向新路径。对话内容文件（`ui_messages.json`、`api_conversation_history.json`）属于历史文本，保持不变。

## 目录结构

```text
.
├── README.md
└── skills/
    └── migrate-project-sessions/
        ├── SKILL.md                          # 技能主文件：交互流程与触发描述
        ├── references/
        │   ├── storage-layout.md             # 存储布局、路径绑定规则、备份/回滚模型
        │   └── session-log-format.md         # 会话日志文件格式参考
        └── scripts/
            ├── _common.py                    # 公共逻辑：存储根探测、任务扫描、原子写、路径/元数据改写、计划聚合
            ├── discover_sessions.py          # 扫描 Zoo+Roo，聚合各 workspace 值，按相似度排序候选旧路径
            ├── list_sessions.py              # 列出匹配会话及其首条用户消息预览
            ├── plan_migration.py             # 生成迁移计划（dry-run），支持 --ids 部分迁移
            ├── apply_migration.py            # 执行迁移（定点备份 + 改写），需 --execute
            ├── restore_backup.py             # 依据备份清单回滚
            └── read_session.py               # 安全的分拣式日志读取（避免整读大文件）
```

## 安装

将技能目录放到 Zoo Code / Roo Code 的技能目录下。技能遵循标准结构（`SKILL.md` + 可选的 `scripts/`、`references/`），因此把 `skills/migrate-project-sessions/` 整体复制到目标技能目录即可：

```powershell
Copy-Item -Recurse skills\migrate-project-sessions "$env:USERPROFILE\.roo\skills\"
```

安装后，当用户表示"移动/重命名了项目，需要迁移会话记录"等意图时，技能会被自动触发。

## 使用

### 通过 AI 触发（推荐）

在移动后的新项目路径下打开会话，向 agent 说明需要迁移会话记录。技能会引导完成（详见 `SKILL.md`）：

1. 以当前项目路径作为迁移目标。
2. 确定旧路径：由用户提供，或由脚本扫描全部已记录路径并按相似度给出候选，再由用户选定。
3. 生成迁移计划并报告匹配到的 Zoo / Roo 会话数量与将写入的 `workspace` 值。
4. 确认迁移范围（Zoo / Roo / 两者，默认两者）。
5. 如需只迁移部分会话，先列出会话与首条用户消息，由用户勾选。
6. 执行迁移；必要时可按备份回滚。

### 手动运行脚本

脚本为纯文件系统操作，可脱离 VS Code 在任意终端运行（输出的均为 JSON）。注意：将 `scripts/` 作为脚本所在目录传入。

```powershell
# 1) 发现候选旧路径（不确定旧路径时）
python skills/migrate-project-sessions/scripts/discover_sessions.py --new-path "d:\Develop\AI\proj"

# 2) 列出旧路径下的会话及首条用户消息（用于挑选迁移范围）
python skills/migrate-project-sessions/scripts/list_sessions.py --old-path "d:\old\proj"

# 3) 生成迁移计划（dry-run，不修改任何文件）
python skills/migrate-project-sessions/scripts/plan_migration.py --old-path "d:\old\proj" --new-path "d:\Develop\AI\proj"

# 4) 执行迁移（--ids 可选，用于只迁移指定会话）
python skills/migrate-project-sessions/scripts/apply_migration.py --old-path "d:\old\proj" --new-path "d:\Develop\AI\proj" --mode both --execute

# 5) 需要时回滚
python skills/migrate-project-sessions/scripts\restore_backup.py --backup-dir "<备份目录>" --execute
```

主要参数：

- `--old-path` / `--new-path`：旧、新项目路径；`--new-path` 缺省为当前工作目录。
- `--mode {zoo,roo,both}`：迁移哪一侧，默认 `both`。
- `--ids id1,id2,...`：仅迁移指定会话，省略则迁移全部命中。
- `--execute`：`apply_migration.py` / `restore_backup.py` 必须显式指定才会真正写入；否则仅预览。
- `--side {both,zoo,roo}`：`list_sessions.py` 的筛选。
- `--parts` / `--first N` / `--last N` / `--grep <regex>` / `--get IDX` / `--file {ui,api}`：`read_session.py` 的读取模式。

## 迁移行为与安全

- **Zoo 会话**：原地改写。改写前，将被触及的文件（`history_item.json`、`task_metadata.json`、checkpoint 的 `config`）备份到 `tasks/_migration_backup_<时间戳>/`，并记录原始 `workspace` 与 `core.worktree`。该目录以 `_` 开头，会被 Zoo 的任务扫描忽略，不会显示为会话。
- **Roo 会话**：复制进 Zoo 存储后改写副本，原文件不动，因此无需备份；同名 Zoo 任务不会被覆盖。
- **请先关闭打开了旧项目路径的窗口**：如果有 VS Code 窗口仍打开着**旧**项目路径，迁移前请先关闭它。该窗口会持有这些会话的内存缓存与逐文件锁，外部改写可能引发锁冲突或被其覆盖。运行本技能所在的**新**路径窗口可以保持打开，因为被改写的会话都属于旧项目。
- **原子写入**：JSON 采用临时文件 + 原子改名；checkpoint 优先用 `git config`，无 git 时回退直接改写 `.git/config`。

## 已知限制

- **精确匹配**：会话只在其 `workspace` 字符串与项目逐字符相等时出现（含盘符大小写、分隔符形式）。脚本会优先采用新路径下已有会话所使用的、由 VS Code 生成的规范字符串。
- **checkpoint 恢复是破坏性的**：恢复会执行 `git clean -fd` 与 `git reset --hard`，会丢弃新目录中的未跟踪文件。
- **嵌套 git 仓库**：若新位置存在嵌套 `.git`，checkpoint 会被禁用。
- **仅限同机移动**：跨机器需先复制相应的 `tasks/<taskId>/` 目录，再在新机执行相同步骤。

## 环境要求

- Python 3.8+。
- Git 可选：有 git 时用 `git config` 更新 checkpoint 工作树，无 git 时回退为直接改写配置文件。
