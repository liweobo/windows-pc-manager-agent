"""Durable Stage 4D3 context capture across every controlled uninstall mechanism."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from uuid import uuid4

import pytest

from pc_manager_agent.domain.software_residuals import UninstallMechanism
from pc_manager_agent.orchestration.uninstall_context import UninstallContextRecorder
from pc_manager_agent.persistence.software_residuals import SoftwareResidualRepository


class _Identity:
    def canonical_digest(self) -> str:
        return "b" * 64


def _classic_target(path: Path) -> SimpleNamespace:
    return SimpleNamespace(
        display_name="Example Product",
        display_version="1.2.3",
        publisher="Example Publisher",
        scope=SimpleNamespace(value="current_user"),
        architecture=SimpleNamespace(value="x64"),
        install_location=path,
        identity=_Identity(),
    )


@pytest.mark.parametrize(
    "mechanism",
    (
        UninstallMechanism.MSI,
        UninstallMechanism.VENDOR,
        UninstallMechanism.WINGET,
        UninstallMechanism.MSIX,
    ),
)
def test_recorder_captures_and_finalizes_each_agent_mechanism(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mechanism: UninstallMechanism,
) -> None:
    repository = SoftwareResidualRepository(tmp_path / f"{mechanism.value}.db")
    repository.initialize()
    recorder = UninstallContextRecorder(repository)
    transaction_id = uuid4()
    install_location = tmp_path / "Example Product"
    target = _classic_target(install_location)
    try:
        if mechanism is UninstallMechanism.MSI:
            preview = SimpleNamespace(
                transaction_id=transaction_id,
                target=target,
                identity_digest="a" * 64,
                validated_product=SimpleNamespace(
                    product_code="{00000000-0000-0000-0000-000000000000}"
                ),
            )
            context_id = recorder.capture_msi(cast(Any, preview))
        elif mechanism is UninstallMechanism.VENDOR:
            preview = SimpleNamespace(
                transaction_id=transaction_id,
                target=target,
                identity_digest="a" * 64,
                raw_uninstall_string="must never be stored",
            )
            context_id = recorder.capture_vendor(cast(Any, preview))
        elif mechanism is UninstallMechanism.WINGET:
            package_identity = SimpleNamespace(
                package_id="Example.Product",
                source_name="winget",
            )
            preview = SimpleNamespace(
                transaction_id=transaction_id,
                software=target,
                package=SimpleNamespace(identity=package_identity),
            )
            context_id = recorder.capture_winget(cast(Any, preview))
        else:
            monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "LocalAppData"))
            identity = SimpleNamespace(
                family=SimpleNamespace(family_name="Example.Product_test"),
                instance=SimpleNamespace(
                    version="1.2.3",
                    full_name="Example.Product_1.2.3_x64__test",
                    architecture="x64",
                ),
                scope=SimpleNamespace(value="current_user"),
                canonical_digest=lambda: "c" * 64,
            )
            package = SimpleNamespace(
                identity=identity,
                installed_path=str(install_location),
                display_name="Example Product",
                publisher_display_name="Example Publisher",
            )
            preview = SimpleNamespace(transaction_id=transaction_id, package=package)
            context_id = recorder.capture_msix(cast(Any, preview))
        assert context_id is not None
        assert recorder.finalize(
            context_id,
            verification_state="verified_removed",
            verified_removed=True,
        )
        stored = repository.get_context_for_transaction(transaction_id)
        assert stored.mechanism is mechanism
        assert stored.eligible_for_analysis is True
        assert stored.known_paths
        serialized = stored.model_dump_json()
        assert "must never be stored" not in serialized
        assert "UninstallString" not in serialized
    finally:
        repository.close()


def test_completed_unverified_context_is_preserved_with_lower_confidence_warning(
    tmp_path: Path,
) -> None:
    repository = SoftwareResidualRepository(tmp_path / "state.db")
    repository.initialize()
    recorder = UninstallContextRecorder(repository)
    preview = SimpleNamespace(
        transaction_id=uuid4(),
        target=_classic_target(tmp_path / "Example"),
        identity_digest="a" * 64,
        validated_product=SimpleNamespace(product_code="{00000000-0000-0000-0000-000000000000}"),
    )
    try:
        context_id = recorder.capture_msi(cast(Any, preview))
        assert recorder.finalize(
            context_id,
            verification_state="still_present",
            verified_removed=False,
            completed_unverified=True,
        )
        stored = repository.get_context_for_transaction(preview.transaction_id)
        assert stored.verification_state == "completed_unverified"
        assert stored.eligible_for_analysis is True
        assert stored.verified_removed is False
    finally:
        repository.close()
