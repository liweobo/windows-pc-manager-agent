"""Synthetic Stage 4E3 authority, provenance, corruption and replay attacks."""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import update
from tests.fixtures.optimization_actions import FakeDomainPreparation, build_stage4e3_report
from tests.integration.test_stage4e3_review_flow import Environment
from tests.integration.test_stage4e3_review_flow import environment as environment

from pc_manager_agent.domain.optimization_actions import (
    OptimizationActionOutcome,
    OptimizationCapability,
    OptimizationOutcomeType,
    OptimizationSessionCreateRequest,
    OptimizationTargetDomain,
    PreparationStatus,
    RecommendationActionState,
)
from pc_manager_agent.domain.optimization_receipts import (
    DomainReceiptSnapshot,
    OptimizationReceiptKind,
    OptimizationTransactionReference,
)
from pc_manager_agent.domain.risk import RiskLevel, RollbackLevel
from pc_manager_agent.domain.system_optimization import CleanupCategory, RecommendationType
from pc_manager_agent.orchestration.optimization_action_resolver import RecommendationActionResolver
from pc_manager_agent.orchestration.optimization_capabilities import (
    OptimizationDomainCapabilityRegistry,
    capability_surface,
)
from pc_manager_agent.orchestration.optimization_handoffs import (
    DomainReviewContext,
    DomainReviewPreparationService,
    OptimizationHandoffStore,
)
from pc_manager_agent.orchestration.optimization_invalidation import (
    RecommendationInvalidationService,
)
from pc_manager_agent.orchestration.optimization_outcomes import OptimizationOutcomeCoordinator
from pc_manager_agent.persistence.optimization_sessions import (
    OptimizationSessionRow,
    OptimizationSessionStoreError,
)
from pc_manager_agent.safety.optimization_actions import (
    OptimizationActionPolicy,
    OptimizationRoutingError,
)
from pc_manager_agent.tools.manifest import CancellationToken
from pc_manager_agent.tools.system_tools.optimization_actions import (
    build_optimization_action_registry,
)


def test_review_core_has_no_platform_writer_or_shell_import():
    root = Path(__file__).resolve().parents[2] / "src" / "pc_manager_agent"
    files = [root / "app" / "optimization_reviews.py"]
    for folder in (
        "domain",
        "audit",
        "orchestration",
        "persistence",
        "safety",
        "tools/system_tools",
    ):
        # The pre-existing Stage 4E1 metadata collector has its own platform boundary tests.
        files.extend(
            path
            for path in (root / folder).glob("optimization*.py")
            if path.name != "optimization_evidence.py"
        )
    forbidden_imports = {
        "subprocess",
        "ctypes",
        "winreg",
        "win32api",
        "win32process",
        "win32service",
        "shutil",
    }
    forbidden_calls = {
        "eval",
        "exec",
        "Popen",
        "ShellExecuteEx",
        "TerminateProcess",
        "SHEmptyRecycleBinW",
        "RemovePackageAsync",
        "ChangeServiceConfig",
        "StartService",
        "ControlService",
        "unlink",
        "rmdir",
        "send2trash",
    }
    for path in files:
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            modules = []
            if isinstance(node, ast.Import):
                modules = [item.name for item in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                modules = [node.module]
            for module in modules:
                assert module.split(".")[0] not in forbidden_imports, path.name
                assert not module.startswith("pc_manager_agent.platform_support"), path.name
                assert not module.startswith("pc_manager_agent.privileged"), path.name
            if isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else node.func.attr
                    if isinstance(node.func, ast.Attribute)
                    else None
                )
                assert name not in forbidden_calls, path.name


def route_for(kind=RecommendationType.REVIEW_STARTUP_ITEM):
    report = build_stage4e3_report(kind)
    return report, RecommendationActionResolver(OptimizationActionPolicy()).resolve(
        report, report.recommendations[0].recommendation_id
    )


def create_session(env: Environment):
    return env.sessions.create(
        OptimizationSessionCreateRequest(
            source_report_id=env.report.report_id,
            recommendation_ids=tuple(item.recommendation_id for item in env.report.recommendations),
        )
    )


