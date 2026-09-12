from __future__ import annotations

import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db import SessionLocal, engine
from app.services.sportsq_intelligence import (
    list_sportsq_intelligence,
)
from app.services.sportsq_scorecall import (
    SCORECALL_TABLE_NAME,
    exact_score_from_lambdas,
    get_scorecall_lock,
    list_scorecall_locks,
    scorecall_locks_table,
    scorecall_status,
)


def _outcome(home_goals: int, away_goals: int) -> str:
    if home_goals > away_goals:
        return "H"
    if home_goals < away_goals:
        return "A"
    return "D"


@pytest.mark.parametrize(
    "locked",
    ["H", "D", "A"],
)
def test_exact_score_is_conditioned_on_locked_1x2(
    locked,
):
    result = exact_score_from_lambdas(
        1.55,
        1.10,
        locked,
    )

    actual = _outcome(
        result["predicted_home_goals"],
        result["predicted_away_goals"],
    )

    assert actual == locked
    assert result["score_call"] == (
        f'{result["predicted_home_goals"]}-'
        f'{result["predicted_away_goals"]}'
    )
    assert result["score_probability"] > 0
    assert result["conditional_probability"] > 0


def test_scorecall_stage5_status_and_schema():
    with SessionLocal() as session:
        status = scorecall_status(
            session,
            competition_id=2,
            season=2026,
        )

        assert status["table_ready"] is True
        assert status["table"] == SCORECALL_TABLE_NAME
        assert status["immutable"] is True
        assert status["strict_temporal_cutoff"] is True
        assert (
            status[
                "outcome_conditioned_on_existing_1x2_lock"
            ]
            is True
        )
        assert status["live_prediction_engine_modified"] is False
        assert status["live_prediction_rows_modified"] is False
        assert status["final_holdout_touched"] is False
        assert status["lock_count"] >= 1


def test_scorecall_locks_match_source_prediction():
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                '''
                SELECT s.locked_prediction,
                       s.predicted_home_goals,
                       s.predicted_away_goals,
                       s.feature_cutoff_at,
                       s.scorecall_locked_at,
                       s.source_prediction_locked_at,
                       s.fixture_id,
                       lp.prediction AS live_prediction,
                       lp.kickoff_utc
                FROM sportsq_scorecall_locks s
                JOIN live_predictions lp
                  ON lp.id = s.live_prediction_id
                ORDER BY s.id
                '''
            )
        ).mappings().all()

    assert rows

    for row in rows:
        assert row["locked_prediction"] == row["live_prediction"]

        actual = _outcome(
            int(row["predicted_home_goals"]),
            int(row["predicted_away_goals"]),
        )
        assert actual == row["locked_prediction"]

        assert str(row["feature_cutoff_at"]) < str(
            row["kickoff_utc"]
        )
        assert str(
            row["source_prediction_locked_at"]
        ) <= str(row["scorecall_locked_at"])


def test_scorecall_evidence_has_temporal_provenance():
    with SessionLocal() as session:
        rows = list_scorecall_locks(
            session,
            competition_id=2,
            season=2026,
            limit=50,
        )

    assert rows

    for row in rows:
        evidence = row["evidence"]
        assert evidence is not None
        assert evidence["locked_1x2"] == row[
            "locked_prediction"
        ]
        assert evidence["final_holdout_touched"] is False
        assert (
            evidence["source_live_prediction_id"]
            == row["live_prediction_id"]
        )
        assert evidence["history_matches"] >= 100


def test_scorecall_lock_table_rejects_update():
    with engine.connect() as conn:
        first_id = conn.execute(
            select(scorecall_locks_table.c.id)
            .order_by(scorecall_locks_table.c.id)
            .limit(1)
        ).scalar_one()

        # SQLAlchemy 2.x SELECT triggers autobegin.
        # End the read transaction before testing
        # the immutable UPDATE trigger.
        conn.rollback()

        with pytest.raises(DBAPIError):
            conn.execute(
                text(
                    '''
                    UPDATE sportsq_scorecall_locks
                       SET score_call = '9-9'
                     WHERE id = :id
                    '''
                ),
                {"id": int(first_id)},
            )

        conn.rollback()


def test_scorecall_lock_table_rejects_delete():
    with engine.connect() as conn:
        first_id = conn.execute(
            select(scorecall_locks_table.c.id)
            .order_by(scorecall_locks_table.c.id)
            .limit(1)
        ).scalar_one()

        # SQLAlchemy 2.x SELECT triggers autobegin.
        # End the read transaction before testing
        # the immutable DELETE trigger.
        conn.rollback()

        with pytest.raises(DBAPIError):
            conn.execute(
                text(
                    '''
                    DELETE FROM sportsq_scorecall_locks
                     WHERE id = :id
                    '''
                ),
                {"id": int(first_id)},
            )

        conn.rollback()


def test_unknown_scorecall_returns_none():
    with SessionLocal() as session:
        assert (
            get_scorecall_lock(
                session,
                fixture_id=999999999,
            )
            is None
        )


def test_sportsq_intelligence_reads_scorecall_lock():
    with SessionLocal() as session:
        payload = list_sportsq_intelligence(
            session,
            competition_id=2,
            season=2026,
            limit=5,
        )

    assert payload["status"] == "success"
    assert payload["items"]

    available = [
        item
        for item in payload["items"]
        if item["sportsq_score_call"]["score"]
        is not None
    ]

    assert available

    for item in available:
        score = item["sportsq_score_call"]
        assert score["status"] == "AVAILABLE"
        assert score["source"] in {
            "sportsq_scorecall_lock",
            "existing_locked_score_call",
        }


def test_scorecall_lock_rows_do_not_contain_grading():
    with engine.connect() as conn:
        columns = {
            row["name"]
            for row in conn.execute(
                text(
                    "PRAGMA table_info("
                    "sportsq_scorecall_locks)"
                )
            ).mappings()
        }

    assert "actual_home_goals" not in columns
    assert "actual_away_goals" not in columns
    assert "is_correct" not in columns
    assert "graded_at" not in columns
