# Developer guide

## Setup and verification

```powershell
uv sync --all-groups
uv run ruff format .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest --cov=pc_manager_agent --cov-report=term-missing --cov-fail-under=85
uv run bandit -q -r src
uv run pip-audit
uv build
uv run python -m pc_manager_agent --smoke-test
```

Tests use temporary roots and SQLite databases. They do not contact OpenAI or
modify real user files. GUI tests use Qt's offscreen platform.

## Adding a tool

1. Define strict Pydantic input/output models.
2. Write a complete `ToolManifest` with truthful risk and rollback.
3. Implement deterministic execution with cancellation and bounds.
4. Add path/permission checks at execution time, not only planning time.
5. Extend Safety Reviewer checks if the tool adds new authority.
6. Add unit, integration, security, audit, failure, and rollback tests.
7. Update all affected documentation and CHANGELOG.

Never add a generic shell tool or use model output as a command.

## OpenAI adapter

The adapter calls `responses.parse(..., text_format=TaskPlan)` and then returns a
provider-neutral result. Callers must separately obtain consent for any external
data upload, apply `SafetyReviewer`, and use deterministic confirmation/execution.
The stage 0 UI deliberately does not call this adapter.

## Git discipline

Work from `codex/*`, `fix/*`, or `docs/*`. Inspect status and diff, run all checks,
scan for credentials, then create one complete logical commit. Push normally and
report the exact SHA. Roll back with `git revert`.
