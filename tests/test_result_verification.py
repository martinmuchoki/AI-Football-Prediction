from types import SimpleNamespace

from unittest.mock import patch

from app.services.result_verification import (
    _effective_status,
    _stored_result_quality_gate,
    reconcile_stored_provider_pair,
)


def row(
    *,
    overall="SOURCE_LAG",
    date="MATCH",
    result="SOURCE_LAG",
    finished=True,
    primary_score="2-1",
    secondary_score=None,
):
    return SimpleNamespace(
        overall_status=overall,
        date_status=date,
        result_status=result,
        primary_finished=finished,
        primary_score=primary_score,
        secondary_score=secondary_score,
    )




def test_stored_result_gate_warns_for_schedule_only_conflict():
    assert (
        _stored_result_quality_gate(
            {
                "MATCH": 339,
                "CONFLICT": 31,
                "SOURCE_LAG": 10,
            },
            schedule_only_conflicts=31,
        )
        == "WARN"
    )


def test_stored_result_gate_fails_for_hard_result_conflict():
    assert (
        _stored_result_quality_gate(
            {
                "MATCH": 339,
                "CONFLICT": 31,
                "SOURCE_LAG": 10,
            },
            schedule_only_conflicts=30,
        )
        == "FAIL"
    )



def test_stored_pair_ignores_secondary_only_history(
    session,
):
    primary = {
        "epl:2026:arsenal:chelsea": {
            "fixture_id": 1001,
            "home": "Arsenal",
            "away": "Chelsea",
            "date": "2026-09-01",
            "finished": True,
            "score": "2-1",
        }
    }

    secondary = {
        "epl:2026:arsenal:chelsea": {
            "fixture_id": 2001,
            "home": "Arsenal",
            "away": "Chelsea",
            "date": "2026-09-01",
            "finished": True,
            "score": "2-1",
        },
        "epl:2026:old-team-a:old-team-b": {
            "fixture_id": 2999,
            "home": "Old Team A",
            "away": "Old Team B",
            "date": "2024-01-01",
            "finished": True,
            "score": "1-0",
        },
    }

    def fake_live_rows(
        session_arg,
        *,
        competition_id,
        season,
        primary_source,
    ):
        if primary_source == "api-football":
            return primary

        if primary_source == "live-score-api":
            return secondary

        raise AssertionError(primary_source)

    with patch(
        "app.services.result_verification._live_rows",
        side_effect=fake_live_rows,
    ):
        result = reconcile_stored_provider_pair(
            session,
            competition_id=39,
            season=2026,
        )

    assert result["total"] == 1
    assert result["overall_counts"] == {
        "MATCH": 1,
    }
    assert result["fixture_agreement_rate"] == 1.0
    assert result["quality_gate"] == "PASS"


def test_existing_openfootball_match_is_preserved():
    effective, fallback, conflict = (
        _effective_status(
            row(
                overall="MATCH",
                result="MATCH",
                secondary_score="2-1",
            ),
            None,
        )
    )

    assert effective == "MATCH"
    assert fallback is False
    assert conflict is False


def test_source_lag_can_be_verified_by_livescore():
    base = row()

    verifier = row(
        overall="MATCH",
        result="MATCH",
        secondary_score="2-1",
    )

    effective, fallback, conflict = (
        _effective_status(
            base,
            verifier,
        )
    )

    assert effective == "MATCH"
    assert fallback is True
    assert conflict is False


def test_livescore_conflict_fails_closed():
    base = row()

    verifier = row(
        overall="CONFLICT",
        result="CONFLICT",
        secondary_score="3-1",
    )

    effective, fallback, conflict = (
        _effective_status(
            base,
            verifier,
        )
    )

    assert effective == "CONFLICT"
    assert fallback is False
    assert conflict is True


def test_missing_livescore_result_remains_source_lag():
    effective, fallback, conflict = (
        _effective_status(
            row(),
            None,
        )
    )

    assert effective == "SOURCE_LAG"
    assert fallback is False
    assert conflict is False


def test_openfootball_date_conflict_is_not_overridden():
    base = row(
        overall="CONFLICT",
        date="CONFLICT",
        result="MATCH",
        secondary_score="2-1",
    )

    verifier = row(
        overall="MATCH",
        result="MATCH",
        secondary_score="2-1",
    )

    effective, fallback, conflict = (
        _effective_status(
            base,
            verifier,
        )
    )

    assert effective == "CONFLICT"
    assert fallback is False
    assert conflict is False


def test_fallback_requires_openfootball_score_to_be_missing():
    base = row(
        overall="SOURCE_LAG",
        secondary_score="2-1",
    )

    verifier = row(
        overall="MATCH",
        result="MATCH",
        secondary_score="2-1",
    )

    effective, fallback, conflict = (
        _effective_status(
            base,
            verifier,
        )
    )

    assert effective == "SOURCE_LAG"
    assert fallback is False
    assert conflict is False


def test_unfinished_primary_is_never_backfilled():
    base = row(
        finished=False,
    )

    verifier = row(
        overall="MATCH",
        result="MATCH",
        secondary_score="2-1",
    )

    effective, fallback, conflict = (
        _effective_status(
            base,
            verifier,
        )
    )

    assert effective == "SOURCE_LAG"
    assert fallback is False
    assert conflict is False
