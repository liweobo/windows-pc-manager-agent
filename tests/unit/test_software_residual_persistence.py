"""SQLite behavior for Stage 4D3 contexts and immutable reports."""

from __future__ import annotations

from pathlib import Path
from uuid import uuid4

import pytest

from pc_manager_agent.persistence.software_residuals import (
    SoftwareResidualRepository,
    SoftwareResidualStoreError,
)
from tests.fixtures.software_residuals import residual_context


def test_repository_lists_only_eligible_contexts_and_requires_exact_ids(tmp_path: Path) -> None:
    repository = SoftwareResidualRepository(tmp_path / "state.db")
    repository.initialize()
    eligible = residual_context(tmp_path / "eligible")
    failed = residual_context(tmp_path / "failed").model_copy(
        update={"verified_removed": False, "verification_state": "failed"}
    )
    try:
        repository.upsert_context(eligible)
        repository.upsert_context(failed)
        listed = repository.list_eligible_contexts(999)
        assert tuple(item.context_id for item in listed) == (eligible.context_id,)
        assert repository.get_context_for_transaction(eligible.transaction_id) == eligible
        with pytest.raises(SoftwareResidualStoreError, match="Unknown"):
            repository.get_context(uuid4())
        with pytest.raises(SoftwareResidualStoreError, match="No residual context"):
            repository.get_context_for_transaction(uuid4())
        assert repository.latest_report(eligible.context_id) is None
        assert repository.get_candidate(eligible.context_id, uuid4()) is None
    finally:
        repository.close()
    with pytest.raises(SoftwareResidualStoreError, match="not initialized"):
        repository.list_eligible_contexts()
