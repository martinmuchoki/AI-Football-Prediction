from types import SimpleNamespace

from app.services.result_verification import (
    _effective_status,
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
