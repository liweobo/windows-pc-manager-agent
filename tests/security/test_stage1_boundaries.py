from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentError,
    ExternalDataConsentService,
    ExternalDataPurpose,
)
from pc_manager_agent.domain.file_analysis import (
    AnalysisType,
    FileAnalysisFilters,
    FileAnalysisIntentDraft,
)
from pc_manager_agent.domain.plans import EstimatedImpact
from pc_manager_agent.orchestration.file_analysis_planner import FileAnalysisPlanCompiler
from pc_manager_agent.providers.llm.base import (
    FileAnalysisPlannerRequest,
    LLMProvider,
    PlannerRequest,
    ProviderIntentResult,
    ProviderPlanResult,
)
from pc_manager_agent.tools.registry import ToolRegistry


class CapturingProvider(LLMProvider):
    """Provider fake that returns one validated but still untrusted intent."""

    def __init__(self, intent: FileAnalysisIntentDraft) -> None:
        self.intent = intent
        self.request: FileAnalysisPlannerRequest | None = None

    @property
    def name(self) -> str:
        return "capturing-provider"

    async def create_plan(self, request: PlannerRequest) -> ProviderPlanResult:
        raise AssertionError(f"Legacy planner must not be called: {request}")

    async def create_file_analysis_intent(
        self,
        request: FileAnalysisPlannerRequest,
    ) -> ProviderIntentResult:
        self.request = request
        return ProviderIntentResult(
            intent=self.intent,
            provider=self.name,
            request_id="trace-1",
        )


def compile_plan(runtime: ApplicationRuntime, root: Path):  # type: ignore[no-untyped-def]
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_analysis_services()
    plan = services.compiler.compile(
        "只读分析",
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            filters=FileAnalysisFilters(minimum_size_bytes=100, inactive_days=90),
            analyses=(AnalysisType.LARGE_FILES, AnalysisType.INACTIVE_FILES),
        ),
    )
    return record, services, plan


