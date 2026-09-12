from dataclasses import dataclass

import pytest

from app.config import Settings
from app.providers.live_score_api import LiveScoreResponse
from app.services.live_score_ingestor import sync_live_score_history
from tests.test_live_score_ingestion import history_match


def make_history(match_id: int, fixture_id: int, day: int):
    row = history_match()
    row["id"] = match_id
    row["fixture_id"] = fixture_id
    row["date"] = f"2025-08-{day:02d}"
    return row


@dataclass
class FakeHistoryClient:
    pages: dict
    settings: Settings

    async def get_history(self, *, competition_id, from_date=None, to_date=None, page=None, **kwargs):
        return self.pages[page]


@pytest.mark.asyncio
async def test_history_sync_probes_next_page_when_full_page_has_no_next_page(session):
    page1 = [make_history(600000 + i, 1800000 + i, (i % 28) + 1) for i in range(30)]
    page2 = [make_history(700000 + i, 1900000 + i, (i % 20) + 1) for i in range(20)]

    client = FakeHistoryClient(
        settings=Settings(_env_file=None, ls_api_key="k", ls_api_secret="s", ls_max_pages=10),
        pages={
            1: LiveScoreResponse(data={"match": page1}),
            2: LiveScoreResponse(data={"match": page2}),
        },
    )

    result = await sync_live_score_history(
        session,
        client,
        competition_id=2,
        season=2025,
    )

    assert result["pages_processed"] == 2
    assert result["received"] == 50
    assert result["inserted"] == 50
    assert result["failed_page"] is None
    assert result["last_successful_page"] == 2
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_history_sync_stops_on_empty_probe_page(session):
    page1 = [make_history(800000 + i, 2000000 + i, (i % 28) + 1) for i in range(30)]

    client = FakeHistoryClient(
        settings=Settings(_env_file=None, ls_api_key="k", ls_api_secret="s", ls_max_pages=10),
        pages={
            1: LiveScoreResponse(data={"match": page1}),
            2: LiveScoreResponse(data={"match": []}),
        },
    )

    result = await sync_live_score_history(
        session,
        client,
        competition_id=2,
        season=2025,
    )

    assert result["pages_processed"] == 2
    assert result["received"] == 30
    assert result["failed_page"] is None
    assert result["last_successful_page"] == 2
    assert result["status"] == "success"


@pytest.mark.asyncio
async def test_history_sync_duplicate_page_guard(session):
    page1 = [make_history(900000 + i, 2100000 + i, (i % 28) + 1) for i in range(30)]

    client = FakeHistoryClient(
        settings=Settings(_env_file=None, ls_api_key="k", ls_api_secret="s", ls_max_pages=10),
        pages={
            1: LiveScoreResponse(data={"match": page1}),
            2: LiveScoreResponse(data={"match": page1}),
        },
    )

    result = await sync_live_score_history(
        session,
        client,
        competition_id=2,
        season=2025,
    )

    assert result["pages_processed"] == 1
    assert result["failed_page"] == 2
    assert result["resume_page"] == 2
    assert result["pagination_guard_triggered"] is True
    assert result["status"] == "partial"
