from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from pc_manager_agent.safety.path_policy import (
    PathPolicy,
    PathSecurityError,
    _is_within,
    is_reparse_point,
)


@pytest.mark.security
def test_path_policy_accepts_root_and_rejects_traversal_and_escape(tmp_path: Path) -> None:
    root = tmp_path / "approved"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    policy = PathPolicy.for_scan_root(root)
    assert policy.validate_scan_root(root) == root.resolve()
    with pytest.raises(PathSecurityError, match="traversal"):
        policy.validate_scan_root(root / ".." / root.name)
    with pytest.raises(PathSecurityError, match="outside"):
        policy.validate_scan_root(outside)
    file_path = root / "file.txt"
    file_path.write_text("x", encoding="utf-8")
    with pytest.raises(PathSecurityError, match="not a directory"):
        policy.validate_scan_root(file_path)


@pytest.mark.security
def test_forbidden_root_and_other_user_profile_are_denied(tmp_path: Path) -> None:
    users = tmp_path / "Users"
    current = users / "current"
    other = users / "other"
    approved = tmp_path
    current.mkdir(parents=True)
    other.mkdir()
    secret = current / ".ssh"
    secret.mkdir()
    explicit_protected = current / "protected-data"
    explicit_protected.mkdir()
    policy = PathPolicy(
        (approved,),
        (explicit_protected,),
        current_user_root=current,
    )
    assert policy.is_forbidden(secret)
    assert policy.is_forbidden(explicit_protected)
    assert policy.is_forbidden(other)
    assert not policy.is_approved(other)
    assert policy.entry_rejection_reason(secret) == "forbidden-path"
    assert policy.approved_roots == (approved.resolve(),)
    assert policy.forbidden_roots == (explicit_protected.resolve(),)


@pytest.mark.security
def test_reparse_flag_is_detected(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    fake = SimpleNamespace(st_file_attributes=0x400)
    monkeypatch.setattr("pc_manager_agent.safety.path_policy.os.lstat", lambda _path: fake)
    monkeypatch.setattr(Path, "is_symlink", lambda _self: False)
    assert is_reparse_point(tmp_path)


@pytest.mark.security
def test_reparse_probe_failure_and_cross_drive_are_safe(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "pc_manager_agent.safety.path_policy.os.lstat",
        lambda _path: (_ for _ in ()).throw(OSError("unavailable")),
    )
    assert not is_reparse_point(tmp_path)
    assert not _is_within(Path("C:/one"), Path("D:/two"))


@pytest.mark.security
def test_entry_rejections_cover_outside_reparse_and_allowed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    allowed = root / "allowed"
    allowed.mkdir()
    redirected = root / "redirected"
    redirected.mkdir()
    policy = PathPolicy.for_scan_root(root)
    assert policy.entry_rejection_reason(outside) == "outside-approved-root"
    assert policy.entry_rejection_reason(allowed) is None
    original = is_reparse_point
    monkeypatch.setattr(
        "pc_manager_agent.safety.path_policy.is_reparse_point",
        lambda path: path == redirected or original(path),
    )
    assert policy.entry_rejection_reason(redirected) == "reparse-point"


@pytest.mark.security
def test_scan_root_reparse_branch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    policy = PathPolicy.for_scan_root(tmp_path)
    monkeypatch.setattr(
        "pc_manager_agent.safety.path_policy.is_reparse_point",
        lambda _path: True,
    )
    with pytest.raises(PathSecurityError, match="reparse"):
        policy.validate_scan_root(tmp_path)


@pytest.mark.security
def test_real_symlink_is_rejected_when_supported(tmp_path: Path) -> None:
    target = tmp_path / "target"
    link = tmp_path / "link"
    target.mkdir()
    try:
        os.symlink(target, link, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"Windows symlink creation is unavailable: {exc}")
    policy = PathPolicy((link,))
    with pytest.raises(PathSecurityError, match="reparse"):
        policy.validate_scan_root(link)


@pytest.mark.security
def test_missing_root_and_empty_policy_fail(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="approved"):
        PathPolicy(())
    missing = tmp_path / "missing"
    policy = PathPolicy((missing,))
    with pytest.raises(PathSecurityError, match="unavailable"):
        policy.validate_scan_root(missing)
