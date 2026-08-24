"""Every final identity/snapshot/cancellation gate in the shared recycle primitive."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.trash import TrashObjectSnapshot
from pc_manager_agent.tools.file_tools.recycle_executor import VerifiedRecycleBinExecutor
from pc_manager_agent.tools.manifest import CancellationToken
from tests.stage2b_support import FakeRecyclePlatform, FakeTrashIdentityPlatform


def _snapshot(path: Path, identity: FakeTrashIdentityPlatform) -> TrashObjectSnapshot:
    state = identity.inspect(path)
    return TrashObjectSnapshot(
        source=path,
        root_state=state,
        tree_digest="a" * 64,
        object_count=1,
        total_size_bytes=state.size_bytes,
        largest_item_bytes=state.size_bytes,
        hidden_count=0,
        system_count=0,
        reparse_count=0,
        offline_count=0,
    )


def test_verified_executor_success_and_each_fail_closed_gate(tmp_path: Path) -> None:
    source = tmp_path / "item.bin"
    source.write_bytes(b"program")
    identity = FakeTrashIdentityPlatform()
    recycle = FakeRecyclePlatform()
    executor = VerifiedRecycleBinExecutor(identity, recycle)
    expected = identity.inspect(source)
    snapshot = _snapshot(source, identity)

    cancelled = CancellationToken()
    cancelled.cancel()
    with pytest.raises(RuntimeError, match="cancelled"):
        executor.recycle(source, expected, snapshot, lambda *_args: snapshot, cancelled)

    with pytest.raises(PermissionError, match="identity"):
        executor.recycle(
            source,
            expected.model_copy(update={"modified_ns": expected.modified_ns + 1}),
            snapshot,
            lambda *_args: snapshot,
            CancellationToken(),
        )

    changed_snapshot = snapshot.model_copy(update={"tree_digest": "b" * 64})
    with pytest.raises(PermissionError, match="tree"):
        executor.recycle(
            source,
            expected,
            snapshot,
            lambda *_args: changed_snapshot,
            CancellationToken(),
        )

    cancel_after_snapshot = CancellationToken()

    def snapshot_then_cancel(*_args: object) -> TrashObjectSnapshot:
        cancel_after_snapshot.cancel()
        return snapshot

    with pytest.raises(RuntimeError, match="cancelled"):
        executor.recycle(
            source,
            expected,
            snapshot,
            snapshot_then_cancel,
            cancel_after_snapshot,
        )

    result = executor.recycle(
        source,
        expected,
        snapshot,
        lambda *_args: snapshot,
        CancellationToken(),
    )
    assert result.recycled
    assert recycle.calls == [source]
