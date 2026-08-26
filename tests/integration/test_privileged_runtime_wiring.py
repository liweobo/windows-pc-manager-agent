from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from pc_manager_agent.app.runtime import ApplicationRuntime
from pc_manager_agent.config.settings import AppSettings
from pc_manager_agent.domain.privileged_actions import PrivilegedActionType
from pc_manager_agent.privileged.revalidation import FakePrivilegedSystemState


def test_runtime_default_and_unknown_modes_are_not_executable(runtime: ApplicationRuntime) -> None:
    with pytest.raises(RuntimeError, match="disabled"):
        runtime.create_privileged_action_services(FakePrivilegedSystemState())
    with pytest.raises(ValidationError):
        AppSettings(privileged_broker_mode="real")


def test_mock_runtime_builds_only_the_internal_fake_boundary(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # GitHub Windows runners use an elevated token. The positive composition case
    # supplies explicit standard-user evidence; the separate test below verifies
    # that an elevated main process is rejected.
    monkeypatch.setattr("pc_manager_agent.app.runtime.current_process_is_elevated", lambda: False)
    runtime = ApplicationRuntime(
        AppSettings(
            data_directory=tmp_path / "app-data",
            privileged_broker_mode="mock",
        )
    )
    state = FakePrivilegedSystemState()
    try:
        services = runtime.create_privileged_action_services(state)
        assert services.fake_state is state
        assert services.broker._registry.action_types == (
            PrivilegedActionType.SERVICE_START,
            PrivilegedActionType.SERVICE_STOP,
        )
    finally:
        runtime.close()


def test_mock_runtime_rejects_an_elevated_main_process(
    runtime: ApplicationRuntime,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime.settings = runtime.settings.model_copy(update={"privileged_broker_mode": "mock"})
    monkeypatch.setattr("pc_manager_agent.app.runtime.current_process_is_elevated", lambda: True)
    with pytest.raises(RuntimeError, match="standard-user"):
        runtime.create_privileged_action_services(FakePrivilegedSystemState())
