from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.confirmation.external_data import (
    ExternalDataConsentError,
    ExternalDataConsentService,
    ExternalDataPurpose,
)
from pc_manager_agent.domain.file_analysis import (
    AnalysisMatchMode,
    AnalysisType,
    FileAnalysisFilters,
    FileAnalysisIntentDraft,
)


@pytest.mark.security
def test_planner_compiler_uses_root_ids_and_rejects_wrapper_mutation(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    root.mkdir()
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_analysis_services()
    plan = services.compiler.compile(
        "分析文件",
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            filters=FileAnalysisFilters(minimum_size_bytes=500, inactive_days=90),
            analyses=(AnalysisType.LARGE_FILES,),
        ),
    )

    assert services.orchestrator.review(plan).approved
    changed_filter = plan.model_copy(
        update={
            "filters": FileAnalysisFilters(
                minimum_size_bytes=501,
                inactive_days=90,
            )
        }
    )
    changed_mode = plan.model_copy(update={"match_mode": AnalysisMatchMode.ANY})

    assert not services.orchestrator.review(changed_filter).approved
    assert not services.orchestrator.review(changed_mode).approved


@pytest.mark.security
def test_unknown_tool_and_scope_expansion_are_denied(
    runtime: ApplicationRuntime,
    tmp_path: Path,
) -> None:
    root = tmp_path / "Downloads"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    record = runtime.authorized_paths.add_authorized(root)
    services = runtime.create_file_analysis_services()
    plan = services.compiler.compile(
        "分析文件",
        FileAnalysisIntentDraft(
            authorized_root_ids=(record.path_id,),
            analyses=(AnalysisType.LARGE_FILES,),
        ),
    )
    unknown_step = plan.task_plan.steps[0].model_copy(update={"tool_name": "file.smart_cleanup"})
    unknown_task = plan.task_plan.model_copy(
        update={"steps": (unknown_step, *plan.task_plan.steps[1:])}
    )
    expanded_step = plan.task_plan.steps[0].model_copy(
        update={
            "arguments": {
                **plan.task_plan.steps[0].arguments,
                "root": str(outside),
            }
        }
    )
    expanded_task = plan.task_plan.model_copy(
        update={"steps": (expanded_step, *plan.task_plan.steps[1:])}
    )

    assert not services.orchestrator.review(
        plan.model_copy(update={"task_plan": unknown_task})
    ).approved
    assert not services.orchestrator.review(
        plan.model_copy(update={"task_plan": expanded_task})
    ).approved


@pytest.mark.security
def test_external_data_consent_is_bound_to_exact_payload() -> None:
    service = ExternalDataConsentService(ttl_seconds=300)
    payload = {"goal": "analyze", "root_ids": ["opaque-id"]}
    request = service.request(
        purpose=ExternalDataPurpose.PLANNING,
        provider="fake",
        payload=payload,
        object_summary="send minimal planning data",
    )
    service.resolve(request.confirmation_id, True)
    service.require_approved(
        request.confirmation_id,
        purpose=ExternalDataPurpose.PLANNING,
        provider="fake",
        payload=payload,
    )
    with pytest.raises(ExternalDataConsentError, match="changed"):
        service.require_approved(
            request.confirmation_id,
            purpose=ExternalDataPurpose.PLANNING,
            provider="fake",
            payload={"goal": "changed", "root_ids": ["opaque-id"]},
        )
