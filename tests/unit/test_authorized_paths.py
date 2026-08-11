from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.authorization.models import AuthorizedPath, AuthorizedPathKind
from pc_manager_agent.authorization.service import AuthorizedPathService
from pc_manager_agent.domain.errors import PathNotAuthorizedError
from pc_manager_agent.persistence.authorized_paths import (
    AuthorizedPathRepository,
    AuthorizedPathStoreError,
)
from pc_manager_agent.safety.path_policy import PathPolicy


def build_service(tmp_path: Path) -> tuple[AuthorizedPathService, AuthorizedPathRepository]:
    repository = AuthorizedPathRepository(tmp_path / "state.db")
    repository.initialize()
    return AuthorizedPathService(repository, network_path_detector=lambda _path: False), repository


def test_authorized_path_lifecycle_does_not_expand_to_parent(tmp_path: Path) -> None:
    root = tmp_path / "Downloads"
    child = root / "nested"
    outside = tmp_path / "Documents"
    child.mkdir(parents=True)
    outside.mkdir()
    service, repository = build_service(tmp_path)

    record = service.add_authorized(root, label="Downloads", favorite=True)

    assert record.path == root.resolve()
    assert record.favorite
    assert service.require_authorized(child) == child.resolve()
    with pytest.raises(PathNotAuthorizedError):
        service.require_authorized(tmp_path)
    with pytest.raises(PathNotAuthorizedError):
        service.require_authorized(outside)
    assert service.remove(record.path_id)
    assert not service.list_authorized()
    restored = service.restore(record)
    assert restored.path_id == record.path_id
    repository.close()


def test_custom_forbidden_root_is_enforced_inside_authorized_root(tmp_path: Path) -> None:
    root = tmp_path / "root"
    blocked = root / "private"
    allowed = root / "public"
    blocked.mkdir(parents=True)
    allowed.mkdir()
    service, repository = build_service(tmp_path)
    authorized = service.add_authorized(root)
    forbidden = service.add_forbidden(blocked)

    policy = service.build_policy((authorized.path_id,))

    assert policy.entry_rejection_reason(blocked) == "forbidden-path"
    assert policy.validate_scan_root(allowed) == allowed.resolve()
    assert service.list_forbidden()[0].kind is AuthorizedPathKind.FORBIDDEN
    assert service.remove(forbidden.path_id)
    repository.close()


def test_authorized_path_rejects_duplicates_and_network_roots(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    service, repository = build_service(tmp_path)
    service.add_authorized(root)
    with pytest.raises(AuthorizedPathStoreError, match="already"):
        service.add_authorized(root)
    repository.close()

    network_repository = AuthorizedPathRepository(tmp_path / "network.db")
    network_repository.initialize()
    network_service = AuthorizedPathService(
        network_repository,
        network_path_detector=lambda _path: True,
    )
    with pytest.raises(PermissionError, match="Network"):
        network_service.add_authorized(root)
    network_repository.close()


def test_authorization_requires_known_ids_and_regular_files(tmp_path: Path) -> None:
    root = tmp_path / "root"
    root.mkdir()
    file = root / "file.txt"
    file.write_text("safe", encoding="utf-8")
    service, repository = build_service(tmp_path)

    with pytest.raises(PathNotAuthorizedError, match="No authorized"):
        service.require_authorized(root)
    with pytest.raises(PathNotAuthorizedError, match="No authorized"):
        service.require_authorized_file(file)
    with pytest.raises(PathNotAuthorizedError, match="No authorized root"):
        service.build_policy(())

    record = service.add_authorized(root)
    assert service.require_authorized_file(file) == file.resolve()
    with pytest.raises(PathNotAuthorizedError, match="Unknown"):
        service.resolve_authorized((uuid4(),))
    with pytest.raises(PathNotAuthorizedError, match="Unknown"):
        service.build_policy((uuid4(),))
    assert not service.remove(uuid4())
    assert service.resolve_authorized((record.path_id,)) == (record,)
    repository.close()


def test_authorization_change_callback_and_restore_identity_check(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "root"
    other = tmp_path / "other"
    root.mkdir()
    other.mkdir()
    repository = AuthorizedPathRepository(tmp_path / "state.db")
    repository.initialize()
    changes: list[tuple[str, AuthorizedPath]] = []
    service = AuthorizedPathService(
        repository,
        network_path_detector=lambda _path: False,
        on_change=lambda action, record: changes.append((action, record)),
    )

    record = service.add_authorized(root)
    assert service.remove(record.path_id)
    service.restore(record)
    assert service.remove(record.path_id)
    changed = record.model_copy(update={"path": other.resolve()})

    def changed_identity(*_args: object, **_kwargs: object) -> Path:
        return root.resolve()

    monkeypatch.setattr(PathPolicy, "canonicalize_authorization_root", changed_identity)
    with pytest.raises(ValueError, match="identity changed"):
        service.restore(changed)

    assert [action for action, _record in changes] == [
        "added",
        "removed",
        "restored",
        "removed",
    ]
    repository.close()


def test_uninitialized_authorization_repository_fails_closed(tmp_path: Path) -> None:
    repository = AuthorizedPathRepository(tmp_path / "state.db")
    with pytest.raises(AuthorizedPathStoreError, match="not initialized"):
        repository.list()
