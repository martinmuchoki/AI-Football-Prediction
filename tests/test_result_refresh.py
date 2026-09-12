from datetime import datetime, timezone

import pytest
from sqlalchemy import select

from app.models import Fixture
from app.providers.api_football import ApiResponse
from app.services.fixture_ingestor import ingest_payload, refresh_unfinished_results
from tests.sample_data import fixture_payload


class FakeClient:
    async def get_fixtures(self, **kwargs):
        assert kwargs["ids"] == [1001]
        return ApiResponse(
            data=[fixture_payload(
                status_short="FT",
                home_goals=1,
                away_goals=1,
                kickoff="2026-09-08T19:00:00+00:00",
            )],
            results=1,
            page_current=1,
            page_total=1,
            requests_remaining=88,
        )


@pytest.mark.asyncio
async def test_refresh_unfinished_result(session):
    ingest_payload(
        session,
        [fixture_payload(kickoff="2026-09-08T19:00:00+00:00")],
        "fixtures",
    )

    summary = await refresh_unfinished_results(
        session,
        FakeClient(),
        lookback_hours=24,
        now=datetime(2026, 9, 8, 22, 0, tzinfo=timezone.utc),
    )

    row = session.scalar(select(Fixture))
    assert summary["requested"] == 1
    assert summary["updated"] == 1
    assert row.is_finished is True
    assert row.result_1x2 == "DRAW"
