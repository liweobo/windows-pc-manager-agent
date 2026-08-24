"""Budget, cancellation, stale report, and race branches for Fresh Revalidation."""

from __future__ import annotations

import stat
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest

from pc_manager_agent.domain.residual_cleanup import ResidualCleanupRequest
from pc_manager_agent.domain.software_residuals import ResidualAnalysisStatus
from pc_manager_agent.safety.residual_cleanup_revalidation import (
    FreshResidualRevalidator,
    ResidualCleanupRevalidationError,
)
from pc_manager_agent.tools.manifest import CancellationToken
from tests.fixtures.residual_cleanup import (
    build_residual_cleanup_environment,
    create_residual_report,
)
from tests.fixtures.software_residuals import residual_context


def _report_case(
    tmp_path: Path,
    *,
    file_count: int = 1,
    max_selected: int = 20,
    max_objects: int = 10_000,
    max_bytes: int = 50 * 1024 * 1024 * 1024,
):
    root = tmp_path / "SyntheticProduct"
    root.mkdir(parents=True)
    paths = []
    for index in range(file_count):
        path = root / f"item-{index}.bin"
        path.write_bytes(b"payload")
        paths.append(path)
    environment = build_residual_cleanup_environment(
        tmp_path / "state.db",
        max_selected=max_selected,
        max_objects=max_objects,
        max_bytes=max_bytes,
    )
    context = residual_context(root)
    report = create_residual_report(environment, context)
    return environment, context, report, root, tuple(paths)


def test_revalidator_rejects_invalid_limits(tmp_path: Path) -> None:
    environment, _context, _report, _root, _paths = _report_case(tmp_path)
    current = environment.revalidator
    try:
        with pytest.raises(ValueError, match="positive"):
            FreshResidualRevalidator(
                current._repository,
                current._path_policy,
                current._classifier,
                current._ownership,
                current._protection,
                current._eligibility,
                current._recent,
                current._identity,
                current._recycle,
                max_selected=0,
                max_contained_objects=1,
                max_total_bytes=1,
            )
    finally:
        environment.close()


def test_revalidator_rejects_selection_limit_unknown_id_and_cancel(
    tmp_path: Path,
) -> None:
    environment, _context, report, _root, paths = _report_case(
        tmp_path,
        file_count=2,
        max_selected=1,
    )
    by_path = {item.path: item for item in report.candidates}
    try:
        with pytest.raises(ResidualCleanupRevalidationError, match="at most"):
            environment.revalidator.assess(
                ResidualCleanupRequest(
                    source_report_id=report.report_id,
                    selected_residual_ids=tuple(by_path[path].candidate_id for path in paths),
                )
            )
        with pytest.raises(ResidualCleanupRevalidationError, match="unknown"):
            environment.revalidator.assess(
                ResidualCleanupRequest(
                    source_report_id=report.report_id,
                    selected_residual_ids=(uuid4(),),
                )
            )
        token = CancellationToken()
        token.cancel()
        with pytest.raises(ResidualCleanupRevalidationError, match="cancelled"):
            environment.revalidator.assess(
                ResidualCleanupRequest(
                    source_report_id=report.report_id,
                    selected_residual_ids=(by_path[paths[0]].candidate_id,),
                ),
                token,
            )
    finally:
        environment.close()


def test_revalidator_rejects_noncomplete_report(tmp_path: Path) -> None:
    environment, _context, report, root, _paths = _report_case(tmp_path)
    try:
        new_report_id = uuid4()
        partial = report.model_copy(
            update={
                "report_id": new_report_id,
                "status": ResidualAnalysisStatus.PARTIAL,
                "candidates": tuple(
                    candidate.model_copy(
                        update={"report_id": new_report_id, "candidate_id": uuid4()}
                    )
                    for candidate in report.candidates
                ),
            }
        )
        environment.residuals.repository.save_report(partial)
        candidate = next(item for item in partial.candidates if item.path == root)
        with pytest.raises(ResidualCleanupRevalidationError, match="complete"):
            environment.revalidator.assess(
                ResidualCleanupRequest(
                    source_report_id=partial.report_id,
                    selected_residual_ids=(candidate.candidate_id,),
                )
            )
    finally:
        environment.close()


