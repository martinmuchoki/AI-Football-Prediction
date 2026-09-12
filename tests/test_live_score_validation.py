import pytest

from app.live_score_validation import (
    LiveScoreValidationError,
    normalize_live_score_fixture,
    normalize_live_score_history,
)
from tests.test_live_score_ingestion import live_fixture, history_match


def test_fixture_is_utc_and_not_started():
    row = normalize_live_score_fixture(live_fixture(), season=2026)
    assert row.provider_fixture_id == 1877286
    assert row.kickoff_utc.isoformat() == "2026-09-12T14:00:00+00:00"
    assert row.status_short == "NS"
    assert row.is_finished is False


def test_finished_history_parses_scores():
    row = normalize_live_score_history(history_match(), season=2026)
    assert row.fulltime_home == 3
    assert row.fulltime_away == 1
    assert row.result_1x2 == "HOME"
    assert row.is_finished is True


def test_rejects_same_team():
    payload = live_fixture()
    payload["away"]["id"] = payload["home"]["id"]
    with pytest.raises(LiveScoreValidationError):
        normalize_live_score_fixture(payload, season=2026)
