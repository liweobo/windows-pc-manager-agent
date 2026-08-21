"""Truthful post-process verification for Stage 4D2B Vendor uninstall."""

from __future__ import annotations

from pathlib import Path

import pytest

from pc_manager_agent.domain.software_uninstall_analysis import (
    NormalizedInstalledSoftware,
    SoftwareInventory,
)
from pc_manager_agent.domain.vendor_uninstall import (
    VendorProcessExecutionResult,
    VendorProcessResultCategory,
    VendorVerificationState,
)
from pc_manager_agent.orchestration.software_inventory import (
    SoftwareInventorySnapshot,
    normalize_raw_entry,
)
from pc_manager_agent.orchestration.vendor_uninstall_verifier import VendorUninstallVerifier
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.vendor_uninstall import vendor_entry


class _Resolver:
    def __init__(
        self,
        software: NormalizedInstalledSoftware | None,
        snapshot: SoftwareInventorySnapshot,
        *,
        failure: Exception | None = None,
    ) -> None:
        self._software = software
        self._snapshot = snapshot
        self._failure = failure

    def inspect(
        self,
        identity_digest: str,
        max_items: int,
        cancellation: CancellationToken,
    ) -> tuple[NormalizedInstalledSoftware | None, SoftwareInventorySnapshot]:
        if self._failure is not None:
            raise self._failure
        return self._software, self._snapshot


def _target(tmp_path: Path, *, version: str = "1.0") -> NormalizedInstalledSoftware:
    target = normalize_raw_entry(vendor_entry(tmp_path / "example", version=version))
    assert target is not None
    return target


def _snapshot(
    entries: tuple[NormalizedInstalledSoftware, ...],
    *,
    warnings: tuple[str, ...] = (),
) -> SoftwareInventorySnapshot:
    return SoftwareInventorySnapshot(
        inventory=SoftwareInventory(entries=entries, warnings=warnings),
        raw_by_identity={},
    )


def _process(category: VendorProcessResultCategory) -> VendorProcessExecutionResult:
    return VendorProcessExecutionResult(
        category=category,
        exit_code=(0 if category is VendorProcessResultCategory.PROCESS_EXITED_ZERO else 5),
        launched=True,
    )


@pytest.mark.parametrize(
    ("category", "expected"),
    (
        (
            VendorProcessResultCategory.PROCESS_EXITED_ZERO,
            VendorVerificationState.VERIFIED_REMOVED,
        ),
        (
            VendorProcessResultCategory.PROCESS_EXITED_NONZERO,
            VendorVerificationState.REMOVED_WITH_UNEXPECTED_PROCESS_RESULT,
        ),
    ),
)
def test_absent_target_uses_inventory_and_preserves_process_fact(
    tmp_path: Path,
    category: VendorProcessResultCategory,
    expected: VendorVerificationState,
) -> None:
    target = _target(tmp_path)
    verifier = VendorUninstallVerifier(_Resolver(None, _snapshot(())))
    result = verifier.verify(target, _process(category), 5_000, CancellationToken())
    assert result.state is expected
    assert result.inventory_refreshed


@pytest.mark.parametrize(
    "category",
    (
        VendorProcessResultCategory.PROCESS_EXITED_ZERO,
        VendorProcessResultCategory.PROCESS_EXITED_NONZERO,
    ),
)
def test_present_exact_target_is_failed(
    tmp_path: Path,
    category: VendorProcessResultCategory,
) -> None:
    target = _target(tmp_path)
    verifier = VendorUninstallVerifier(_Resolver(target, _snapshot((target,))))
    result = verifier.verify(target, _process(category), 5_000, CancellationToken())
    assert result.state is VendorVerificationState.FAILED


def test_replacement_version_requires_review(tmp_path: Path) -> None:
    target = _target(tmp_path, version="1.0")
    replacement = _target(tmp_path, version="2.0")
    verifier = VendorUninstallVerifier(_Resolver(None, _snapshot((replacement,))))
    result = verifier.verify(
        target,
        _process(VendorProcessResultCategory.PROCESS_EXITED_ZERO),
        5_000,
        CancellationToken(),
    )
    assert result.state is VendorVerificationState.TARGET_INSTANCE_CHANGED
    assert result.replacement_candidates == 1


def test_partial_or_failed_inventory_cannot_prove_removal(tmp_path: Path) -> None:
    target = _target(tmp_path)
    partial = VendorUninstallVerifier(
        _Resolver(None, _snapshot((), warnings=("synthetic partial inventory",)))
    ).verify(
        target,
        _process(VendorProcessResultCategory.PROCESS_EXITED_ZERO),
        5_000,
        CancellationToken(),
    )
    assert partial.state is VendorVerificationState.COMPLETED_UNVERIFIED
    assert not partial.inventory_refreshed

    failed = VendorUninstallVerifier(
        _Resolver(None, _snapshot(()), failure=RuntimeError("inventory unavailable"))
    ).verify(
        target,
        _process(VendorProcessResultCategory.PROCESS_EXITED_ZERO),
        5_000,
        CancellationToken(),
    )
    assert failed.state is VendorVerificationState.COMPLETED_UNVERIFIED
    assert failed.warnings == ("Fresh software inventory failed: RuntimeError.",)


def test_replacement_comparison_requires_nonempty_exact_visible_fields(tmp_path: Path) -> None:
    target = _target(tmp_path)
    replacement = _target(tmp_path, version="2.0").model_copy(update={"publisher": None})
    result = VendorUninstallVerifier(_Resolver(None, _snapshot((replacement,)))).verify(
        target,
        _process(VendorProcessResultCategory.PROCESS_EXITED_ZERO),
        5_000,
        CancellationToken(),
    )
    assert result.state is VendorVerificationState.VERIFIED_REMOVED
    assert result.replacement_candidates == 0
