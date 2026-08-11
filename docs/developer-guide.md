# Developer guide

## Setup and verification

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

Tests create temporary local trees and SQLite databases. They do not modify real user
files or contact OpenAI. Qt runs offscreen. The benchmark creates 10,000 empty synthetic
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
