# Production logging, crash evidence and diagnostics

## Structured local logging

`configure_application_logging` installs one package logger writing one-line JSON records to
`logs/application.jsonl`. Production ignores debug requests and uses INFO or higher. Files rotate at 2 MiB with five
backups. The destination and its parent must not be reparse points. The logger does not configure the process-wide
root logger and exposes `LoggingRuntime.close()` for deterministic shutdown and tests.

Every record has timestamp, severity, component, event code and a redacted message. Optional trace/task IDs accept
only bounded identifier characters. Details are recursively sanitized. Known credential assignments, Bearer values,
OpenAI/GitHub/JWT token shapes and URL queries are removed. Windows absolute paths become salted, non-reversible
per-process references. Values and sequences are bounded. Exception output retains type and sanitized message, not
the traceback or locals.

## Crash evidence and safe mode

`LocalCrashReporter` creates exclusive local JSON reports containing the application version, exception type,
sanitized message and at most 50 frames. Frame paths are hashed and source lines/locals are absent. No report is
uploaded. The newest ten reports are retained. Production does not chain the raw exception to a pre-existing unknown
exception hook; source development may explicitly retain normal console traceback behavior.

`CrashLoopGuard` writes an active-session marker only after the single-instance lock is acquired. A clean shutdown
removes it. Three recent unclean sessions in ten minutes recommend reduction-only safe mode. Corrupt health metadata
also reduces capability rather than granting authority. Safe mode constructs a separate minimal window, disables all
feature flags, LLM provider and Broker configuration, and exposes only local status, structural audit metadata,
settings and reviewed diagnostic export. It cannot prepare or execute business-domain plans.

## Diagnostic workflow

1. The user chooses a new local `.zip` path.
2. `prepare` snapshots finite, already-sanitized bytes in memory and returns an R1 Preview with exact entry hashes,
   byte counts, output path, exclusions and expiry. It creates no file.
3. The confirmation dialog defaults to No. Rejection is audited and discards volatile bytes.
4. Approval is audited before a single-use volatile authority is issued.
5. `export` revalidates target, authority, expiry and content digest, exclusively commits the ZIP and verifies exact
   members and bytes. Result audit is mandatory; if it fails, the newly created ZIP is removed.
6. The result states `automatically_uploaded=false` and recovery `MANUAL`.

The commit uses a same-directory temporary file and hard-link no-overwrite operation. A filesystem without hard-link
support fails closed and leaves no target; Stage 7A provides no overwrite or weaker fallback. Export cannot target a
network path, missing/reparse parent, existing file, ambiguous Windows path or non-ZIP suffix.

## Operational checks

```powershell
uv run pytest tests/unit/test_production_logging.py `
  tests/unit/test_crash_observability.py tests/unit/test_diagnostic_bundle.py -q
uv run pytest tests/unit/test_production_logging.py `
  --cov=pc_manager_agent.observability.logging --cov-fail-under=95 -q
uv run pytest tests/unit/test_crash_observability.py `
  --cov=pc_manager_agent.observability.crash --cov-fail-under=95 -q
uv run pytest tests/unit/test_diagnostic_bundle.py `
  --cov=pc_manager_agent.diagnostics.bundle --cov-fail-under=95 -q
```

Never attach real credentials or private user content to a test fixture. Automated tests use temporary directories,
synthetic errors and fake callback failures only.
