from datetime import datetime, timezone

from app.models import Fixture, LeagueSeason, LivePrediction, Team
from app.services.prediction_grading import grade_live_predictions, performance_summary


def _seed(session, *, finished=True, actual="H", prediction="H", publish=True):
    league = LeagueSeason(
        provider="live-score-api",
        provider_league_id=2,
        season=2026,
        name="Premier League",
    )
    home = Team(provider="live-score-api", provider_team_id=101, name="Home")
    away = Team(provider="live-score-api", provider_team_id=102, name="Away")
    session.add_all([league, home, away])
    session.flush()

    fixture = Fixture(
        provider="live-score-api",
        provider_fixture_id=9001,
        league_season_id=league.id,
        home_team_id=home.id,
        away_team_id=away.id,
        kickoff_utc=datetime(2026, 9, 1, tzinfo=timezone.utc),
        status_short="FT" if finished else "NS",
        is_finished=finished,
        result_1x2=actual if finished else None,
        raw_json="{}",
    )
    session.add(fixture)
    session.flush()

    pred = LivePrediction(
        fixture_id=fixture.id,
        provider="live-score-api",
        provider_fixture_id=9001,
        competition_id=2,
        season=2026,
        model_name="bookmaker_open",
        policy_version="phase5-epl-v1",
        market_source="odds.pre",
        home_odds=1.5,
        draw_odds=4.0,
        away_odds=6.0,
        market_overround=1.083333,
        p_home=0.615385,
        p_draw=0.230769,
        p_away=0.153846,
        prediction=prediction,
        confidence=0.615385,
        threshold=0.65,
        selector_label="HIGH_CONFIDENCE" if publish else "PASS",
        publish=publish,
        odds_snapshot_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        locked_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        kickoff_utc=datetime(2026, 9, 1, tzinfo=timezone.utc),
    )
    session.add(pred)
    session.commit()
    return pred


def test_grades_finished_prediction(session):
    pred = _seed(session)
    result = grade_live_predictions(session, competition_id=2, season=2026)
    session.refresh(pred)
    assert result["graded_new"] == 1
    assert pred.actual_result == "H"
    assert pred.is_correct is True
    assert pred.graded_at is not None


def test_does_not_grade_unfinished(session):
    pred = _seed(session, finished=False)
    result = grade_live_predictions(session, competition_id=2, season=2026)
    session.refresh(pred)
    assert result["graded_new"] == 0
    assert pred.actual_result is None


def test_performance_summary(session):
    _seed(session)
    grade_live_predictions(session, competition_id=2, season=2026)
    result = performance_summary(session, competition_id=2, season=2026)
    assert result["locked"] == 1
    assert result["graded"] == 1
    assert result["correct"] == 1
    assert result["accuracy"] == 1.0
    assert result["log_loss"] is not None
    assert result["brier_multiclass"] is not None
    assert result["final_holdout_touched"] is False
