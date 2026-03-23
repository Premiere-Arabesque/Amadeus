import pytest

from app.core.outcomes import ActionOutcome, OutcomeStatus
from app.core.types import ExecutionMode
from tests.test_support import build_in_memory_memory_service


@pytest.mark.anyio
async def test_memory_service_compacts_and_searches_archive() -> None:
    service, _ = build_in_memory_memory_service(
        max_active_entries=2,
        archive_batch_size=1,
    )

    await service.record_outcome(
        ActionOutcome(
            action_id="step-1",
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            summary="alpha breakfast memory",
        )
    )
    await service.record_outcome(
        ActionOutcome(
            action_id="step-2",
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            summary="beta walking memory",
        )
    )
    await service.record_outcome(
        ActionOutcome(
            action_id="step-3",
            status=OutcomeStatus.SUCCESS,
            mode=ExecutionMode.NARRATIVE,
            summary="gamma reading memory",
        )
    )

    assert len(service.active_entries) == 2
    assert service.archive_entries
    assert "alpha breakfast memory" in service.archive_entries[0].summary

    active_hits, archive_hits = await service.search_memory("alpha breakfast", top_k=3)

    assert active_hits == []
    assert archive_hits
    assert archive_hits[0].archive_id.startswith("arc_")
