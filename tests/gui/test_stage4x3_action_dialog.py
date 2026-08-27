"""Stage 4X3 GUI tests use synthetic plans and never request real UAC."""

from __future__ import annotations

from pathlib import Path
from typing import cast
from uuid import uuid4

import pytest
from pytestqt.qtbot import QtBot
from tests.fixtures.privileged_actions import build_privileged_test_stack

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.domain.privileged_actions import ServiceStartupTypeChangePayload
from pc_manager_agent.domain.service_actions import ServiceStartupType
from pc_manager_agent.orchestration.elevated_stage4x3 import (
    ElevatedStage4X3PreparationService,
    PreparedStage4X3Action,
)
from pc_manager_agent.ui.stage4x3_action_dialog import Stage4X3ActionDialog
from pc_manager_agent.ui.stage4x3_workers import (
    PreparedStage4X3Ui,
    Stage4X3UiAction,
    Stage4X3UiRequest,
)

pytestmark = pytest.mark.gui


def test_dialog_defaults_to_cancel_and_displays_r3_uac_and_full_rollback(
    qtbot: QtBot,
    runtime: ApplicationRuntime,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stack = build_privileged_test_stack(tmp_path / "stage4x3-ui.db")
    try:
        current = stack.fake_state.inspect_service(stack.fake_service.identity.service_name)
        assert current is not None
        source_id = uuid4()
        payload = ServiceStartupTypeChangePayload(
            source_transaction_id=source_id,
            service_identity=current.identity,
            expected_current_configuration=current.startup_configuration,
            requested_startup_type=ServiceStartupType.AUTOMATIC,
            expected_runtime_state=current.state,
            impact_digest="1" * 64,
            safety_digest=current.safety_digest,
            backup_id=uuid4(),
            backup_digest="2" * 64,
        )
        privileged = stack.service.prepare(
            source_plan_id=source_id,
            source_plan_hash="3" * 64,
            payload=payload,
            target_identity_hash=current.identity.canonical_digest(),
            object_summary="one exact synthetic service",
            target_state_hash=current.state_digest(),
            safety_digest=current.safety_digest,
            privilege_resolution=current.privilege_resolution,
        )
        request = Stage4X3UiRequest(
            action=Stage4X3UiAction.SERVICE_STARTUP_CHANGE,
            user_goal="Change Example service startup type",
            display_name="Example Service",
            service_identity=current.identity,
        )
        value = PreparedStage4X3Ui(
            cast(ElevatedStage4X3PreparationService, object()),
            request,
            PreparedStage4X3Action(request.user_goal, source_id, privileged),
        )
        monkeypatch.setattr(Stage4X3ActionDialog, "_prepare", lambda _self: None)
        dialog = Stage4X3ActionDialog(runtime, request)
        qtbot.addWidget(dialog)
        dialog.show()
        assert dialog._cancel.isDefault()
        assert not dialog._primary.isEnabled()

        dialog._prepared_ready(value)
        html = dialog._details.toPlainText()
        assert dialog._stage == "PLAN_CONFIRMATION"
        assert dialog._primary.isEnabled()
        assert "R3" in html
        assert "FULL" in html
        assert "Windows Administrator" in html
        assert "UAC" in dialog._risk.text()
    finally:
        stack.close()
