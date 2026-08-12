from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.trash import RecycleVerificationStatus
from pc_manager_agent.platform_support.windows.recycle_bin import (
    _hresult_succeeded,
    _RecycleProgressSink,
)


class _ShellItem:
    def GetDisplayName(self, _mode: int) -> str:
        return "::{RecycleBinItem}"


@pytest.mark.windows
def test_progress_sink_requires_recycle_transfer_and_captures_new_item() -> None:
    sink = _RecycleProgressSink()
    assert sink.PreDeleteItem(0, object()) != 0
    assert not sink.saw_recycle_transfer_flag

    from win32com.shell import shellcon

    assert sink.PreDeleteItem(shellcon.TSF_DELETE_RECYCLE_IF_POSSIBLE, object()) == 0
    sink.PostDeleteItem(0, object(), 0, _ShellItem())
    assert sink.delete_hresult == 0
    assert sink.recycle_item_identifier == "::{RecycleBinItem}"


@pytest.mark.windows
def test_progress_sink_null_new_item_never_claims_recycled() -> None:
    sink = _RecycleProgressSink()
    sink.PostDeleteItem(0, object(), 0, None)
    assert sink.delete_hresult == 0
    assert sink.recycle_item_identifier is None
    assert RecycleVerificationStatus.UNKNOWN.value == "UNKNOWN"


def test_platform_source_has_no_legacy_delete_fallback() -> None:
    source = (
        Path(__file__).parents[2]
        / "src"
        / "pc_manager_agent"
        / "platform_support"
        / "windows"
        / "recycle_bin.py"
    ).read_text(encoding="utf-8")
    assert "SHFileOperation" not in source
    assert "SHEmptyRecycleBin" not in source
    assert "shell=True" not in source


def test_hresult_helper_accepts_informational_success_and_rejects_failure() -> None:
    assert _hresult_succeeded(0)
    assert _hresult_succeeded(0x00270008)  # COPYENGINE_S_DONT_PROCESS_CHILDREN
    assert not _hresult_succeeded(0x80270037)  # COPYENGINE_E_RECYCLE_SIZE_TOO_BIG
