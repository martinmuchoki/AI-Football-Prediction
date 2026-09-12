from sqlalchemy import func, select

from app.models import Fixture, LeagueSeason, Team
from app.services.fixture_ingestor import ingest_payload
from tests.sample_data import fixture_payload


def test_ingestion_is_idempotent(session):
    first = ingest_payload(session, [fixture_payload()], "fixtures")
    second = ingest_payload(session, [fixture_payload()], "fixtures")

    assert first["inserted"] == 1
    assert first["updated"] == 0
    assert second["inserted"] == 0
    assert second["updated"] == 1

    assert session.scalar(select(func.count(Fixture.id))) == 1
    assert session.scalar(select(func.count(Team.id))) == 2
    assert session.scalar(select(func.count(LeagueSeason.id))) == 1


def test_finished_result_updates_existing_fixture(session):
    ingest_payload(session, [fixture_payload()], "fixtures")
    ingest_payload(
        session,
        [fixture_payload(status_short="FT", home_goals=3, away_goals=2)],
        "results",
    )

    row = session.scalar(select(Fixture))
    assert row.is_finished is True
    assert row.home_goals == 3
    assert row.away_goals == 2
    assert row.result_1x2 == "HOME"