def test_handoff_one_shot_and_limits():
    report, route = route_for()
    store = OptimizationHandoffStore(maximum=1)
    adapter = DomainReviewPreparationService(
        OptimizationCapability.STARTUP_REVIEW,
        store,
        lambda route, _report, _token: DomainReviewContext(route=route),
    )
    assert adapter.available()
    prepared = adapter.prepare(route, report, CancellationToken())
    assert prepared.status is PreparationStatus.NEEDS_TARGET_SELECTION
    with pytest.raises(OptimizationRoutingError, match="CAPACITY"):
        adapter.prepare(route, report, CancellationToken())
    assert prepared.fresh_context_id
    with pytest.raises(OptimizationRoutingError, match="MISMATCH"):
        store.take(prepared.fresh_context_id, uuid4())
    with pytest.raises(OptimizationRoutingError, match="UNKNOWN"):
        store.take(prepared.fresh_context_id, route.route_id)
    prepared = adapter.prepare(route, report, CancellationToken())
    context = store.take(prepared.fresh_context_id, route.route_id)
    assert context.route == route
    store.save(context)
    store.clear()
    with pytest.raises(OptimizationRoutingError):
        store.take(context.context_id, route.route_id)
    for maximum in (0, 1_001):
        with pytest.raises(ValueError):
            OptimizationHandoffStore(maximum=maximum)
    with pytest.raises(ValueError):
        DomainReviewPreparationService(OptimizationCapability.NONE, store, lambda *_: context)
    with pytest.raises(OptimizationRoutingError, match="CAPABILITY"):
        adapter.prepare(
            route.model_copy(update={"target_capability": OptimizationCapability.PROCESS_REVIEW}),
            report,
            CancellationToken(),
        )
    cancelled = CancellationToken()
    cancelled.cancel()
    with pytest.raises(OptimizationRoutingError, match="CANCELLED"):
        adapter.prepare(route, report, cancelled)
    expired = context.model_copy(
        update={
            "route": route.model_copy(
                update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
            )
        }
    )
    with pytest.raises(OptimizationRoutingError, match="EXPIRED"):
        store.save(expired)
    store._contexts[expired.context_id] = expired
    with pytest.raises(OptimizationRoutingError, match="EXPIRED"):
        store.take(expired.context_id, route.route_id)


@pytest.mark.parametrize("attack", ["substitution", "late_cancel"])
def test_handoff_rejects_resolver_substitution_or_cancellation(attack):
    report, route = route_for()
    token = CancellationToken()

    def resolve(route, _report, token):
        if attack == "late_cancel":
            token.cancel()
        return DomainReviewContext(
            route=route
            if attack == "late_cancel"
            else route.model_copy(update={"route_id": uuid4()})
        )

    adapter = DomainReviewPreparationService(
        OptimizationCapability.STARTUP_REVIEW, OptimizationHandoffStore(), resolve
    )
    with pytest.raises(OptimizationRoutingError):
        adapter.prepare(route, report, token)


def test_invalidation_is_bounded_and_never_resurrects():
    report, route = route_for()
    with pytest.raises(ValueError):
        RecommendationInvalidationService(max_invalidations=0)
    for mode in ("snapshot", "target", "domain", "expired", "saturated"):
        invalidation = RecommendationInvalidationService(max_invalidations=1)
        if mode == "snapshot":
            invalidation.invalidate_by_snapshot(route.source_snapshot_id)
        elif mode == "target":
            invalidation.invalidate_by_target_identity(
                route.source_report_id, (route.recommendation_id,)
            )
        elif mode == "domain":
            invalidation.invalidate_by_domain(route.target_domain)
        elif mode == "expired":
            invalidation = RecommendationInvalidationService(now=lambda: route.expires_at)
        else:
            invalidation.invalidate_by_snapshot(uuid4())
            invalidation.invalidate_by_snapshot(uuid4())
        with pytest.raises(OptimizationRoutingError, match="STALE"):
            invalidation.require_current(route, report)


def test_registry_never_falls_back_or_accepts_changed_result():
    report, route = route_for()
    with pytest.raises(OptimizationRoutingError):
        capability_surface(OptimizationCapability.NONE)
    registry = OptimizationDomainCapabilityRegistry()
    with pytest.raises(OptimizationRoutingError):
        registry.available(OptimizationCapability.STARTUP_REVIEW)
    registry.seal()
    assert registry.capabilities == ()
    with pytest.raises(OptimizationRoutingError):
        registry.prepare(route, report, CancellationToken())
    for attack in ("route", "surface", "context", "late_cancel"):
        adapter = FakeDomainPreparation(OptimizationCapability.STARTUP_REVIEW)
        original = adapter.prepare

        def prepare(route, report, token, attack=attack, original=original):
            result = original(route, report, token)
            if attack == "late_cancel":
                token.cancel()
                return result
            return result.model_copy(
                update={"route_id": uuid4()}
                if attack == "route"
                else {"next_ui_surface": capability_surface(OptimizationCapability.PROCESS_REVIEW)}
                if attack == "surface"
                else {"fresh_context_id": None}
            )

        adapter.prepare = prepare
        registry = OptimizationDomainCapabilityRegistry()
        registry.register(adapter)
        registry.seal()
        assert registry.capabilities == (OptimizationCapability.STARTUP_REVIEW,)
        with pytest.raises(OptimizationRoutingError):
            registry.prepare(route, report, CancellationToken())


