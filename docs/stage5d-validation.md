# Stage 5D validation record

Validated locally on Windows 11, Python 3.13.1, 2026-09-02. Automated tests used temporary SQLite databases,
fake providers, fake Agents, synthetic Context/Memory and existing fake domain adapters. No real destructive action,
UAC, microphone capture, paid model request or Recycle Bin emptying was performed.

## Results

| Check | Command summary | Result |
|---|---|---|
| Formatting | `uv run ruff format --check .` | 825 files already formatted |
| Lint | `uv run ruff check .` | passed |
| Types | `uv run mypy src` | 525 source files, passed |
| Full non-performance tests | pytest with branch coverage and 85% gate | 1672 passed, 6 skipped, 11 deselected; 87.25% |
| Stage 5D core gate | 59 Agent/Context/Memory/integration/security/GUI tests | passed; 87.60% |
| Stage 5D critical gate | 55 policy/Context tests with 95% gate | passed; 97.68% |
| Performance | `uv run pytest tests/performance -q -s` | 11 passed in 213.39s |
| Static security | `uv run bandit -q -r src` | passed, no findings |
| Dependency audit | `uv run pip-audit` | no known vulnerabilities; local project is not on PyPI |
| Build | `uv build` | wheel and source archive built successfully |
| Startup | `uv run python -m pc_manager_agent --smoke-test` | exit code 0 |
| Secret scan | local `gitleaks` | unavailable on this host; staged-file pattern review required before commit; GitHub gitleaks pending |

## Expected skips

Five symlink/reparse tests require a Windows privilege not enabled for this user. The real Recycle Bin integration
test is explicitly opt-in and remained skipped. Equivalent fake and policy tests passed. These skips do not imply
that real destructive operations were attempted.

## Manual follow-up

- Check Task Center and Memory layouts at normal Windows DPI and high DPI.
- Confirm that closing/reopening the app marks an active task INTERRUPTED and does not repopulate a goal body.
- Confirm common preference values are understandable to a beginner; unsafe/secret values should show a refusal.
- After pushing, wait for GitHub Actions, including the official gitleaks job. Do not describe CI as passed until it
  finishes successfully.
