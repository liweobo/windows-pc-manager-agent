# Stage 5E validation record

Validated locally on Windows 11, Python 3.13.1, 2026-09-07. Automated tests used temporary SQLite databases,
sealed fake domain workflows, synthetic receipts and offscreen Qt. No real destructive operation, UAC elevation,
paid provider request, user document write, browser transaction or Recycle Bin emptying was performed.

## Results

| Check | Command summary | Result |
|---|---|---|
| Formatting | `uv run ruff format --check .` | 861 files already formatted |
| Lint | `uv run ruff check .` | passed |
| Types | `uv run mypy src` | 545 source files, passed |
| Full non-performance tests | pytest excluding performance and Playwright, with branch coverage and 85% gate | 1699 passed, 6 skipped, 13 deselected; 86.71% |
| Cross-stage security gate | all security boundaries plus Stage 5D/5E policy suites | 1035 passed, 3 skipped; 95.71% |
| Stage 5E core gate | 29 task/orchestrator/recovery/fake-E2E/security/GUI tests | passed; 86.57% |
| Stage 5E critical gate | 12 safety/checkpoint/workflow-boundary tests with 95% gate | passed; 97.70% |
| Performance | `uv run pytest tests/performance -q -s` | 11 passed in 174.91s |
| Static security | `uv run bandit -q -r src` | passed, no findings |
| Dependency audit | `uv run pip-audit` | no known vulnerabilities; the local project itself is not published on PyPI |
| Build | `uv build` | wheel and source archive built successfully |
| Startup | offscreen `uv run python -m pc_manager_agent --smoke-test` | exit code 0 |
| Secret scan | local staged-file pattern review plus GitHub gitleaks workflow | local review is required immediately before commit; remote result remains pending until push |

## Expected skips

Five symlink/reparse tests require a Windows privilege not enabled for this standard user. The real Recycle Bin
integration test is explicitly opt-in and remained skipped. Equivalent fake, identity and policy tests passed.
These skips do not imply that real destructive actions were attempted.

## Boundary verified by automation

- A task-level confirmation binds only R0 coordination nodes; it cannot authorize an owning-domain write.
- Dispatch reservation is unique and write-capable work cannot be automatically retried.
- Restart invalidates pending task confirmations, marks active work interrupted and never restores authority.
- Crash reconciliation requires an opaque owning-domain transaction reference and prohibits replay.
- Pause, resume and cancellation stop future coordination only; they never kill an external process or undo work.
- Deterministic summaries accept only validated owning-domain receipts and never expose a global Undo.
- Home and tray actions create or navigate to one task; neither supplies approval or bulk confirmation.

## Manual follow-up

- Exercise the five Home templates at normal and high Windows DPI and confirm every one stops at plan review.
- Close and reopen the app with a task waiting for domain review; verify the task is INTERRUPTED and requires Fresh
  review without redispatch.
- Confirm tray attention opens Task Center but cannot accept any plan or domain confirmation.
- Complete the owning domain's existing manual Windows validation checklists independently. Stage 5E does not make
  those earlier domain implementations production-certified.
- After pushing, inspect the final GitHub Actions run, including gitleaks. Do not describe remote CI as passed until
  that run finishes successfully.