@pytest.mark.parametrize("attack", ["before_prepare", "during_prepare", "removed"])
def test_router_rechecks_report_after_every_boundary(environment, monkeypatch, attack):
    env = environment
    original_get = env.reports.get
    original_prepare = env.provider.prepare
    calls = 0

    def get(report_id):
        nonlocal calls
        calls += 1
        report = original_get(report_id)
        if attack == "before_prepare" and calls == 2:
            return report.model_copy(update={"plan_id": uuid4()})
        return report

    def prepare(route, report, token):
        result = original_prepare(route, report, token)
        if attack == "during_prepare":
            env.reports.save(report.model_copy(update={"plan_id": uuid4()}))
        elif attack == "removed":
            env.reports.clear()
        return result

    monkeypatch.setattr(env.reports, "get", get)
    monkeypatch.setattr(env.provider, "prepare", prepare)
    with pytest.raises(OptimizationRoutingError):
        env.router.prepare(env.reference)


@pytest.mark.parametrize(
    "attack", ["digest", "payload", "columns", "missing", "duplicate", "revision"]
)
def test_journal_corruption_and_replacement_fail_closed(environment, attack):
    session = create_session(environment)
    if attack == "missing":
        with pytest.raises(OptimizationSessionStoreError):
            environment.repository.get(uuid4())
    elif attack == "duplicate":
        with pytest.raises(OptimizationSessionStoreError):
            environment.repository.create(session)
    elif attack == "revision":
        with pytest.raises(OptimizationSessionStoreError):
            environment.repository.create(session.model_copy(update={"revision": 2}))
        with pytest.raises(OptimizationSessionStoreError):
            environment.repository.save(session, expected_revision=1)
    else:
        import hashlib

        change = (
            {"digest": "0" * 64}
            if attack == "digest"
            else {"payload": "{}", "digest": hashlib.sha256(b"{}").hexdigest()}
            if attack == "payload"
            else {"status": "COMPLETED"}
        )
        with environment.repository._sessions.begin() as db:
            db.execute(
                update(OptimizationSessionRow)
                .where(OptimizationSessionRow.session_id == str(session.session_id))
                .values(**change)
            )
        with pytest.raises(OptimizationSessionStoreError):
            environment.repository.get(session.session_id)


@pytest.mark.parametrize(
    "action",
    [
        "skip",
        "close_wrong",
        "cancel_twice",
        "stale",
        "duplicate",
        "prepare_failure",
        "cancel_during",
        "missing_item",
    ],
)
def test_session_denials_and_cancellation(environment, monkeypatch, action):
    env = environment
    session = create_session(env)
    rec = session.items[0].recommendation_id
    if action == "skip":
        env.sessions.skip(session.session_id, rec)
        with pytest.raises(OptimizationRoutingError):
            env.sessions.skip(session.session_id, rec)
    elif action == "missing_item":
        with pytest.raises(OptimizationRoutingError):
            env.sessions.prepare(session.session_id, uuid4())
    elif action == "close_wrong":
        env.sessions.prepare(session.session_id, rec)
        with pytest.raises(OptimizationRoutingError):
            env.sessions.close_review(session.session_id, rec, uuid4())
    elif action == "cancel_twice":
        env.sessions.cancel(session.session_id)
        env.sessions.cancel(session.session_id)
        with pytest.raises(OptimizationRoutingError):
            env.sessions.prepare(session.session_id, rec)
    elif action == "stale":
        env.invalidation.invalidate_by_snapshot(env.report.snapshot.snapshot_id)
        with pytest.raises(OptimizationRoutingError):
            env.sessions.prepare(session.session_id, rec)
        assert (
            env.sessions.get(session.session_id).items[0].state is RecommendationActionState.STALE
        )
    elif action == "duplicate":
        with pytest.raises(OptimizationRoutingError):
            env.sessions.create(
                OptimizationSessionCreateRequest(
                    source_report_id=env.report.report_id, recommendation_ids=(rec, rec)
                )
            )
    else:
        original = env.provider.prepare

        def prepare(route, report, token):
            if action == "prepare_failure":
                raise OptimizationRoutingError("SYNTHETIC_FAILURE")
            result = original(route, report, token)
            env.sessions.cancel(session.session_id)
            return result

        monkeypatch.setattr(env.provider, "prepare", prepare)
        with pytest.raises(OptimizationRoutingError):
            env.sessions.prepare(session.session_id, rec)
        assert env.sessions.get(session.session_id).items[0].state in {
            RecommendationActionState.FAILED,
            RecommendationActionState.SKIPPED,
        }


