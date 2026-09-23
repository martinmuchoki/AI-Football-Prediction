from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    UniqueConstraint,
    inspect,
    insert,
    select,
    text,
)
from sqlalchemy.orm import Session

from app.models import Fixture, FixtureReconciliation
from app.services.result_verification import (
    API_FOOTBALL_SOURCE,
    LIVE_SCORE_SOURCE,
)
from app.services.sportsq_scorecall import (
    SCORECALL_TABLE_NAME,
    scorecall_locks_table,
)


SCORECALL_GRADES_TABLE_NAME = "sportsq_scorecall_grades"
GRADING_VERSION = "1.0-verified"

metadata = MetaData()

scorecall_grades_table = Table(
    SCORECALL_GRADES_TABLE_NAME,
    metadata,

    Column("id", Integer, primary_key=True, autoincrement=True),

    Column("scorecall_lock_id", Integer, nullable=False),
    Column("live_prediction_id", Integer, nullable=False),

    Column("fixture_id", Integer, nullable=False),
    Column("provider_fixture_id", Integer, nullable=False),

    Column("competition_id", Integer, nullable=False),
    Column("season", Integer, nullable=False),

    Column("predicted_home_goals", Integer, nullable=False),
    Column("predicted_away_goals", Integer, nullable=False),

    Column("actual_home_goals", Integer, nullable=False),
    Column("actual_away_goals", Integer, nullable=False),

    Column("predicted_score", String(20), nullable=False),
    Column("actual_score", String(20), nullable=False),

    Column("exact_score_correct", Boolean, nullable=False),
    Column("outcome_correct", Boolean, nullable=False),

    # Verification provenance.
    Column("verification_reconciliation_id", Integer, nullable=False),
    Column("verification_primary_source", String(80), nullable=False),
    Column("verification_secondary_source", String(80), nullable=False),
    Column("verification_date_status", String(30), nullable=False),
    Column("verification_result_status", String(30), nullable=False),
    Column("verification_overall_status", String(30), nullable=False),
    Column("verification_checked_at", DateTime(timezone=True), nullable=False),

    Column("grading_version", String(40), nullable=False),
    Column("graded_at", DateTime(timezone=True), nullable=False),

    UniqueConstraint(
        "scorecall_lock_id",
        name="uq_sportsq_scorecall_grade_lock",
    ),

    UniqueConstraint(
        "fixture_id",
        "grading_version",
        name="uq_sportsq_scorecall_grade_fixture_version",
    ),
)


TRIGGER_UPDATE = "trg_sportsq_scorecall_grades_no_update"
TRIGGER_DELETE = "trg_sportsq_scorecall_grades_no_delete"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_utc(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _outcome(home: int, away: int) -> str:
    if home > away:
        return "H"
    if home < away:
        return "A"
    return "D"


def ensure_scorecall_grading_schema(
    session: Session,
) -> dict[str, Any]:
    """
    Create Result Intelligence schema on the SAME database engine
    used by the supplied SQLAlchemy session.

    This intentionally avoids the previous global-engine/test-engine
    split.
    """

    bind = session.get_bind()

    metadata.create_all(
        bind,
        tables=[scorecall_grades_table],
        checkfirst=True,
    )

    if bind.dialect.name == "sqlite":
        with bind.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {TRIGGER_UPDATE}
                    BEFORE UPDATE ON {SCORECALL_GRADES_TABLE_NAME}
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'sportsq_scorecall_grades is immutable'
                        );
                    END
                    """
                )
            )

            conn.execute(
                text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {TRIGGER_DELETE}
                    BEFORE DELETE ON {SCORECALL_GRADES_TABLE_NAME}
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'sportsq_scorecall_grades is immutable'
                        );
                    END
                    """
                )
            )

    existing = set(inspect(bind).get_table_names())

    if SCORECALL_GRADES_TABLE_NAME not in existing:
        raise RuntimeError(
            "ScoreCall grading table was not created."
        )

    return {
        "table": SCORECALL_GRADES_TABLE_NAME,
        "immutable": True,
        "grading_version": GRADING_VERSION,
        "verified_results_only": True,
        "verification_primary_source": API_FOOTBALL_SOURCE,
        "verification_secondary_source": LIVE_SCORE_SOURCE,
        "scorecall_lock_table_modified": False,
        "prediction_engine_rewritten": False,
        "final_holdout_touched": False,
    }


