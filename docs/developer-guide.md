# Developer guide

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
