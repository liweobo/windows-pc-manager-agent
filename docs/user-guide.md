# User guide

## First run

1. In PowerShell, run `uv sync --all-groups`, then `uv run pc-manager-agent`.
2. Keep the app running as your normal Windows account; do not use “Run as administrator”.
3. Open **文件分析** and choose **添加授权目录**. Read the exact path and confirm.
4. Optionally mark it as a common directory or add a protected child under
   **自定义禁止目录**. Authorization never includes the selected directory's parent.
5. Enter a goal, select one or more authorized roots, and adjust:
   - minimum size (default 1 GB);
   - suspected inactivity (default 90 days);
   - large/inactive/duplicate analyses;
   - “all conditions” or “any condition”.
6. Select **生成只读分析计划**, inspect scope, exclusions, thresholds, R0 risk, zero
   modifications/deletions, then explicitly confirm and start.

With no model configured, controls create the same deterministic plan locally. If a model
is configured, the app first asks whether it may send the goal, root labels/opaque IDs,
allowed analyses, and tool names. It does not send real paths or file details.

## Results

The progress area shows discovered files/directories, bytes, issues, and the current
analysis phase. **取消** requests a cooperative stop; completed safe batches remain visible
and the result is marked `CANCELLED`.

The table includes path, size, category, timestamps, candidate status, inactivity
confidence, and duplicate group. Use search, category, minimum size, sort field, and sort
direction; pages are loaded from SQLite rather than all held in memory.

- **大文件** means size is equal to or above the displayed threshold.
- **疑似长期未使用** means access and modification evidence pass the rule. NTFS atime may
  be disabled or deferred, so confidence is HIGH/MEDIUM/LOW/UNKNOWN and never proves value.
- **内容重复** means full SHA-256 matches; the default plan also compares bytes. The app
  does not choose which copy is original and offers no delete button.

**打开所在目录** asks Windows Explorer to select an existing authorized file. **导出**
creates a new local `.csv` or `.json`; existing files and network destinations are refused.
If export fails after creation, a partial file can remain and must be inspected/removed
manually. Stage 1 never deletes it automatically.

**生成模型说明** requires another confirmation and sends aggregate totals only. Every
number displayed in the explanation comes from local deterministic results.

## Safe file operations and Undo

In the analysis table, check only the rows you want and select **移动勾选项** or
**重命名勾选项**. The **安全文件操作** page also accepts explicit files/directories.
For a move, choose a destination that is itself inside an authorized root. For rename,
choose one finite rule: prefix, suffix, sequence, upper/lower case, literal replacement,
or modification-date prefix. The app never evaluates code or a regular expression.

Generating Preview is read-only. Review every final path and the totals for READY,
CONFLICT, BLOCKED, bytes, directory creation, R1, and FULL rollback. Existing target names,
unsafe/redirected paths, changed sources, permission errors, cross-volume moves, and batch
limits are displayed and are not silently fixed. Confirmation shows the concrete item and
byte impact. You must then separately start execution; changing/restarting invalidates it.

During execution, **停止后续操作** means the currently active Windows operation finishes
and is verified, then later items do not start. Completed items remain real and have Undo;
the result accurately shows success, failure, skipped, and pending counts.

To undo, select the exact transaction in history and generate **回滚 Preview**. The manager
uses stored Undo—not model guesses—and displays changed results, occupied original paths,
revoked scope, or non-empty created directories. Approve the separate rollback confirmation
only after checking it. A conflict is never overwritten; it requires manual resolution and
a fresh Preview.

Natural-language move/rename/organize requests require a configured provider and external
data confirmation. Only goal text, labels, opaque root IDs, enums, and tool names leave the
computer. Source discovery, metadata, years, real paths, Preview, safety and execution stay
local. With the provider disabled, the explicit selection/rule controls remain fully usable.

## Audit and local data

Open **审计** and refresh to view authorization, external consent, plan review, confirmation,
tool start/completion/failure, result, and export events. Local SQLite data is under the
directory shown in **设置**. Do not edit it while the app runs. Secrets and file contents
are not intentionally stored in audit events.

## Common errors

- **尚未授权目录**: add and select a root before planning.
- **outside approved scope / protected**: choose the exact intended local root; do not use
  its parent to bypass protection.
- **reparse point**: select the physical directory, not a shortcut, symlink, or junction.
- **Network-backed paths unavailable**: Stage 1 accepts local drives only.
- **NAME_CONFLICT**: another object owns the target; Stage 2A never overwrites or auto-renames.
- **CROSS_VOLUME_MOVE**: choose a destination on the same volume; copy-plus-delete is absent.
- **SOURCE_CHANGED / RESULT_CHANGED**: regenerate Preview after inspecting the current file.
- **INTERRUPTED**: the app stopped during a transaction and did not resume; inspect history
  and generate a rollback Preview.
- **old confirmation invalid**: regenerate and reconfirm after any scope/threshold change.
- **OpenAI configuration incomplete**: either disable the provider or set provider, model,
  and API key in the same PowerShell environment. Never paste a key into chat.
- **audit database failure**: verify the local data directory is writable and not locked;
  do not continue by deleting safety code or using administrator mode.

Closing the window normally hides it to the tray. Use the tray's safe exit action to cancel
work, wait for workers, hide the icon, and close the application.
