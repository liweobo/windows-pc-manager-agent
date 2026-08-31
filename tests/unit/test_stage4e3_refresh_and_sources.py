"""Source-only preparation and targeted refresh tests; no live machine actions."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pc_manager_agent.app.optimization_reviews import RepositoryDomainReviewResolver
from pc_manager_agent.domain.optimization_actions import OptimizationTargetDomain
from pc_manager_agent.domain.process_actions import ProcessTargetQuery, ProcessTargetQueryType
from pc_manager_agent.domain.system_diagnostics import (
    CollectorOutcome,
    CollectorState,
    DiskKind,
    DiskSnapshot,
    MemorySnapshot,
    SystemCollector,
    SystemSnapshot,
)
from pc_manager_agent.domain.system_optimization import RecommendationType
from pc_manager_agent.orchestration.optimization_refresh import (
    compare_optimization_observations,
    optimization_refresh_goal,
)
from pc_manager_agent.orchestration.process_target_resolver import ProcessTargetResolver
from pc_manager_agent.safety.optimization_actions import OptimizationRoutingError
from pc_manager_agent.tools.manifest import CancellationToken
from tests.security.test_stage4e3_boundaries import route_for
from tests.stage4a_support import FakeProcessPlatform, process_observation


@pytest.mark.parametrize("domain", list(OptimizationTargetDomain))
def test_refresh_is_one_fixed_readonly_intent(domain):
    if domain is OptimizationTargetDomain.UNSUPPORTED:
        with pytest.raises(OptimizationRoutingError):
            optimization_refresh_goal(domain)
    else:
        goal = optimization_refresh_goal(domain)
        assert goal.startswith("查看")
        assert "健康检查" not in goal


@pytest.mark.parametrize(
    "domain,collector",
    [
        (OptimizationTargetDomain.STARTUP, SystemCollector.STARTUP),
        (OptimizationTargetDomain.PROCESS, SystemCollector.MEMORY),
        (OptimizationTargetDomain.SOFTWARE, SystemCollector.SOFTWARE),
        (OptimizationTargetDomain.SYSTEM_CLEANUP, SystemCollector.DISKS),
        (OptimizationTargetDomain.SOFTWARE_RESIDUAL, SystemCollector.DISKS),
        (OptimizationTargetDomain.PERSONAL_STORAGE, SystemCollector.DISKS),
    ],
)
@pytest.mark.parametrize("available", [True, False])
def test_short_measurements_never_claim_causation(domain, collector, available):
    now = datetime.now(UTC)
    outcomes = (
        CollectorOutcome(
            collector=collector,
            state=CollectorState.SUCCEEDED if available else CollectorState.PARTIAL,
        ),
    )
    memory = MemorySnapshot(total_bytes=1000, available_bytes=500, used_bytes=500, used_percent=50)
    disk = DiskSnapshot(
        device="C:",
        mountpoint=Path("C:/"),
        kind=DiskKind.FIXED,
        total_bytes=1000,
        free_bytes=500,
        used_bytes=500,
        used_percent=50,
    )
    before = SystemSnapshot(collected_at=now, outcomes=outcomes, memory=memory, disks=(disk,))
    after = before.model_copy(update={"collected_at": now + timedelta(seconds=1)})
    observations = compare_optimization_observations(domain, before, after)
    assert len(observations) == 1
    assert observations[0].measured is available
    assert observations[0].caused_by_action is False
    assert observations[0].attribution_confidence.value == "UNKNOWN"
    with pytest.raises(OptimizationRoutingError):
        compare_optimization_observations(domain, after, before)
    assert compare_optimization_observations(OptimizationTargetDomain.SERVICE, before, after) == ()
    if collector is SystemCollector.DISKS:
        changed = after.model_copy(
            update={"disks": (disk.model_copy(update={"total_bytes": 2000}),)}
        )
        assert not compare_optimization_observations(domain, before, changed)[0].measured


@pytest.mark.parametrize("changed", ["time", "path", "none"])
@pytest.mark.parametrize("group", [True, False])
def test_selected_pid_cannot_be_reused_by_replacement_process(changed, group):
    observation = process_observation()
    platform = FakeProcessPlatform((observation,))
    query = ProcessTargetQuery(
        query_type=ProcessTargetQueryType.SELECTED_PROCESS,
        pid=observation.identity.pid,
        include_application_group=group,
        expected_create_time=observation.identity.create_time + timedelta(seconds=1)
        if changed == "time"
        else observation.identity.create_time,
        expected_executable_path=Path("C:/different.exe")
        if changed == "path"
        else observation.identity.executable_path,
    )
    resolver = ProcessTargetResolver(platform)
    if changed == "none":
        assert resolver.resolve(query)
    else:
        with pytest.raises(Exception, match="identity changed"):
            resolver.resolve(query)
    assert platform.graceful_calls == []


@pytest.mark.parametrize(
    "kind",
    [
        RecommendationType.REVIEW_TEMP_STORAGE,
        RecommendationType.REVIEW_STARTUP_ITEM,
        RecommendationType.REVIEW_HIGH_RESOURCE_PROCESS,
        RecommendationType.REVIEW_INSTALLED_SOFTWARE,
    ],
)
def test_generic_prepare_never_selects_a_target_or_contacts_a_model(kind):
    report, route = route_for(kind)
    context = RepositoryDomainReviewResolver(SimpleNamespace()).resolve(
        route, report, CancellationToken()
    )
    assert context.route == route
    assert context.authorized_root_ids == ()
    assert context.uninstall_transaction_id is None


@pytest.mark.parametrize(
    "attack",
    ["none", "missing_report", "missing_candidate", "ineligible", "wrong_transaction", "cancelled"],
)
def test_residual_prepare_uses_exact_uninstall_provenance(attack):
    report, route = route_for(RecommendationType.REVIEW_SOFTWARE_RESIDUAL)
    candidate = report.cleanup_candidates[0]
    source = candidate.source_reference
    tx = uuid4()
    upstream = SimpleNamespace(
        context_id=uuid4(),
        uninstall_transaction_id=tx,
        candidates=(SimpleNamespace(candidate_id=source.upstream_candidate_id),),
    )
    context = SimpleNamespace(
        eligible_for_analysis=attack != "ineligible",
        transaction_id=uuid4() if attack == "wrong_transaction" else tx,
    )
    repo = SimpleNamespace(get_report=lambda _id: upstream, get_context=lambda _id: context)
    if attack == "missing_report":
        candidate = candidate.model_copy(update={"source_reference": None})
        report = report.model_copy(update={"cleanup_candidates": (candidate,)})
    elif attack == "missing_candidate":
        upstream.candidates = ()
    token = CancellationToken()
    if attack == "cancelled":
        token.cancel()
    resolver = RepositoryDomainReviewResolver(SimpleNamespace(software_residual_repository=repo))
    if attack == "none":
        assert resolver.resolve(route, report, token).uninstall_transaction_id == tx
    else:
        with pytest.raises(OptimizationRoutingError):
            resolver.resolve(route, report, token)


@pytest.mark.parametrize("attack", ["none", "missing_record", "changed", "revoked", "empty"])
def test_personal_prepare_never_widens_authorized_roots(attack):
    report, route = route_for(RecommendationType.REVIEW_LARGE_FILES)
    candidate = report.cleanup_candidates[0]
    root = Path("C:/Approved")
    root_id = uuid4()
    record = SimpleNamespace(metadata=SimpleNamespace(path=candidate.path, scan_root=root))
    if attack == "changed":
        record.metadata.path = Path("C:/another.txt")
    if attack == "missing_record":
        report = report.model_copy(
            update={
                "cleanup_candidates": (candidate.model_copy(update={"source_reference": None}),)
            }
        )
    if attack == "empty":
        report = report.model_copy(update={"cleanup_candidates": ()})
    paths = SimpleNamespace(
        list_authorized=lambda: (
            () if attack == "revoked" else (SimpleNamespace(path=root, path_id=root_id),)
        ),
        require_authorized_file=lambda path: path,
    )
    resolver = RepositoryDomainReviewResolver(
        SimpleNamespace(
            authorized_paths=paths, analysis_results=SimpleNamespace(get_record=lambda _id: record)
        )
    )
    if attack == "none":
        assert resolver.resolve(route, report, CancellationToken()).authorized_root_ids == (
            root_id,
        )
    else:
        with pytest.raises(OptimizationRoutingError):
            resolver.resolve(route, report, CancellationToken())