def outcome_setup(env):
    session = create_session(env)
    preparation = env.sessions.prepare(session.session_id, session.items[0].recommendation_id)
    route = env.router.inspect(env.reference).model_copy(update={"route_id": preparation.route_id})
    context = DomainReviewContext(route=route)
    reference = OptimizationTransactionReference(
        kind=OptimizationReceiptKind.STARTUP, transaction_id=uuid4()
    )
    snapshot = DomainReceiptSnapshot(
        reference=reference,
        plan_id=uuid4(),
        plan_digest="a" * 64,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        state="PREVIEWED",
        risk=RiskLevel.R2,
        recovery=RollbackLevel.FULL,
    )
    reader = SimpleNamespace(snapshot=snapshot)
    reader.read = lambda _reference: reader.snapshot
    coordinator = OptimizationOutcomeCoordinator(reader, env.sessions, env.invalidation)
    return session, preparation, context, reference, reader, coordinator


@pytest.mark.parametrize("outcome", list(OptimizationOutcomeType))
def test_correlated_outcomes_are_domain_owned_and_not_global_undo(environment, outcome):
    session, prep, context, reference, reader, coordinator = outcome_setup(environment)
    assert coordinator.collect(uuid4()) is None
    coordinator.bind(session.session_id, prep, context, reference)
    assert coordinator.collect(prep.handoff_id) is None
    with pytest.raises(OptimizationRoutingError):
        coordinator.bind(session.session_id, prep, context, reference)
    reader.snapshot = reader.snapshot.model_copy(
        update={
            "state": "COMPLETED",
            "outcome": outcome,
            "confirmation_id": uuid4(),
            "updated_at": datetime.now(UTC),
        }
    )
    if outcome is OptimizationOutcomeType.APPLIED_VERIFIED:
        environment.sessions.cancel(session.session_id)
    result = coordinator.collect(prep.handoff_id)
    assert result.outcome is outcome
    saved = environment.sessions.get(session.session_id)
    assert saved.items[0].outcome == result
    assert saved.global_undo_available is False
    assert coordinator.collect(prep.handoff_id) is None
    with pytest.raises(OptimizationRoutingError):
        environment.sessions.accept_correlated_outcome(session.session_id, result)
    assert environment.audit.list_recent(1)[0].event_type == "optimization.action.outcome"


@pytest.mark.parametrize(
    "attack",
    [
        "cancelled",
        "wrong_handoff",
        "wrong_route",
        "wrong_domain",
        "expired",
        "historical",
        "completed",
        "dispatching",
        "changed_plan",
        "late_risk",
    ],
)
def test_result_correlation_rejects_stale_or_substituted_authority(environment, attack):
    session, prep, context, reference, reader, coordinator = outcome_setup(environment)
    if attack == "cancelled":
        environment.sessions.cancel(session.session_id)
    elif attack == "wrong_handoff":
        prep = prep.model_copy(update={"handoff_id": uuid4()})
    elif attack == "wrong_route":
        context = context.model_copy(
            update={"route": context.route.model_copy(update={"route_id": uuid4()})}
        )
    elif attack == "wrong_domain":
        reference = reference.model_copy(update={"kind": OptimizationReceiptKind.MSI})
    elif attack == "expired":
        context = context.model_copy(
            update={
                "route": context.route.model_copy(
                    update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)}
                )
            }
        )
    elif attack == "historical":
        reader.snapshot = reader.snapshot.model_copy(
            update={"created_at": context.created_at - timedelta(seconds=1)}
        )
    elif attack == "completed":
        reader.snapshot = reader.snapshot.model_copy(
            update={"outcome": OptimizationOutcomeType.FAILED}
        )
    elif attack == "dispatching":
        reader.snapshot = reader.snapshot.model_copy(update={"state": "EXECUTING"})
    if attack in {"changed_plan", "late_risk"}:
        coordinator.bind(session.session_id, prep, context, reference)
        reader.snapshot = reader.snapshot.model_copy(
            update={"plan_id": uuid4()} if attack == "changed_plan" else {"risk": RiskLevel.R3}
        )
        with pytest.raises(OptimizationRoutingError):
            coordinator.collect(prep.handoff_id)
    else:
        with pytest.raises(OptimizationRoutingError):
            coordinator.bind(session.session_id, prep, context, reference)


