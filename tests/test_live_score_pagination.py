from dataclasses import dataclass

import pytest

from app.config import Settings
from app.providers.live_score_api import LiveScoreResponse
from app.services.live_score_ingestor import sync_live_score_fixtures
from tests.test_live_score_ingestion import live_fixture


@dataclass
class FakeClient:
    pages: dict
    settings: Settings

    async def get_fixtures(self, *, competition_id, page=None, **kwargs):
        return self.pages[page]


@pytest.mark.asyncio
async def test_fixture_sync_auto_paginates(session):
    first = live_fixture()
    second = live_fixture()
    second["id"] = 1877287
    second["home"] = {"name": "Arsenal", "id": 18, "logo": None}
    second["away"] = {"name": "Everton", "id": 20, "logo": None}

    client = FakeClient(
        settings=Settings(_env_file=None, ls_api_key="k", ls_api_secret="s", ls_max_pages=10),
        pages={
            1: LiveScoreResponse(data={"fixtures": [first], "next_page": "exists", "prev_page": False}),
            2: LiveScoreResponse(data={"fixtures": [second], "next_page": False, "prev_page": "exists"}),
        },
    )

    result = await sync_live_score_fixtures(
        session,
        client,
        competition_id=2,
        season=2026,
    )

    assert result["pages_processed"] == 2
    assert result["received"] == 2
    assert result["inserted"] == 2
    assert result["failed_page"] is None
    assert result["last_successful_page"] == 2


@pytest.mark.asyncio
async def test_fixture_sync_stops_and_reports_resume_page(session):
    first = live_fixture()

    class FailingClient(FakeClient):
        async def get_fixtures(self, *, competition_id, page=None, **kwargs):
            if page == 2:
                raise RuntimeError("temporary page failure")
            return self.pages[page]

    client = FailingClient(
        settings=Settings(_env_file=None, ls_api_key="k", ls_api_secret="s", ls_max_pages=10),
        pages={1: LiveScoreResponse(data={"fixtures": [first], "next_page": "exists", "prev_page": False})},
    )

    result = await sync_live_score_fixtures(
        session,
        client,
        competition_id=2,
        season=2026,
    )

    assert result["pages_processed"] == 1
    assert result["failed_page"] == 2
    assert result["resume_page"] == 2
    assert result["last_successful_page"] == 1
    assert result["status"] == "partial"
