from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from app.live_score_validation import normalize_live_score_fixture
from app.models import Fixture, LivePrediction
from app.services.live_prediction_engine import (
    extract_pre_match_odds,
    fair_probabilities_from_decimal_odds,
    generate_live_predictions,
)
from app.services.live_score_ingestor import _ingest


def live_fixture(fixture_id=1877286, competition_id=2, odds=None, days_ahead=3):
    kickoff = datetime.now(timezone.utc) + timedelta(days=days_ahead)
    return {
        "time": kickoff.strftime("%H:%M:%S"),
        "id": fixture_id,
        "date": kickoff.date().isoformat(),
        "round": "4",
        "competition": {"id": competition_id, "name": "Premier League", "is_league": True, "is_cup": False},
        "away": {"name": "Hull City", "id": 490},
        "country": {"name": "England", "id": 19},
        "home": {"name": "Chelsea", "id": 17},
        "odds": odds or {"pre": {"1": 1.25, "2": 12, "X": 5.5}, "live": {"1": None, "2": None, "X": None}},
        "location": "Stamford Bridge",
    }


def ingest_fixture(session, raw, season=2026):
    return _ingest(session, [raw], season=season, run_type="test-live", normalizer=normalize_live_score_fixture)


def test_extract_and_devig_pre_match_odds():
    h, d, a = extract_pre_match_odds(live_fixture())
    ph, pd, pa, overround = fair_probabilities_from_decimal_odds(h, d, a)
    assert (h, d, a) == (1.25, 5.5, 12.0)
    assert abs((ph + pd + pa) - 1.0) < 1e-12
    assert ph > 0.65
    assert overround > 1.0


def test_live_prediction_is_locked_and_idempotent(session):
    ingest_fixture(session, live_fixture())
    first = generate_live_predictions(session, competition_id=2, season=2026)
    assert first["locked_new"] == 1
    assert first["high_confidence"] == 1
    assert first["pass"] == 0

    stored = session.scalar(select(LivePrediction))
    original_home_odds = stored.home_odds
    assert stored.selector_label == "HIGH_CONFIDENCE"
    assert stored.prediction == "H"
    assert stored.publish is True

    fixture = session.scalar(select(Fixture))
    raw = json.loads(fixture.raw_json)
    raw["odds"]["pre"] = {"1": 4.0, "X": 3.0, "2": 1.8}
    fixture.raw_json = json.dumps(raw)
    session.commit()

    second = generate_live_predictions(session, competition_id=2, season=2026)
    assert second["locked_new"] == 0
    assert second["already_locked"] == 1
    stored_again = session.scalar(select(LivePrediction))
    assert stored_again.home_odds == original_home_odds


def test_missing_odds_are_skipped(session):
    raw = live_fixture(fixture_id=1877287, odds={"pre": {"1": None, "X": None, "2": None}})
    ingest_fixture(session, raw)
    result = generate_live_predictions(session, competition_id=2, season=2026)
    assert result["locked_new"] == 0
    assert result["missing_or_invalid_odds"] == 1


def test_competition_filter_is_enforced(session):
    ingest_fixture(session, live_fixture(fixture_id=1877288, competition_id=99))
    result = generate_live_predictions(session, competition_id=2, season=2026)
    assert result["candidates"] == 0
    assert session.scalar(select(LivePrediction)) is None


def test_pass_prediction_is_saved_but_not_published(session):
    odds = {"pre": {"1": 2.5, "X": 3.4, "2": 2.62}}
    ingest_fixture(session, live_fixture(fixture_id=1877289, odds=odds))
    result = generate_live_predictions(session, competition_id=2, season=2026)
    assert result["locked_new"] == 1
    assert result["pass"] == 1
    stored = session.scalar(select(LivePrediction))
    assert stored.selector_label == "PASS"
    assert stored.publish is False
    assert result["final_holdout_touched"] is False
