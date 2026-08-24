# Developer guide

## Stage 4D3 development

The Stage 4D3 graph begins in `UninstallContextRecorder`, which is optionally injected into each D2
service. Capture occurs immediately before the already-authorized adapter dispatch; finalization
records the verification outcome. Do not add commands, raw uninstall metadata, file contents or
guessed paths to this context.

`ResidualAnalysisPlanCompiler` accepts one transaction and derives scope only through
`ResidualScanScopePolicy`. `ResidualSafetyReviewer` must keep the exact three-tool order and verify
manifest risk/read-only/rollback, context/scope/plan digests and zero estimated modifications. A model
or UI must never inject a path or tool name.

Add a collector only when it owns one finite `ResidualSource`, accepts exact `ContextPathEvidence`,
shares `ResidualCollectionBudget`, uses metadata-only no-follow APIs and returns fail-soft issues.
Classification, ownership and protection belong in their separate deterministic safety modules.
Ownership evidence must never emit a cleanup recommendation, and new user-data classes must default
to at least PROTECTED.

Useful focused commands:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_software_residual_models.py tests/unit/test_software_residual_policies.py
uv run pytest tests/integration/test_software_residual_analysis.py tests/security/test_software_residual_safety.py
uv run pytest tests/gui/test_residual_analysis_dialog.py
uv run pytest tests/performance/test_software_residual_performance.py -q -s
```

The zero-destructive test must continue to monitor Python delete APIs and the registry must expose no
cleanup/trash tool. Export is the only Stage 4D3-created file and must remain exclusive-create,
local-only and separately audited. Update the API reference whenever any public/private Stage 4D3
function changes because safety depends on the exact call boundary.

Optional bounded settings are `PC_MANAGER_RESIDUAL_MAX_ROOTS` (default 16, maximum 32),
`PC_MANAGER_RESIDUAL_MAX_OBJECTS` (default/maximum 25,000) and
`PC_MANAGER_RESIDUAL_TIMEOUT_SECONDS` (default 60, maximum 600). Lower values are valid safety
choices; values beyond model bounds prevent application configuration from loading.

## Stage 4D2C1 development

实现位于 `domain/winget_uninstall.py`、`orchestration/winget_*`、`safety/winget_*`、
`confirmation/winget_uninstall.py`、`persistence/winget_uninstall.py`、
`platform_support/*/winget_uninstall.py`、`tools/system_tools/winget_uninstall.py`、
`audit/winget_uninstall.py` 和 `ui/winget_uninstall_*`。逐函数说明见 `api-reference.md`。

维护时必须保持：

- registry 精确只有 `software.uninstall.winget`，adapter 输入没有命令/argv/Source URL 字段；
- 只接受 Package ID+version+official source+current-user scope 与唯一 HIGH Software mapping；
- `winget list` 本地化表格不能成为唯一执行权威；JSON parser 必须有 schema/大小/数量边界；
- WindowsApps alias 必须直接证明 AppExecLink 和 Desktop App Installer family，不查 PATH；
- fixed argument tuple 不可由用户/LLM/配置扩展；禁止 quiet/override/force/purge/source mutation；
- capability 与 software class 分离，新增 class 没有明确规则时保持 BLOCK；
- preflight 只读：进程 warning，running service/winget busy/incomplete/active transaction block；
- elevated Agent、Shell/UAC、自动重启/重试、terminate/kill/service stop 都没有 fallback；
- 两级 durable confirmation、independent invariant validator 与 write guard 都不可省略；
- MSI/Vendor/winget repository 必须三方互斥，restart 只标 INTERRUPTED，不 redispatch；
- exit code 与 final result 分离，只有双清单完整且双方 identity 消失才 VERIFIED_REMOVED；
- residual 只能 exact-path `lstat`，Rollback 永远 NONE；audit 不保存命令、URL、路径或环境值。

专项命令：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_winget_*.py `
  tests/unit/test_software_uninstall_router.py `
  tests/integration/test_winget_uninstall_workflow.py `
  tests/security/test_winget_uninstall_security.py `
  tests/gui/test_winget_uninstall_dialog.py -q
uv run pytest tests/integration/test_windows_winget_inventory_readonly.py -q
uv run ruff check .
uv run mypy src
```

全部执行测试必须使用 synthetic inventory/fake adapter。普通/CI 测试不得卸载 runner 软件；
不得提交真实 Package 清单、Source URL、本机 alias 路径、审计数据库或残留报告。

## Stage 4D2B development

实现分布在 `domain/vendor_uninstall.py`、`orchestration/vendor_*`、
`safety/vendor_*`、`confirmation/vendor_uninstall.py`、`persistence/vendor_uninstall.py`、
`platform_support/vendor_uninstall.py`、`platform_support/windows/authenticode.py`、
`platform_support/windows/vendor_uninstall.py`、`tools/system_tools/vendor_uninstall.py`、
`audit/vendor_uninstall.py` 和 `ui/vendor_uninstall_*`。`software_uninstall_router.py` 只读选择
MSI/Vendor 机制，不能执行。完整函数级说明见 `docs/api-reference.md` 的 Stage 4D2B 章节。

维护时必须保持以下不变量：

- 专用 registry 精确等于 `software.uninstall.vendor`；tool 输入只能是内部
  `VendorUninstallRequest(action=ValidatedVendorUninstallAction)`，禁止加入字符串命令、路径、
  参数或通用 process API。
- Raw/Quiet UninstallString 只存在于 fresh ephemeral inventory。解析使用
  `CommandLineToArgvW`；不要用 POSIX `shlex` 代替，不要把 raw 值持久化或传给模型。
- Resolver 不做 PATH 搜索或环境展开。只有 direct local absolute `.exe` 能进入 trust；
  wrapper/loader/script/UNC/device/reparse/temp/download/cache 全部 fail closed。
- 文件身份、SHA-256、离线 Authenticode、Publisher 匹配、安装目录关系和 exact argv 都是执行
  不变量。不要把“signed”或“在 Program Files”单独变成 ALLOW 条件。
- 参数策略是 finite allow-list。策略失败就停止；禁止删掉可疑参数后继续、添加 silent/restart
  参数，或让 LLM/用户编辑参数。
- Stage 4D1 class policy 先于 mechanism。任何新 class 没有明确 ALLOW 规则都保持 R3/BLOCK。
- Plan、runtime confirmation 和 write guard 是三个独立门，全部绑定稳定摘要、过期且单次消费。
  MSI 与 Vendor repository 必须互相阻止第二个 active uninstall。
- Preflight 只有读权限；不要注入 Stage 4A/4C 写服务。相关进程不自动 kill，服务不自动 stop。
- Adapter 只能使用 exact array、absolute executable、explicit cwd、sanitized env、DEVNULL、
  `shell=False`。禁止 shell/CMD/PowerShell/runas/ShellExecute/重试/重启/terminate/kill/UI automation。
- 停止监控和 long-running 结果不代表停止卸载，事务保持 `MONITORING`；重启变
  `INTERRUPTED`，绝不 redispatch。只有正常结束后才运行 fresh verifier。
- Verifier 必须保留 process result 与 observed software state；residual analyzer 只能 exact-path
  `lstat`，不能枚举、跟随或删除。Rollback 永远为 NONE。
- Audit/confirmation/transaction 不保存 raw command、full args/path、child environment 或 secret。
  Pre-start audit/persistence 失败必须阻止 launch。

本阶段定向验证命令：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_vendor_uninstall_*.py `
  tests/unit/test_software_uninstall_router.py `
  tests/integration/test_vendor_uninstall_workflow.py `
  tests/security/test_vendor_uninstall_security.py `
  tests/gui/test_vendor_uninstall_dialog.py -q
uv run ruff check .
uv run mypy src
```

所有 fixture 必须是合成数据和 fake adapter。不得提交本机软件清单、真实 UninstallString、
真实可执行路径、注册表 dump、审计数据库或残留报告；CI 不得卸载 runner 软件。

## Stage 4D2A development

执行链分布在 `domain/software_uninstall_execution.py`、
`orchestration/software_msi_validation.py`、`orchestration/software_execution_preflight.py`、
`safety/software_uninstall_execution_*`、`confirmation/software_uninstall_execution.py`、
`persistence/software_uninstall_execution.py`、`platform_support/*/msi_uninstall.py`、
`tools/system_tools/software_uninstall.py`、`orchestration/software_uninstall_execution.py`、
`audit/software_uninstall_execution.py` 和 `ui/software_uninstall_*`。

开发时必须保持这些不变量：

- Stage 4D2A registry 精确等于 `software.uninstall.msi`；输入只能是
  `MsiUninstallRequest(product=ValidatedMsiProduct)`，禁止加入字符串命令或额外参数。
- ProductCode 必须来自 fresh 本地 inventory，并与 msi.dll 当前注册、Stage 4D1 capability、
  source-qualified identity 和 metadata 全部一致；LLM 只能给 `SoftwareTargetQuery`。
- safety class 在 mechanism 之前决定。新类型没有显式 ALLOW 规则就保持 R3/BLOCK。
- 计划确认、runtime 确认和 write guard 是三个独立门；测试必须覆盖 expiry、binding、replay、
  double-click、数据库失败和身份变化。
- `WindowsMsiUninstallPlatform` 的 executable 和参数模板是产品策略，不允许传入 raw metadata。
  不得添加 silent flags、Vendor/package fallback、shell、elevation、kill/reboot/retry。
- Windows Installer 未退出时不能刷新清单并宣称 final success。有限监控后保持 `WAITING`；
  restart 只标 `INTERRUPTED`。
- verifier 必须保留 installer result 与 observed state 两部分；residual analyzer 只能 exact-path
  `lstat`，不能枚举、跟随链接或删除。
- 所有测试使用 fake adapter 和 synthetic ProductCode。普通 CI 绝不卸载 runner 软件。

专项命令：

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_msi_uninstall_*.py `
  tests/integration/test_msi_uninstall_workflow.py `
  tests/security/test_msi_uninstall_execution_security.py `
  tests/gui/test_software_uninstall_dialog.py -q
