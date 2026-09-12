from sqlalchemy import func, select

from app.models import Fixture, LeagueSeason, Team
from app.services.live_score_ingestor import _ingest
from app.live_score_validation import normalize_live_score_fixture, normalize_live_score_history


def live_fixture():
    return {
        "time": "14:00:00",
        "id": 1877286,
        "date": "2026-09-12",
        "round": "4",
        "competition": {"id": 2, "name": "Premier League", "is_league": True, "is_cup": False},
        "away": {"name": "Hull City", "logo": "https://example.test/hull.png", "id": 490, "country_id": 19},
        "country": {"name": "England", "id": 19},
        "home": {"name": "Chelsea", "logo": "https://example.test/chelsea.png", "id": 17, "country_id": 19},
        "odds": {"pre": {"1": 1.25, "2": 12, "X": 5.5}, "live": {"1": None, "2": None, "X": None}},
        "location": "Stamford Bridge",
    }


def history_match():
    return {
        "id": 555001,
        "fixture_id": 1877286,
        "date": "2026-09-12",
        "scheduled": "14:00",
        "round": "4",
        "status": "FINISHED",
        "time": "FT",
        "competition": {"id": 2, "name": "Premier League", "is_league": True},
        "country": {"name": "England", "id": 19},
        "home": {"name": "Chelsea", "logo": "https://example.test/chelsea.png", "id": 17},
        "away": {"name": "Hull City", "logo": "https://example.test/hull.png", "id": 490},
        "scores": {
            "score": "3 - 1",
            "ht_score": "1 - 0",
            "ft_score": "3 - 1",
            "et_score": "",
            "ps_score": "",
        },
        "location": "Stamford Bridge",
    }


def test_live_fixture_ingestion_is_idempotent(session):
    first = _ingest(
        session, [live_fixture()], season=2026,
        run_type="test-fixtures", normalizer=normalize_live_score_fixture
    )
    second = _ingest(
        session, [live_fixture()], season=2026,
        run_type="test-fixtures", normalizer=normalize_live_score_fixture
    )

    assert first["inserted"] == 1
    assert second["updated"] == 1
    assert session.scalar(select(func.count(Fixture.id))) == 1
    assert session.scalar(select(func.count(Team.id))) == 2
    assert session.scalar(select(func.count(LeagueSeason.id))) == 1


def test_history_updates_existing_fixture_and_grades_result(session):
    _ingest(
        session, [live_fixture()], season=2026,
        run_type="test-fixtures", normalizer=normalize_live_score_fixture
    )
    summary = _ingest(
        session, [history_match()], season=2026,
        run_type="test-history", normalizer=normalize_live_score_history
    )

    row = session.scalar(select(Fixture))
    assert summary["updated"] == 1
    assert row.is_finished is True
    assert row.home_goals == 3
    assert row.away_goals == 1
    assert row.result_1x2 == "HOME"
    assert row.status_short == "FT"
