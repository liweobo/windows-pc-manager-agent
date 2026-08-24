"""Safe Windows Explorer selection for Stage 4D3 report candidates."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from pc_manager_agent.domain.software_residuals import (
    OwnershipConfidence,
    ResidualCandidate,
    ResidualClassification,
    ResidualIdentity,
    ResidualObjectType,
    ResidualRecommendation,
    ResidualSource,
    UserDataProtectionLevel,
)
from pc_manager_agent.platform_support.windows.residual_explorer import (
    ResidualExplorerError,
    WindowsResidualExplorerService,
)


def _candidate(path: Path, root: Path) -> ResidualCandidate:
    metadata = path.stat()
    identity = ResidualIdentity(
        normalized_path=path,
        device_id=metadata.st_dev,
        file_id=metadata.st_ino,
        object_type=ResidualObjectType.FILE,
        size_bytes=metadata.st_size,
        modified_time_ns=metadata.st_mtime_ns,
    )
    return ResidualCandidate(
        report_id=uuid4(),
        identity=identity,
        path=path,
        scan_root=root,
        source=ResidualSource.INSTALL_LOCATION,
        object_type=ResidualObjectType.FILE,
        size_bytes=metadata.st_size,
        classification=ResidualClassification.PROGRAM_RESIDUAL,
        ownership_confidence=OwnershipConfidence.HIGH,
        protection_level=UserDataProtectionLevel.CAUTION,
        readable=True,
        reparse_or_symlink=False,
        recommendation=ResidualRecommendation.REPORT,
    )


def test_explorer_uses_fixed_argument_array_and_shell_false(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "candidate.bin"
    path.write_bytes(b"metadata")
    import pc_manager_agent.platform_support.windows.residual_explorer as explorer_module

    run = MagicMock(return_value=SimpleNamespace(returncode=0))
    monkeypatch.setattr(explorer_module.subprocess, "run", run)
    monkeypatch.setattr(
        WindowsResidualExplorerService,
        "_explorer_path",
        staticmethod(lambda: Path("C:/Windows/explorer.exe")),
    )
    WindowsResidualExplorerService().select_candidate(_candidate(path, root))
    arguments, options = run.call_args
    assert arguments[0] == ["C:\\Windows\\explorer.exe", f"/select,{path}"]
    assert options["shell"] is False


def test_explorer_rejects_scope_reparse_stale_and_missing_candidates(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    path = root / "candidate.bin"
    path.write_bytes(b"metadata")
    candidate = _candidate(path, root)
    service = WindowsResidualExplorerService()
    outside = candidate.model_copy(update={"path": tmp_path / "outside.bin"})
    with pytest.raises(ResidualExplorerError, match="outside"):
        service.select_candidate(outside)
    reparse = candidate.model_copy(update={"reparse_or_symlink": True})
    with pytest.raises(ResidualExplorerError, match="reparse"):
        service.select_candidate(reparse)
    stale_identity = candidate.identity.model_copy(
        update={"file_id": candidate.identity.file_id + 1}
    )
    stale = candidate.model_copy(update={"identity": stale_identity})
    with pytest.raises(ResidualExplorerError, match="identity changed"):
        service.select_candidate(stale)
    missing = candidate.model_copy(update={"path": root / "missing.bin"})
    with pytest.raises(ResidualExplorerError, match="no longer available"):
        service.select_candidate(missing)
