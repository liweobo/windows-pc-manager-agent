# User guide

## 使用 Stage 4B 启动项管理

1. 打开“启动项管理”页，等待只读刷新完成。上表是 Windows 当前配置，下表只显示本
   Agent 以前禁用且仍有可验证备份的条目。
2. 用名称、发布者、来源、状态或目标程序筛选。只有“可禁用”的普通当前用户第三方条目
   可以操作；其余条目显示只读或阻止原因。
3. 选中一行并点击“审查禁用”。阅读具体名称、来源、程序路径、状态、安全分类、R2、
   普通用户权限、备份摘要和 FULL 回滚的有效条件。
4. 完成计划确认后，程序会重新读取全部身份和备份。再次核对即时 Preview，再确认执行。
5. 查看验证结果。它只说明该配置当前已移出或已恢复，不保证程序以后一定/一定不启动。
6. 恢复时只能在下方 Agent 禁用列表选中记录并重复两次确认。若原位置已出现新对象、备份
   损坏或禁用材料变化，恢复会停止，绝不覆盖。

本功能不编辑 HKLM/RunOnce/公共 Startup，不禁用 Microsoft、Windows、安全软件、驱动、
企业管理或 Agent 自身条目，不写 StartupApproved，不批量处理，也不会请求管理员权限。

## 使用 Stage 4A 受控进程关闭

1. 在“系统诊断”页运行“进程”或性能诊断，选中一个明确进程，再点击“审查选中进程的
   关闭选项”；也可在聊天输入“关闭 demo”。
2. 等待后台实时查询。旧的 Stage 3 表格行只是线索，程序会重新读取 PID、进程名、路径、
   用户、启动时间、会话、窗口、服务和保护状态。
3. 阅读计划 Preview：具体应用组/成员、资源影响、安全分类、R2、普通用户权限和回滚
   `NONE`。系统、服务、安全软件、其他用户、Agent 等阻止项不会出现可执行确认按钮。
4. 点击“确认计划并重新验证”。此时仍未关闭进程；程序会再次读取身份和安全状态。
5. 阅读即时 Preview，在短时有效期内点击“请求正常退出”。应用可能自己弹出保存提示。
6. 查看逐 PID 验证结果。`EXITED`/`ALREADY_EXITED` 表示原身份已不存在；超时不等于成功。
7. 只有正常退出不支持或超时后，才可主动打开“查看强制终止选项”。它会创建全新的
   R2_HIGH_IMPACT 计划并再次要求两次确认，绝不会自动执行。

强制终止可能丢失未保存数据或损坏应用状态，回滚等级为 `NONE`。重新启动应用不是 Undo。
“停止等待/后续对象”也不是 Undo。程序不会请求管理员权限或在权限不足时重试提权。

聊天中的“关掉它”只有在最近一次性能结果给出了一个明确第一名，或你明确选择了一行时才
绑定目标；否则会要求选择，不会猜测。名称对应多个不同安装路径时也会拒绝并要求选行。

## 使用“系统诊断”页

1. 输入目标，或选择“系统概览”“性能诊断”“进程”“启动项”“服务”“软件”。
2. 阅读收集器、采样次数/间隔、对象上限、`R0`、修改数 `0` 和无需管理员权限声明。
3. 点击“确认 R0 计划”；计划变化后旧确认失效。
4. 点击“开始只读诊断”。任务在后台运行，“取消”会安全结束采样并跳过后续项。
5. 在概览、磁盘、进程、启动项、服务和软件分页查看结果。单项失败不删除其他结果。
6. 阅读确定性观察和实际阈值；它们不是故障根因或恶意软件判定。

当前版本不缓存启动项、服务或软件清单；每次执行都会重新读取，界面显示统一采集时间。

常用输入：`诊断电脑为什么卡顿`、`查看 CPU 和内存`、`查看进程资源占用`、
`查看开机启动项`、`查看 Windows 服务列表`、`查看已安装软件清单`。

Stage 3 不终止进程、禁用启动项/服务、卸载软件、修改注册表或请求管理员权限。

## Move selected objects to Windows Recycle Bin

1. Add an authorized ordinary personal directory and run file analysis, or open the
   **Windows Recycle Bin** tab and add paths manually.
2. Check the exact files you want. The Agent never chooses them for you.
3. Choose **Move to Recycle Bin**, then generate the R2 Preview.
4. Review every path, contained-object count, total bytes, hidden/system indicators,
   blocked reasons, and the MANUAL recovery warning.
5. Complete the first plan confirmation. Nothing is moved at this point.
6. The application rechecks all identities and directory contents. Complete the second
   immediate confirmation only if the displayed objects are still correct.
7. Review the result and recovery records. To restore, open Windows Recycle Bin, locate
   the object by original name and deletion time, right-click it, and choose **Restore**.

The operation is unavailable for protected/system/application-data paths, links/junctions,
network/removable/unknown/non-system volumes, or when Windows cannot prove Recycle Bin
capability. The app cannot restore an item after the Recycle Bin was emptied. It never
offers permanent deletion or clearing the Recycle Bin.

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
- **TARGET_AMBIGUOUS**：同名进程来自不同路径；回到进程表选择具体一行。
- **PROCESS_IDENTITY_CHANGED / TARGET_GROUP_CHANGED**：进程或浏览器帮助进程已经变化；
  生成新 Preview，不要复用旧确认。
- **UNSUPPORTED_GRACEFUL_EXIT**：没有可接收 `WM_CLOSE` 的顶层窗口；若确实需要，阅读并
  主动进入独立强制终止流程。
- **PROCESS_ACCESS_DENIED**：普通用户权限不足；Stage 4A 不会提权，请不要以管理员方式绕过。

Closing the window normally hides it to the tray. Use the tray's safe exit action to cancel
work, wait for workers, hide the icon, and close the application.
