import pytest

from app.validation import FixtureValidationError, normalize_fixture
from tests.sample_data import fixture_payload


def test_normalizes_finished_fixture():
    row = normalize_fixture(fixture_payload(status_short="FT", home_goals=2, away_goals=1))
    assert row.provider_fixture_id == 1001
    assert row.is_finished is True
    assert row.result_1x2 == "HOME"
    assert row.home_goals == 2
    assert row.away_goals == 1
    assert row.kickoff_utc.tzinfo is not None


def test_rejects_finished_fixture_without_score():
    with pytest.raises(FixtureValidationError):
        normalize_fixture(fixture_payload(status_short="FT", home_goals=None, away_goals=None))


def test_rejects_same_home_and_away_team():
    payload = fixture_payload()
    payload["teams"]["away"]["id"] = payload["teams"]["home"]["id"]
    with pytest.raises(FixtureValidationError):
        normalize_fixture(payload)


def test_rejects_negative_score():
    payload = fixture_payload(status_short="FT", home_goals=-1, away_goals=1)
    with pytest.raises(FixtureValidationError):
        normalize_fixture(payload)
