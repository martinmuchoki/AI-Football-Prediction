from __future__ import annotations

from app.main import app
from app.services.sportsq_intelligence import (
    BRAND_HIERARCHY,
    _confidence_band,
    _form_index_from_matches,
    _normalise_1x2,
    _score_call,
    sportsq_capabilities,
)

def test_brand_hierarchy_is_official():
    assert BRAND_HIERARCHY["brand"] == "MDRN SportsQ"
    assert BRAND_HIERARCHY["predict"] == "SportsQ Predict"
    assert BRAND_HIERARCHY["score_call"] == "SportsQ ScoreCall"
    assert BRAND_HIERARCHY["confidence"] == "SportsQ Confidence"
    assert BRAND_HIERARCHY["news_impact"] == "SportsQ News Impact"
    assert BRAND_HIERARCHY["form_index"] == "SportsQ Form Index"

def test_1x2_normalization():
    assert _normalise_1x2("1") == "HOME"
    assert _normalise_1x2("H") == "HOME"
    assert _normalise_1x2("home") == "HOME"
    assert _normalise_1x2("X") == "DRAW"
    assert _normalise_1x2("draw") == "DRAW"
    assert _normalise_1x2("2") == "AWAY"
    assert _normalise_1x2("A") == "AWAY"

def test_confidence_bands():
    assert _confidence_band(70) == "HIGH"
    assert _confidence_band(60) == "MEDIUM"
    assert _confidence_band(50) == "LOW"
    assert _confidence_band(None) == "UNAVAILABLE"

def test_score_call_does_not_fabricate():
    score, source = _score_call({
        "home_probability": 0.60,
        "draw_probability": 0.25,
        "away_probability": 0.15,
    })
    assert score is None
    assert source == "not_exposed_by_existing_lock"

def test_score_call_uses_existing_locked_score():
    score, source = _score_call({
        "predicted_home_goals": 2,
        "predicted_away_goals": 1,
    })
    assert score == "2-1"
    assert source == "existing_locked_score_call"

def test_form_index_uses_only_supplied_prior_matches():
    matches = [
        {"home_team_id": 1, "away_team_id": 2, "home_goals": 2, "away_goals": 0},
        {"home_team_id": 3, "away_team_id": 1, "home_goals": 1, "away_goals": 1},
        {"home_team_id": 1, "away_team_id": 4, "home_goals": 0, "away_goals": 1},
    ]
    result = _form_index_from_matches(matches, 1)
    assert result["matches"] == 3
    assert result["wins"] == 1
    assert result["draws"] == 1
    assert result["losses"] == 1
    assert result["points"] == 4
    assert 0 <= result["index"] <= 100

def test_stage2_routes_exist():
    paths = {route.path for route in app.routes}
    required = {
        "/api/v1/sportsq/intelligence",
        "/api/v1/sportsq/intelligence/{fixture_id}",
        "/api/v1/sportsq/form/{fixture_id}",
        "/api/v1/sportsq/news-impact/{fixture_id}",
        "/api/v1/sportsq/accuracy",
        "/api/v1/sportsq/capabilities",
    }
    assert required.issubset(paths)

def test_capabilities_preserve_prediction_contract(session):
    result = sportsq_capabilities(session)
    assert result["safety"]["prediction_engine_rewritten"] is False
    assert result["safety"]["live_prediction_rows_modified"] is False
    assert result["safety"]["existing_prediction_locks_immutable"] is True
    assert result["safety"]["historical_holdout_touched"] is False

def test_news_impact_is_not_fabricated(session):
    result = sportsq_capabilities(session)
    news = result["sportsq_news_impact"]
    assert news["fabricated"] is False
    assert news["status"] == "NO_STRUCTURED_TEAM_NEWS_INPUT"
