from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.system_optimization import ScanScopeDecision
from pc_manager_agent.safety.system_cleanup_scope import (
    SystemCleanupScanScopePolicy,
    SystemCleanupScopeError,
)


def test_scope_requires_exact_known_or_authorized_root(tmp_path: Path) -> None:
    known = tmp_path / "known"
    authorized = tmp_path / "authorized"
    unknown = tmp_path / "unknown"
    for path in (known, authorized, unknown):
        path.mkdir()
    child = known / "child"
    child.touch()
    policy = SystemCleanupScanScopePolicy(known_roots=(known,), authorized_user_roots=(authorized,))
    assert policy.classify_root(known) is ScanScopeDecision.METADATA_ONLY
    assert policy.classify_root(authorized) is ScanScopeDecision.AUTHORIZED_USER_PATH
    assert policy.classify_root(unknown) is ScanScopeDecision.UNSUPPORTED
    with pytest.raises(SystemCleanupScopeError, match="not readable"):
        policy.validate_root(unknown)
    assert policy.validate_root(known) == known.absolute()
    assert policy.validate_entry(child, known) == child.absolute()
    with pytest.raises(SystemCleanupScopeError, match="escaped"):
        policy.validate_entry(unknown, known)


def test_sensitive_and_traversal_paths_fail_closed(tmp_path: Path) -> None:
    sensitive = tmp_path / ".ssh"
    sensitive.mkdir()
    policy = SystemCleanupScanScopePolicy(known_roots=(sensitive,), authorized_user_roots=())
    with pytest.raises(SystemCleanupScopeError, match="Sensitive"):
        policy.validate_root(sensitive)
    with pytest.raises(SystemCleanupScopeError, match="traversal"):
        policy.classify_root(tmp_path / "child/../escape")
    with pytest.raises(SystemCleanupScopeError, match="absolute"):
        policy.classify_root(Path("relative"))
    windows_config = tmp_path / "config" / "SAM"
    windows_config.parent.mkdir()
    windows_config.touch()
    database_policy = SystemCleanupScanScopePolicy(
        known_roots=(windows_config,), authorized_user_roots=()
    )
    with pytest.raises(SystemCleanupScopeError, match="security database"):
        database_policy.classify_root(windows_config)


def test_missing_or_non_directory_root_fails_closed(tmp_path: Path) -> None:
    missing = tmp_path / "missing"
    policy = SystemCleanupScanScopePolicy(known_roots=(missing,), authorized_user_roots=())
    with pytest.raises(SystemCleanupScopeError, match="metadata"):
        policy.validate_root(missing)
    regular_file = tmp_path / "file"
    regular_file.touch()
    file_policy = SystemCleanupScanScopePolicy(
        known_roots=(regular_file,), authorized_user_roots=()
    )
    with pytest.raises(SystemCleanupScopeError, match="not an available directory"):
        file_policy.validate_root(regular_file)


def test_protected_subtree_is_classified_before_known_root(tmp_path: Path) -> None:
    protected = tmp_path / "protected"
    protected.mkdir()
    policy = SystemCleanupScanScopePolicy(
        known_roots=(protected,),
        authorized_user_roots=(),
        protected_roots=(protected,),
    )
    assert policy.classify_root(protected) is ScanScopeDecision.PROTECTED


def test_symlink_root_is_never_followed(tmp_path: Path) -> None:
    target = tmp_path / "target"
    link = tmp_path / "link"
    target.mkdir()
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        pytest.skip("Creating a test symlink requires local Windows developer permission")
    policy = SystemCleanupScanScopePolicy(known_roots=(link,), authorized_user_roots=())
    with pytest.raises(SystemCleanupScopeError, match="reparse"):
        policy.validate_root(link)


def test_reparse_branch_is_fail_closed_without_os_symlink_permission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "root"
    root.mkdir()
    policy = SystemCleanupScanScopePolicy(known_roots=(root,), authorized_user_roots=())
    monkeypatch.setattr(Path, "is_symlink", lambda _path: True)
    with pytest.raises(SystemCleanupScopeError, match="reparse"):
        policy.validate_root(root)