def test_revalidator_enforces_internal_and_aggregate_budgets(tmp_path: Path) -> None:
    internal, _context, report, root, _paths = _report_case(
        tmp_path / "internal",
        file_count=1,
        max_objects=1,
    )
    try:
        root_candidate = next(item for item in report.candidates if item.path == root)
        assessment = internal.revalidator.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(root_candidate.candidate_id,),
            )
        )
        assert not assessment.all_eligible
        assert "residualcleanuppolicyerror" in (assessment.items[0].eligibility_reason_codes[0])
    finally:
        internal.close()

    aggregate_root = tmp_path / "aggregate"
    aggregate_root.mkdir()
    aggregate, _context, report, _root, paths = _report_case(
        aggregate_root,
        file_count=2,
        max_objects=1,
    )
    try:
        by_path = {item.path: item for item in report.candidates}
        with pytest.raises(ResidualCleanupRevalidationError, match="hard object"):
            aggregate.revalidator.assess(
                ResidualCleanupRequest(
                    source_report_id=report.report_id,
                    selected_residual_ids=tuple(by_path[path].candidate_id for path in paths),
                )
            )
    finally:
        aggregate.close()

    byte_root = tmp_path / "byte-budget"
    byte_root.mkdir()
    byte_environment, _context, report, _root, paths = _report_case(
        byte_root,
        file_count=1,
        max_bytes=1,
    )
    try:
        candidate = next(item for item in report.candidates if item.path == paths[0])
        assessment = byte_environment.revalidator.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        assert not assessment.all_eligible
    finally:
        byte_environment.close()


def test_tree_cancellation_and_policy_rejection_become_safe_blocks(tmp_path: Path) -> None:
    environment, _context, report, root, _paths = _report_case(tmp_path, file_count=2)
    candidate = next(item for item in report.candidates if item.path == root)

    class DelayedCancellation(CancellationToken):
        def __init__(self) -> None:
            super().__init__()
            self.calls = 0

        def cancellation_requested(self) -> bool:
            self.calls += 1
            return self.calls >= 3

    try:
        with pytest.raises(ResidualCleanupRevalidationError, match="cancelled"):
            environment.revalidator.assess(
                ResidualCleanupRequest(
                    source_report_id=report.report_id,
                    selected_residual_ids=(candidate.candidate_id,),
                ),
                DelayedCancellation(),
            )
        environment.revalidator._path_policy.entry_rejection_reason = lambda _path, _root: (
            "synthetic-protected-descendant"
        )
        assessment = environment.revalidator.assess(
            ResidualCleanupRequest(
                source_report_id=report.report_id,
                selected_residual_ids=(candidate.candidate_id,),
            )
        )
        assert not assessment.all_eligible
    finally:
        environment.close()


def test_root_identity_races_are_blocked_during_and_after_snapshot(tmp_path: Path) -> None:
    environment, _context, report, root, _paths = _report_case(tmp_path)
    candidate = next(item for item in report.candidates if item.path == root)
    request = ResidualCleanupRequest(
        source_report_id=report.report_id,
        selected_residual_ids=(candidate.candidate_id,),
    )
    base_identity = environment.revalidator._identity

    class ChangingIdentity:
        def __init__(self, change_from_call: int) -> None:
            self.calls = 0
            self.change_from_call = change_from_call

        def inspect(self, path: Path):
            state = base_identity.inspect(path)
            if path == root:
                self.calls += 1
                if self.calls >= self.change_from_call:
                    return state.model_copy(update={"modified_ns": state.modified_ns + 1})
            return state

        def move_same_volume(self, source: Path, destination: Path) -> None:
            base_identity.move_same_volume(source, destination)

        def create_directory(self, destination: Path) -> None:
            base_identity.create_directory(destination)

        def remove_empty_directory(self, path: Path) -> None:
            base_identity.remove_empty_directory(path)

    try:
        environment.revalidator._identity = ChangingIdentity(change_from_call=4)
        during = environment.revalidator.assess(request)
        assert not during.all_eligible
        environment.revalidator._identity = ChangingIdentity(change_from_call=2)
        after = environment.revalidator.assess(request)
        assert not after.all_eligible
        assert "root-changed-during-scan" in after.items[0].eligibility_reason_codes
    finally:
        environment.close()


def test_evidence_and_old_identity_helpers_fail_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    environment, context, report, root, _paths = _report_case(tmp_path)
    candidate = next(item for item in report.candidates if item.path == root)
    try:
        with pytest.raises(ResidualCleanupRevalidationError, match="exact"):
            FreshResidualRevalidator._evidence_for(
                candidate,
                context.model_copy(update={"known_paths": ()}),
            )
        assert not FreshResidualRevalidator._old_identity_matches(
            candidate.model_copy(update={"path": tmp_path / "missing"})
        )
        monkeypatch.setattr(
            "pc_manager_agent.safety.residual_cleanup_revalidation.os.lstat",
            lambda _path: SimpleNamespace(
                st_file_attributes=0,
                st_mode=stat.S_IFIFO,
            ),
        )
        assert not FreshResidualRevalidator._old_identity_matches(candidate)
    finally:
        environment.close()
