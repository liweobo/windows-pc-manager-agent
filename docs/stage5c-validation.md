# Stage 5C validation record

## Automated scope

Stage 5C tests use fake DNS, a fake browser adapter, temporary SQLite databases/directories and a synthetic
Playwright route. They cover model invariants, protocol shapes, SSRF/origin policy, prompt-injection signals,
transactional-action denial, observation limits, durable confirmation binding/replay/expiry/restart,
download type/size/magic/no-overwrite/recovery rules, cancellation, audit minimization, GUI planning and the
Browser-to-Office non-authoritative handoff.

The normal suite excludes the real Playwright contract through the `playwright` marker. Windows CI installs
managed Chromium and runs that contract separately. The 85-percent Stage 5C core gate measures domain,
orchestration, tools, audit, IPC protocol, Worker and Main client. The OS/browser-bound Playwright adapter is
required to pass its real synthetic Chromium contract but is not included in the line-percentage denominator;
faking browser internals merely to increase line coverage would not validate Chromium behavior. The separate
safety/confirmation coverage gate remains at 95 percent.

## Commands

```powershell
uv run ruff format .
uv run ruff check .
uv run mypy src
$env:QT_QPA_PLATFORM = "offscreen"
uv run pytest -m "not performance and not playwright" --cov=pc_manager_agent --cov-report=term-missing --cov-fail-under=85
uv run pytest tests/unit/browser tests/integration/browser/test_browser_workflow.py tests/security/test_browser_boundaries.py --cov=pc_manager_agent.safety.browser --cov=pc_manager_agent.confirmation.browser --cov=pc_manager_agent.persistence.browser --cov-report=term-missing --cov-fail-under=95
uv run pytest tests/integration/browser/test_playwright_contract.py -q
uv run pytest tests/performance -q -s
uv run bandit -q -r src
uv run pip-audit
uv build
uv run python -m pc_manager_agent --smoke-test
```

## Local evidence — 2026-09-01

- Full non-performance/non-Playwright regression: 1,613 passed, 6 skipped, 13 marker-deselected; total
  coverage 86.54%, above the 85% gate. Skips were the opt-in real Recycle Bin test and Windows environments
  without symlink/reparse creation privilege; deterministic denial branches still ran.
- Stage 5C core gate: 78 passed; 86.12% across domain, orchestration, tools, audit, IPC protocol, Worker,
  Main client, safety, confirmation and persistence.
- Browser safety/confirmation gate: 74 passed; 95.32% combined coverage.
- Managed Chromium synthetic adapter plus real isolated Worker-client lifecycle: 3 passed, including strict
  `ResourceWarning` handling. No private/public real site or user account was contacted.
- Performance suite: 11 passed in 236.75 seconds. The maximum synthetic browser observation used 5,000
  semantic references and 40,000 characters in 0.882 seconds with 6.88 MiB traced Python peak memory.
- Ruff format/check, mypy, Bandit, `pip-audit`, wheel/sdist build and offscreen smoke startup passed.
  `pip-audit` found no known dependency vulnerability; the local project itself is correctly skipped because
  it is not a published PyPI dependency.

Remote GitHub Actions evidence is reported separately after the exact commit is pushed; local success is not
described as remote CI success.

## Deliberately unverified by automation

- Real account login, password entry, SSO, CAPTCHA, payment or transaction pages are not tested and are not
  authorized Stage 5C capabilities.
- Public website compatibility is not guaranteed; sites can change semantics or block automation.
- The download policy checks identity, type, size and magic bytes. It is not a malware scanner.
- URL/DNS checks are application-layer controls, not an OS network sandbox or proof against every DNS race.
- Manual takeover needs a separate visible-Windows exercise; automation tests only its state/authority model.
- External model summarization remains disabled and no real model API request belongs in this validation.

Use [the manual checklist](manual-testing/browser-automation.md) for a cautious visible-browser exercise.
