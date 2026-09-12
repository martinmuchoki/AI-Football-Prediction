from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import func, select

from app.live_score_validation import normalize_live_score_fixture
from app.models import Fixture, OddsSnapshot, SourceObservation
from app.services.live_score_ingestor import _ingest
from app.services.odds_history import capture_pre_match_odds, odds_movement


def live_fixture(fixture_id=2000001, odds=None):
    kickoff = datetime.now(timezone.utc) + timedelta(days=3)
    return {
        "time": kickoff.strftime("%H:%M:%S"),
        "id": fixture_id,
        "date": kickoff.date().isoformat(),
        "round": "4",
        "competition": {"id": 2, "name": "Premier League", "is_league": True, "is_cup": False},
        "away": {"name": "Away FC", "id": 902},
        "country": {"name": "England", "id": 19},
        "home": {"name": "Home FC", "id": 901},
        "odds": odds or {"pre": {"1": 1.80, "X": 3.60, "2": 4.50}},
        "location": "Test Stadium",
    }


def ingest(session, raw):
    _ingest(
        session,
        [raw],
        season=2026,
        run_type="test-odds-history",
        normalizer=normalize_live_score_fixture,
    )


def test_capture_is_idempotent_for_unchanged_market(session):
    from app.services.source_registry import seed_default_sources
    seed_default_sources(session)
    ingest(session, live_fixture())

    first = capture_pre_match_odds(session, competition_id=2, season=2026)
    second = capture_pre_match_odds(session, competition_id=2, season=2026)

    assert first["captured_new"] == 1
    assert second["captured_new"] == 0
    assert second["unchanged"] == 1
    assert session.scalar(select(func.count(OddsSnapshot.id))) == 1
    assert session.scalar(select(func.count(SourceObservation.id))) == 6


def test_changed_market_creates_second_snapshot_and_movement(session):
    from app.services.source_registry import seed_default_sources
    seed_default_sources(session)
    ingest(session, live_fixture(fixture_id=2000002))

    capture_pre_match_odds(session, competition_id=2, season=2026)

    fixture = session.scalar(select(Fixture).where(Fixture.provider_fixture_id == 2000002))
    raw = json.loads(fixture.raw_json)
    raw["odds"]["pre"] = {"1": 1.60, "X": 4.00, "2": 5.50}
    fixture.raw_json = json.dumps(raw)
    session.commit()

    capture_pre_match_odds(session, competition_id=2, season=2026)

    assert session.scalar(select(func.count(OddsSnapshot.id))) == 2
    movement = odds_movement(session, provider_fixture_id=2000002)
    assert movement["status"] == "success"
    assert movement["snapshots"] == 2
    assert movement["probability_delta"]["H"] > 0
    assert movement["final_holdout_touched"] is False


def test_missing_odds_are_skipped(session):
    from app.services.source_registry import seed_default_sources
    seed_default_sources(session)
    ingest(session, live_fixture(
        fixture_id=2000003,
        odds={"pre": {"1": None, "X": None, "2": None}},
    ))
    result = capture_pre_match_odds(session, competition_id=2, season=2026)
    assert result["captured_new"] == 0
    assert result["missing_or_invalid_odds"] == 1