@pytest.mark.security
def test_validator_rejects_impact_session_threshold_and_step_mutations(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    _record, services, plan = compile_plan(runtime, root)

    changed_impact = plan.task_plan.model_copy(
        update={"estimated_impact": EstimatedImpact(files_modified=1, files_deleted=0)}
    )
    session_step = plan.task_plan.steps[0].model_copy(
        update={
            "arguments": {
                **plan.task_plan.steps[0].arguments,
                "session_id": str(uuid4()),
            }
        }
    )
    threshold_step = plan.task_plan.steps[1].model_copy(
        update={
            "arguments": {
                **plan.task_plan.steps[1].arguments,
                "minimum_size_bytes": 101,
            }
        }
    )
    inactive_step = plan.task_plan.steps[2].model_copy(
        update={
            "arguments": {
                **plan.task_plan.steps[2].arguments,
                "inactive_days": 91,
            }
        }
    )
    mutated_plans = (
        plan.model_copy(update={"task_plan": changed_impact}),
        plan.model_copy(
            update={
                "task_plan": plan.task_plan.model_copy(
                    update={"steps": (session_step, *plan.task_plan.steps[1:])}
                )
            }
        ),
        plan.model_copy(
            update={
                "task_plan": plan.task_plan.model_copy(
                    update={
                        "steps": (
                            plan.task_plan.steps[0],
                            threshold_step,
                            plan.task_plan.steps[2],
                        )
                    }
                )
            }
        ),
        plan.model_copy(
            update={
                "task_plan": plan.task_plan.model_copy(
                    update={
                        "steps": (
                            plan.task_plan.steps[0],
                            plan.task_plan.steps[1],
                            inactive_step,
                        )
                    }
                )
            }
        ),
    )

    for mutated in mutated_plans:
        assert not services.orchestrator.review(mutated).approved


@pytest.mark.security
def test_validator_rejects_scope_exclusion_and_analysis_set_mutations(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    record, services, plan = compile_plan(runtime, root)

    invalid_exclusions = plan.task_plan.steps[0].model_copy(
        update={
            "arguments": {
                **plan.task_plan.steps[0].arguments,
                "excluded_paths": [str(outside)],
            }
        }
    )
    missing_analysis = plan.task_plan.model_copy(update={"steps": plan.task_plan.steps[:2]})
    wrong_scope = plan.task_plan.model_copy(
        update={"scope": plan.task_plan.scope.model_copy(update={"included_paths": (outside,)})}
    )
    unknown_id = plan.model_copy(update={"authorized_root_ids": (uuid4(),)})

    assert not services.orchestrator.review(
        plan.model_copy(
            update={
                "task_plan": plan.task_plan.model_copy(
                    update={"steps": (invalid_exclusions, *plan.task_plan.steps[1:])}
                )
            }
        )
    ).approved
    assert not services.orchestrator.review(
        plan.model_copy(update={"task_plan": missing_analysis})
    ).approved
    assert not services.orchestrator.review(
        plan.model_copy(update={"task_plan": wrong_scope})
    ).approved
    assert not services.orchestrator.review(unknown_id).approved
    assert record.path == root.resolve()


@pytest.mark.security
def test_compiler_rejects_overlapping_roots_and_missing_analyzer(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    nested = root / "nested"
    nested.mkdir(parents=True)
    parent_record = runtime.authorized_paths.add_authorized(root)
    child_record = runtime.authorized_paths.add_authorized(nested)
    services = runtime.create_file_analysis_services()
    overlapping = FileAnalysisIntentDraft(
        authorized_root_ids=(parent_record.path_id, child_record.path_id),
        analyses=(AnalysisType.LARGE_FILES,),
    )

    with pytest.raises(ValueError, match="overlapping"):
        services.compiler.compile("analyze", overlapping)

    empty_registry = ToolRegistry()
    compiler = FileAnalysisPlanCompiler(
        runtime.authorized_paths,
        empty_registry,
        max_files=1_000,
        timeout_seconds=60,
    )
    with pytest.raises(ValueError, match="not registered"):
        compiler.compile(
            "analyze",
            FileAnalysisIntentDraft(
                authorized_root_ids=(parent_record.path_id,),
                analyses=(AnalysisType.LARGE_FILES,),
            ),
        )


@pytest.mark.security
def test_planner_sends_only_opaque_roots_and_requires_exact_consent(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "secret-real-path"
    root.mkdir()
    record = runtime.authorized_paths.add_authorized(root, label="Downloads")
    services = runtime.create_file_analysis_services()
    provider = CapturingProvider(
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            analyses=(AnalysisType.LARGE_FILES,),
        )
    )
    from pc_manager_agent.orchestration.file_analysis_planner import FileAnalysisPlanner

    planner = FileAnalysisPlanner(
        provider,
        runtime.authorized_paths,
        services.registry,
        services.compiler,
        runtime.external_consent,
    )
    request = planner.build_provider_request("find large files", (record.path_id,))
    assert str(root.resolve()) not in request.model_dump_json()
    consent = planner.request_external_consent("find large files", (record.path_id,))
    runtime.external_consent.resolve(consent.confirmation_id, True)

    result = asyncio.run(
        planner.plan("find large files", consent.confirmation_id, (record.path_id,))
    )

    assert result.provider_request_id == "trace-1"
    assert provider.request == request
    assert result.plan.task_plan.scope.included_paths == (root.resolve(),)


@pytest.mark.security
def test_external_consent_rejects_unknown_reuse_expiry_purpose_and_provider() -> None:
    current = datetime(2026, 1, 1, tzinfo=UTC)

    def now() -> datetime:
        return current

    service = ExternalDataConsentService(ttl_seconds=2, now=now)
    payload = {"goal": "analyze"}
    with pytest.raises(ExternalDataConsentError, match="Unknown"):
        service.resolve(uuid4(), True)
    request = service.request(
        purpose=ExternalDataPurpose.PLANNING,
        provider="provider-a",
        payload=payload,
        object_summary="minimal payload",
    )
    service.resolve(request.confirmation_id, True)
    with pytest.raises(ExternalDataConsentError, match="already resolved"):
        service.resolve(request.confirmation_id, True)
    with pytest.raises(ExternalDataConsentError, match="purpose or provider"):
        service.require_approved(
            request.confirmation_id,
            purpose=ExternalDataPurpose.EXPLANATION,
            provider="provider-a",
            payload=payload,
        )
    with pytest.raises(ExternalDataConsentError, match="purpose or provider"):
        service.require_approved(
            request.confirmation_id,
            purpose=ExternalDataPurpose.PLANNING,
            provider="provider-b",
            payload=payload,
        )
    current += timedelta(seconds=3)
    with pytest.raises(ExternalDataConsentError, match="expired"):
        service.require_approved(
            request.confirmation_id,
            purpose=ExternalDataPurpose.PLANNING,
            provider="provider-a",
            payload=payload,
        )