uv run ruff check .
uv run mypy src
```

新增或修改任何生产函数时，必须同步下面的 `api-reference.md`；文档不得包含真实软件清单、
真实 ProductCode、原始 UninstallString 或本机残留路径。

## Stage 4D1 development

实现分布在 `domain/software_uninstall_analysis.py`、`platform_support/*/software_inventory.py`、
`platform_support/windows/uninstall_metadata.py`、`orchestration/software_*`、
`safety/software_*`、`confirmation/software_uninstall_analysis.py`、
`audit/software_uninstall_analysis.py`、`tools/system_tools/software_analysis.py` 和
`ui/software_*`。保持 raw source 与 normalized domain 分离；新增来源适配器只能返回元数据，
不得加入卸载调用。

Stage 4D1 专用 `ToolRegistry` 必须精确等于五工具 allow-list；所有 manifest 必须
R0/read-only/NONE/no-runtime-confirmation；所有结果必须证明 `execution_performed=false`。不要在
通用写执行器注册 software uninstall 工具，不要把 acknowledgement 映射到
`ExecutionAuthorization`。GUI worker 只调用编排服务，禁止引入 uninstall worker/button。

专项验证命令见 CI 的 `Stage 4D1 uninstall-analysis zero-execution boundary` 步骤；它使用
`.coveragerc-stage4d1` 对身份、解析、能力、策略、校验和工具边界执行 95% 门槛。另运行：

```powershell
uv run pytest tests/integration/test_windows_software_inventory_readonly.py -q
uv run pytest tests/performance/test_software_inventory_performance.py -q -s
```

真实 Windows 测试只读卸载注册表视图并断言没有执行；性能测试使用合成记录。未来执行阶段必须
另建风险模型、工具集、确认和测试，不能扩大本阶段计划或复用 target acknowledgement。

## Stage 4C2 development

Stage 4C2 is split across `domain/service_startup_actions.py`, `safety/service_startup_*`,
`confirmation/service_startup_actions.py`, `persistence/service_startup_actions.py`,
`platform_support/service_startup.py`, `platform_support/windows/service_startup.py`, three exact
registered tools, orchestration, audit, rollback command, Qt workers/dialogs and the runtime composition
root. `ServiceStableIdentity` must remain separate from `ServiceStartupConfiguration`.

The only approved mutation is one dependency-free eligible service changing between non-delayed
Automatic and Manual. Do not add Disabled or delayed transitions, `ChangeServiceConfig2`, a generic
configuration object, arbitrary access masks, runtime Start/Stop, account/password/binary/dependency/
recovery/security changes, batch operations, DACL changes, elevation, shell, WMI, `sc.exe`, automatic
retry or crash resume. A new transition requires a new threat review and explicit user authorization.

Focused verification:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_service_startup_actions.py -q
uv run pytest tests/integration/test_service_startup_workflow.py -q
uv run pytest tests/security/test_service_startup_safety.py -q
uv run pytest tests/gui/test_service_startup_management.py -q
uv run pytest tests/unit/test_windows_service_startup_adapter.py -q
uv run pytest tests/integration/test_windows_service_startup_readonly.py -q
```

All mutation tests must use `FakeServiceStartupPlatform` and a fake protector with disposable SQLite
files. The real-Windows integration is query-only and must never call `ChangeServiceConfig`. Restore tests
must cover backup corruption, identity/config/runtime/dependency drift, confirmation expiry/replay,
ordinary-user permission denial, journal failure, restore conflict and reverse-restore history. Every new
or changed production function must be described in `docs/api-reference.md`.

## Stage 4C1 development

Stage 4C1 is split across `domain/service_actions.py`, the narrow
`platform_support/service_control.py` protocol, `platform_support/windows/service_control.py`,
target/dependency orchestration, safety policy/Preview/validator, two-tier confirmation,
SQLite transaction persistence, two registered tools, audit, Qt workers/dialog/tab, and the
runtime composition root. Restart must remain orchestration of STOP then START; never add a
platform `restart`, cascade, generic service-control opcode, configuration change, process-kill,
shell, WMI, `sc.exe`, elevation, automatic retry, or auto-resume path.

Focused verification:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_service_action_models.py -q
uv run pytest tests/integration/test_service_action_flow.py -q
uv run pytest tests/security/test_service_safety.py tests/security/test_service_source_boundary.py -q
uv run pytest tests/gui/test_service_management.py -q
uv run pytest tests/integration/test_windows_service_control_readonly.py -q
```

All write-flow tests must use `FakeServicePlatform`; never start, stop or restart an installed
service from a test. The real-Windows test is read-only. `PC_MANAGER_SERVICE_ACTION_TIMEOUT_SECONDS`
sets the bounded 5–120 second state wait (default 30), and
`PC_MANAGER_SERVICE_RUNTIME_CONFIRMATION_TTL_SECONDS` sets the 15–300 second immediate gate
(default 60). Do not put these settings—or any credential—in `.env.example` when it contains
local user edits. Every new service method/function must be added to `docs/api-reference.md`.

## Stage 4B development

Stage 4B is split across `domain/startup_actions.py`, `platform_support/startup.py`, the
Windows startup/DPAPI adapters, `safety/startup_*`, `confirmation/startup_actions.py`,
`persistence/startup_actions.py`, orchestration, two narrow registered tools, audit and GUI
workers. Never add arbitrary registry/path parameters, StartupApproved writes, a shell
fallback, batch mutation or machine-wide behavior to these interfaces.

Focused verification:

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_startup_*.py tests/security/test_startup_safety.py -q
uv run pytest tests/integration/test_startup_action_workflow.py -q
uv run pytest tests/gui/test_startup_management.py -q
uv run pytest tests/integration/test_windows_startup_readonly.py -q
```

Mutation integration tests must use a fake adapter and disposable app data. The real-Windows
test is query-only; do not change a user's startup configuration from a test. A new source
requires an explicit identity model, risk decision, exact backup/recovery proof, confirmation
text, platform experiment, safety tests and an independent review before registration.

## Stage 4A development

Stage 4A code is split across `domain/process_actions.py`, `platform_support/processes.py`,
`platform_support/windows/process_management.py`, `orchestration/process_*`,
`safety/process_*`, `confirmation/process_actions.py`, `persistence/process_actions.py`,
`tools/system_tools/process_actions.py`, audit, workers and the Preview dialog.

Never add a generic PID-kill, shell, taskkill, elevation or “force fallback” entry point.
Any additional process action needs a distinct enum, manifest, risk, transaction transition,
confirmation text, platform method, verifier and tests. PID alone is never identity.

Focused verification:

```powershell
uv run pytest tests/unit/test_process_*.py tests/security/test_process_policy.py -q
uv run pytest tests/integration/test_process_action_flow.py -q
uv run pytest tests/gui/test_process_action_dialog.py -q
uv run pytest tests/integration/test_windows_process_management_real.py -q
```

The real adapter test creates and terminates only a child Python process started by that
test. Never point a test at an existing user process. The GUI worker tests must use
`FakeProcessPlatform`; no confirmation test should mutate the real operating system.

## Stage 3 development

Stage 3 is split across domain models, eight registered system tools, the platform protocol,
Windows query adapter, safety, confirmation, orchestration, audit, provider contracts, and
the dashboard. New collectors need a complete R0 manifest, finite enum/mapping entry,
independent safety validation, and success/denial/error/cancellation/limit tests. Never add
write methods to this protocol or reuse diagnostic confirmation for a later write.

```powershell
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest tests/unit/test_system_diagnostic_*.py tests/integration/test_system_diagnostic_flow.py tests/security/test_system_diagnostic_safety.py tests/gui/test_system_diagnostics_tab.py -q
uv run pytest tests/integration/test_windows_system_collectors_real.py -q
uv run pytest tests/performance/test_system_diagnostics_performance.py -q -s
```

The deterministic fake is `tests/fixtures/system_diagnostics.py`. The real Windows test is
query-only and verifies that process command-line and uninstall-command fields do not exist.

## Stage 2B development checks

Stage 2B code is split across `domain/trash.py`, `safety/trash_*`,
`confirmation/trash.py`, `orchestration/trash_*`, `tools/file_tools/trash.py`,
`platform_support/windows/recycle_bin.py`, `recovery/`, persistence/audit, and the GUI.
Do not reuse R1 FULL Undo models to represent Recycle Bin behavior.

Run the focused tests before the full suite:

```powershell
uv run pytest tests/unit/test_trash_models.py tests/unit/test_trash_confirmation.py -q
uv run pytest tests/security/test_trash_safety.py -q
uv run pytest tests/integration/test_trash_flow.py tests/integration/test_trash_recovery.py -q
uv run pytest tests/gui/test_trash_tab.py -q
```

Real integration may use only uniquely named files created by the test itself. Never point
a test at existing user data. A new platform adapter must expose positive recycle evidence,
must not fall back to `SHFileOperation` or filesystem deletion, and must keep COM on an STA
worker thread. Update `docs/api-reference.md` for every changed production function.

## Setup and verification

CI pins uv 0.11.32 for reproducible setup; use that version when regenerating `uv.lock`.

```powershell
uv sync --all-groups
uv run ruff format .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest -m "not performance" --cov=pc_manager_agent --cov-report=term-missing --cov-fail-under=85
uv run pytest tests/performance/test_large_scan.py -q -s
uv run bandit -q -r src
uv run pip-audit
uv build
uv run python -m pc_manager_agent --smoke-test
```

真实回收站探针默认跳过，因为它会把测试自己创建的微小临时文本放进当前用户回收站。只应在
一次性 Windows CI 主机或明确接受该测试副作用的环境中运行：

```powershell
$env:PC_MANAGER_RUN_REAL_RECYCLE_TEST = "1"
uv run pytest tests/integration/test_windows_recycle_bin_real.py -q
```

CI 在临时 Windows runner 上执行该探针，并要求 Shell 回收标志、新回收站项标识和源路径消失
全部成立。

除显式启用的真实回收站探针外，tests create temporary local trees and SQLite databases.
They do not modify real user files or contact OpenAI. Qt runs offscreen. The benchmark creates 10,000 empty synthetic
files, streams metadata to SQLite, and enforces broad time/memory regression ceilings.

The CI safety command separately measures path policy, authorization, confirmation,
rollback, plan/compiler validation, and registry boundaries with a 95% combined minimum.

## Stage 1 implementation map

- `authorization`: persisted user allow/deny decisions; never accept model path text.
- `domain/file_analysis.py` and `domain/reports.py`: strict immutable contracts.
- `safety/path_policy.py`: canonical local path and protected-root enforcement.
- `orchestration/file_analysis_planner.py`: untrusted intent → local executable plan.
- `safety/file_analysis_validator.py`: independent semantic/manifest/scope review.
- `tools/file_tools`: streaming scanner, classifier, large/inactive/duplicate analyzers,
  and safe cancellable hash reads.
- `persistence/analysis_results.py`: batches, keyset paging, membership, summaries/issues.
- `orchestration/file_analysis.py`: review, confirmation, execution, verification, audit.
- `reporting/exporter.py`: bounded exclusive-create exports without overwrite/delete.
- `ui/analysis_tab.py` and `ui/workers.py`: presentation and non-blocking workers only.

## Stage 2A implementation map

- `domain/file_operations.py`, `domain/transactions.py`: immutable plan/Preview/identity and
  explicit transaction/item models; no provider or Qt dependency.
- `orchestration/file_operation_planner.py`: optional path-free model intent, bounded local
  source resolution, and concrete deterministic move/rename/organization compilation.
- `safety/file_operation_validator.py`, `safety/operation_preview.py`: independent R1 graph
  review and real read-only filesystem impact/conflict snapshot.
- `confirmation/file_operations.py`: expiring, plan+Preview-bound, one-time forward and
  rollback confirmations.
- `persistence/file_operations.py`: additive SQLite transaction/item/argument reservation
  and checksum-protected write-ahead Undo journal; startup invalidates stale work safely.
- `tools/file_tools/{create_directory,move,rename,remove_created_directory}.py`: registered
  single-object operations with execution-time revalidation and typed verification.
- `platform_support/windows/file_operations.py`: Unicode long-path Win32 identity, move,
  create-directory and rollback-only empty-directory primitives without shell/elevation.
- `orchestration/transaction_executor.py`: fail-safe STOP policy, per-item persistence,
  audit and verified terminal reports.
- `rollback/manager.py`: persisted reverse-order plans, live conflicts, independent
  confirmation and registered reverse execution.
- `ui/operation_tab.py`, `ui/workers.py`: Preview/history/progress/rollback presentation and
  non-blocking execution only.

## Adding a write operation

1. Confirm it is inside the current stage/risk boundary; do not generalize Stage 2A.
2. Define strict immutable plan, input, output, postcondition, and Undo fields.
3. Add a complete R1 manifest with Preview, confirmation, batch, permission, platform and
   truthful rollback declarations. Register it only in runtime composition.
4. Extend the deterministic compiler and independent validator; never accept a path/tool,
   risk, code, or command directly from a model.
5. Make Preview read-only and bind every execution-relevant value, identity and final path.
6. Persist RUNNING + PREPARED Undo before calling the platform adapter. Invoke through
   `ToolRegistry` with `ExecutionAuthorization`, then verify before COMPLETED/AVAILABLE.
7. Define a reverse registered operation, reverse conflict checks, fresh confirmation and
   postcondition verification. FULL must have testable validity conditions.
8. Test normal, conflict, source/target change, permission/lock, cancellation, batch,
   crash, partial failure, reverse order, rollback conflict, audit failure, and GUI flow.

## Adding or changing analysis

1. Define strict provider-neutral Pydantic input/output and result annotations.
2. Implement deterministic code over paged metadata; use content only when unavoidable.
3. Add a complete R0 `ToolManifest`, then register it only in runtime composition.
4. Extend the compiler's allow-list mapping and safety validator's exact semantic checks.
5. Bind all meaningful options into the immutable confirmed task plan.
6. Add cancellation, limits, changed-file, permission, and malformed-result tests.
7. Audit only necessary structured summaries; do not record file content or secrets.
8. Update README, CHANGELOG, architecture, security/threat, user/developer, roadmap,
   rollback, API reference, and AGENTS before committing.

Never add a generic shell tool, dynamic code execution, link following, silent overwrite,
or model-authored filesystem authority.

## Model and external-data boundary

`FileAnalysisPlannerRequest` contains user goal, root labels/opaque IDs, allowed analysis
enums, and current registry names. The provider returns `FileAnalysisIntentDraft`; the
compiler discards any idea of provider authority and derives paths/steps itself.

`AnalysisExplanationRequest` contains filters and `FileAnalysisSummary`; it has no path,
name, issue details, or content. Provider observations reject digits, preventing invented
numbers from overriding measured output. Both calls require an exact digest-bound external
consent and have fake-client tests.

## API and Git discipline

[`api-reference.md`](api-reference.md) documents every production function/method,
including private maintainer helpers. After changing a function, update its signature,
behavior, errors, side effects, and safety notes there.

Work from `codex/*`, `fix/*`, or `docs/*`. Inspect status/diff, preserve user changes, run
all checks, scan staged content for credentials/user data, create logical commits, and push
without force. Prefer `git revert <sha>` on a new branch for code rollback.
## Stage 4D2C2 development boundary

Install all locked Windows-only PyWinRT projections with `uv sync --all-groups`. Do not replace the
adapter with PowerShell. Unit/integration/security/GUI tests use synthetic package records and a fake
removal adapter; ordinary CI must never uninstall a runner's real Store applications. The only real
Windows test is `uv run pytest tests/integration/test_msix_windows_inventory.py -q`, which is bounded
and read-only and never prints package identities.

When adding fields, update the identity, Preview invariant, both confirmation bindings, reserved tool
argument digest, audit redaction tests and API reference together. Any incomplete WinRT property,
relationship, process/service or inventory result must block execution. Never add an all-users,
Provisioned, arbitrary removal-option, generic PackageManager, PowerShell, retry or cleanup surface.