def test_review_tool_names_and_cancellation(environment):
    registry = build_optimization_action_registry(environment.router, environment.sessions)
    route = registry.execute(
        "optimization.recommendation.inspect",
        environment.reference.model_dump(mode="json"),
        CancellationToken(),
    )
    assert not route.causes_system_change
    created = registry.execute(
        "optimization.session.create",
        {
            "source_report_id": str(environment.report.report_id),
            "recommendation_ids": [str(environment.reference.recommendation_id)],
        },
        CancellationToken(),
    )
    refreshed = registry.execute(
        "optimization.session.refresh", {"session_id": str(created.session_id)}, CancellationToken()
    )
    assert refreshed == created
    token = CancellationToken()
    token.cancel()
    with pytest.raises(PermissionError):
        registry.execute(
            "optimization.recommendation.inspect",
            environment.reference.model_dump(mode="json"),
            token,
        )


def test_model_copy_cannot_turn_route_into_authority():
    _report, route = route_for()
    for fields in (
        {"target_domain": OptimizationTargetDomain.SOFTWARE},
        {"target_capability": OptimizationCapability.NONE},
    ):
        with pytest.raises(ValidationError):
            type(route).model_validate(route.model_dump() | fields)
    with pytest.raises(ValidationError):
        OptimizationActionOutcome(
            recommendation_id=uuid4(),
            handoff_id=uuid4(),
            target_domain=OptimizationTargetDomain.STARTUP,
            outcome=OptimizationOutcomeType.FAILED,
            verified=True,
            summary_code="FAILED",
        )


@pytest.mark.parametrize(
    "kind,change,expected",
    [
        (
            RecommendationType.REVIEW_SOFTWARE_RESIDUAL,
            {"source_reference": None},
            "RESIDUAL_PROVENANCE_REQUIRED",
        ),
        (
            RecommendationType.REVIEW_LARGE_FILES,
            {"category": CleanupCategory.DUPLICATE_FILE},
            "PERSONAL_CATEGORY_MISMATCH",
        ),
        (
            RecommendationType.REVIEW_LARGE_FILES,
            {"source_reference": None},
            "PERSONAL_PROVENANCE_REQUIRED",
        ),
        (
            RecommendationType.REVIEW_RECYCLE_BIN,
            {"category": CleanupCategory.USER_TEMP},
            "BIN_CATEGORY_MISMATCH",
        ),
    ],
)
def test_candidate_routes_deny_wrong_provenance_and_category(kind, change, expected):
    report, _route = route_for(kind)
    report = report.model_copy(
        update={"cleanup_candidates": (report.cleanup_candidates[0].model_copy(update=change),)}
    )
    decision = OptimizationActionPolicy().evaluate(report, report.recommendations[0])
    assert decision.reason_codes == (expected,)


def test_finding_cannot_substitute_for_candidate_and_unknown_kind_is_denied():
    report, _route = route_for()
    rec = report.recommendations[0].model_copy(
        update={"recommendation_type": RecommendationType.REVIEW_TEMP_STORAGE}
    )
    assert OptimizationActionPolicy().evaluate(report, rec).reason_codes == (
        "CANDIDATE_EVIDENCE_REQUIRED",
    )
    assert OptimizationActionPolicy()._candidate_route(
        RecommendationType.MANUAL_REVIEW, ()
    ).reason_codes == ("UNSUPPORTED_RECOMMENDATION",)


@pytest.mark.parametrize(
    "kind,capability",
    [
        (OptimizationReceiptKind.RECYCLE_BIN, OptimizationCapability.CLEANUP_REVIEW),
        (OptimizationReceiptKind.CLEANUP, OptimizationCapability.RECYCLE_BIN_REVIEW),
    ],
)
def test_bin_empty_and_item_cleanup_never_share_handoff(environment, kind, capability):
    session, prep, context, reference, _reader, coordinator = outcome_setup(environment)
    prep = prep.model_copy(update={"target_domain": OptimizationTargetDomain.SYSTEM_CLEANUP})
    context = context.model_copy(
        update={
            "route": context.route.model_copy(
                update={
                    "target_capability": capability,
                    "target_domain": OptimizationTargetDomain.SYSTEM_CLEANUP,
                }
            )
        }
    )
    reference = reference.model_copy(update={"kind": kind})
    with pytest.raises(OptimizationRoutingError, match="INDEPENDENT_REVIEW"):
        coordinator.bind(session.session_id, prep, context, reference)