def grade_verified_scorecall_locks(
    session: Session,
    *,
    competition_id: int,
    season: int,
    now: datetime | None = None,
) -> dict[str, Any]:

    ensure_scorecall_grading_schema(session)

    tables = set(
        inspect(session.get_bind()).get_table_names()
    )

    summary: dict[str, Any] = {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),

        "candidates": 0,
        "graded_new": 0,
        "already_graded": 0,

        "skipped_unverified": 0,
        "skipped_unfinished": 0,
        "skipped_missing_score": 0,

        "grading_version": GRADING_VERSION,
        "verified_results_only": True,

        "verification_primary_source": API_FOOTBALL_SOURCE,
        "verification_secondary_source": LIVE_SCORE_SOURCE,

        "scorecall_lock_table_modified": False,
        "prediction_engine_rewritten": False,
        "final_holdout_touched": False,
    }

    if SCORECALL_TABLE_NAME not in tables:
        summary["status"] = "scorecall_table_not_ready"
        return summary

    graded_at = _utc(
        now or datetime.now(timezone.utc)
    )

    stmt = (
        select(scorecall_locks_table)
        .where(
            scorecall_locks_table.c.competition_id
            == int(competition_id),

            scorecall_locks_table.c.season
            == int(season),
        )
        .order_by(
            scorecall_locks_table.c.scorecall_locked_at.asc(),
            scorecall_locks_table.c.id.asc(),
        )
    )

    rows = list(session.execute(stmt).mappings())

    summary["candidates"] = len(rows)

    for lock in rows:

        fixture = session.get(
            Fixture,
            int(lock["fixture_id"]),
        )

        if fixture is None:
            summary["skipped_unfinished"] += 1
            continue

        existing = session.execute(
            select(
                scorecall_grades_table.c.id
            ).where(
                scorecall_grades_table.c.scorecall_lock_id
                == int(lock["id"])
            )
        ).first()

        if existing is not None:
            summary["already_graded"] += 1
            continue

        verification = session.scalar(
            select(FixtureReconciliation)
            .where(
                FixtureReconciliation.competition_id
                == int(competition_id),

                FixtureReconciliation.season
                == int(season),

                FixtureReconciliation.primary_source
                == API_FOOTBALL_SOURCE,

                FixtureReconciliation.secondary_source
                == LIVE_SCORE_SOURCE,

                FixtureReconciliation.primary_fixture_id
                == int(lock["provider_fixture_id"]),

                FixtureReconciliation.date_status
                == "MATCH",

                FixtureReconciliation.result_status
                == "MATCH",

                FixtureReconciliation.overall_status
                == "MATCH",
            )
            .order_by(
                FixtureReconciliation.checked_at.desc(),
                FixtureReconciliation.id.desc(),
            )
            .limit(1)
        )

        if verification is None:
            summary["skipped_unverified"] += 1
            continue

        if not bool(fixture.is_finished):
            summary["skipped_unfinished"] += 1
            continue

        actual_home = (
            fixture.fulltime_home
            if fixture.fulltime_home is not None
            else fixture.home_goals
        )

        actual_away = (
            fixture.fulltime_away
            if fixture.fulltime_away is not None
            else fixture.away_goals
        )

        if actual_home is None or actual_away is None:
            summary["skipped_missing_score"] += 1
            continue

        predicted_home = int(
            lock["predicted_home_goals"]
        )

        predicted_away = int(
            lock["predicted_away_goals"]
        )

        actual_home = int(actual_home)
        actual_away = int(actual_away)

        session.execute(
            insert(scorecall_grades_table).values(
                scorecall_lock_id=int(lock["id"]),
                live_prediction_id=int(
                    lock["live_prediction_id"]
                ),

                fixture_id=int(lock["fixture_id"]),
                provider_fixture_id=int(
                    lock["provider_fixture_id"]
                ),

                competition_id=int(
                    lock["competition_id"]
                ),

                season=int(lock["season"]),

                predicted_home_goals=predicted_home,
                predicted_away_goals=predicted_away,

                actual_home_goals=actual_home,
                actual_away_goals=actual_away,

                predicted_score=(
                    f"{predicted_home}-{predicted_away}"
                ),

                actual_score=(
                    f"{actual_home}-{actual_away}"
                ),

                exact_score_correct=(
                    predicted_home == actual_home
                    and predicted_away == actual_away
                ),

                outcome_correct=(
                    _outcome(
                        predicted_home,
                        predicted_away,
                    )
                    == _outcome(
                        actual_home,
                        actual_away,
                    )
                ),

                verification_reconciliation_id=int(
                    verification.id
                ),

                verification_primary_source=str(
                    verification.primary_source
                ),

                verification_secondary_source=str(
                    verification.secondary_source
                ),

                verification_date_status=str(
                    verification.date_status
                ),

                verification_result_status=str(
                    verification.result_status
                ),

                verification_overall_status=str(
                    verification.overall_status
                ),

                verification_checked_at=_db_utc(
                    verification.checked_at
                ),

                grading_version=GRADING_VERSION,
                graded_at=_db_utc(graded_at),
            )
        )

        summary["graded_new"] += 1

    session.commit()

    return summary


# Compatibility name. It now enforces verified-only grading.
def grade_scorecall_locks(
    session: Session,
    *,
    competition_id: int,
    season: int,
    now: datetime | None = None,
) -> dict[str, Any]:

    return grade_verified_scorecall_locks(
        session,
        competition_id=competition_id,
        season=season,
        now=now,
    )


def scorecall_performance_summary(
    session: Session,
    *,
    competition_id: int,
    season: int,
) -> dict[str, Any]:

    tables = set(
        inspect(session.get_bind()).get_table_names()
    )

    result: dict[str, Any] = {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),

        "graded": 0,

        "exact_score_correct": 0,
        "exact_score_accuracy": None,

        "outcome_correct": 0,
        "outcome_accuracy": None,

        "grading_version": GRADING_VERSION,

        "verified_results_only": True,
        "immutable_grades": True,

        "scorecall_lock_table_modified": False,
        "prediction_engine_rewritten": False,
        "final_holdout_touched": False,
    }

    if SCORECALL_GRADES_TABLE_NAME not in tables:
        result["status"] = "not_ready"
        return result

    rows = list(
        session.execute(
            select(
                scorecall_grades_table
            ).where(
                scorecall_grades_table.c.competition_id
                == int(competition_id),

                scorecall_grades_table.c.season
                == int(season),
            )
        ).mappings()
    )

    result["graded"] = len(rows)

    if not rows:
        return result

    exact = sum(
        1
        for row in rows
        if bool(row["exact_score_correct"])
    )

    outcome = sum(
        1
        for row in rows
        if bool(row["outcome_correct"])
    )

    result.update(
        {
            "exact_score_correct": exact,

            "exact_score_accuracy": round(
                exact / len(rows),
                6,
            ),

            "outcome_correct": outcome,

            "outcome_accuracy": round(
                outcome / len(rows),
                6,
            ),
        }
    )

    return result
