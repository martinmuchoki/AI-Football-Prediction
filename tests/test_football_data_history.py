import httpx
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.models import Fixture, LeagueSeason
from app.providers.football_data import FootballDataClient, season_code
from app.services.football_data_ingestor import (
    ingest_rows,
    parse_csv,
    sync_football_data_history,
)


SAMPLE = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee,HS,AS
E0,15/08/2025,20:00,Liverpool,Bournemouth,4,2,H,2,1,H,A Ref,19,11
E0,16/08/2025,12:30,Aston Villa,Newcastle,0,0,D,0,0,D,B Ref,8,14
"""


def test_season_code():
    assert season_code(2025) == "2526"
    assert season_code(1999) == "9900"


def test_parse_and_ingest_is_idempotent(session):
    rows = parse_csv(SAMPLE)
    first = ingest_rows(session, rows, season=2025, league_code="E0")
    second = ingest_rows(session, rows, season=2025, league_code="E0")

    assert first["inserted"] == 2
    assert second["updated"] == 2
    assert session.scalar(select(func.count(Fixture.id))) == 2
    league = session.scalar(select(LeagueSeason))
    assert league.provider == "football-data-csv"
    assert league.season == 2025


@pytest.mark.asyncio
async def test_client_url_and_sync(session):
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(200, text=SAMPLE)

    settings = Settings(
        _env_file=None,
        fd_base_url="https://www.football-data.co.uk/mmz4281",
    )
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = FootballDataClient(settings, client=http_client)
        result = await sync_football_data_history(
            session,
            client,
            season=2025,
            league_code="E0",
        )

    assert seen["url"].endswith("/2526/E0.csv")
    assert result["received"] == 2
    assert result["inserted"] == 2
    assert result["rejected"] == 0
