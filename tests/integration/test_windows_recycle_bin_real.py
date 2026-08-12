from __future__ import annotations

import os
from pathlib import Path

import pytest

from pc_manager_agent.domain.trash import RecycleVerificationStatus
from pc_manager_agent.platform_support.windows.recycle_bin import WindowsRecycleBinPlatform


@pytest.mark.windows
def test_real_shell_moves_disposable_fixture_to_recycle_bin(tmp_path: Path) -> None:
    """Exercise real IFileOperation only in an explicitly enabled disposable environment."""
    if os.getenv("PC_MANAGER_RUN_REAL_RECYCLE_TEST") != "1":
        pytest.skip("Real Recycle Bin integration test is opt-in")
    source = tmp_path / "pc-manager-agent-ci-recycle-probe.txt"
    source.write_text("disposable CI fixture", encoding="utf-8")
    platform = WindowsRecycleBinPlatform()
    capability = platform.capability(source)
    if not capability.available:
        pytest.skip(capability.reason or "Recycle Bin unavailable on runner volume")

    result = platform.recycle(source)

    assert result.recycled, result.model_dump(mode="json")
    assert result.verification_status is RecycleVerificationStatus.VERIFIED_RECYCLED
    assert result.recycle_item_identifier
    assert not source.exists()
