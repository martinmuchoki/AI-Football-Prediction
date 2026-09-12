from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    inspect,
    insert,
    select,
    text,
)
from sqlalchemy.orm import Session

from app.db import engine
from app.models import Fixture, LivePrediction


SCORECALL_TABLE_NAME = "sportsq_scorecall_locks"
MODEL_NAME = "sportsq-scorecall-lock-conditioned-poisson"
MODEL_VERSION = "1.0"
MAX_GOALS = 8
TEAM_VENUE_WINDOW = 20
PRIOR_WEIGHT = 8.0
MIN_GLOBAL_HISTORY = 100

metadata = MetaData()

scorecall_locks_table = Table(
    SCORECALL_TABLE_NAME,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("live_prediction_id", Integer, nullable=False),
    Column("fixture_id", Integer, nullable=False),
    Column("provider_fixture_id", Integer, nullable=False),
    Column("competition_id", Integer, nullable=False),
    Column("season", Integer, nullable=False),
    Column("policy_version", String(80), nullable=False),
    Column("locked_prediction", String(1), nullable=False),
    Column("predicted_home_goals", Integer, nullable=False),
    Column("predicted_away_goals", Integer, nullable=False),
    Column("score_call", String(20), nullable=False),
    Column("home_lambda", Float, nullable=False),
    Column("away_lambda", Float, nullable=False),
    Column("score_probability", Float, nullable=False),
    Column("conditional_probability", Float, nullable=False),
    Column("outcome_probability_mass", Float, nullable=False),
    Column("model_name", String(120), nullable=False),
    Column("model_version", String(40), nullable=False),
    Column("feature_cutoff_at", DateTime(timezone=True), nullable=False),
    Column("source_prediction_locked_at", DateTime(timezone=True), nullable=False),
    Column("scorecall_locked_at", DateTime(timezone=True), nullable=False),
    Column("history_matches", Integer, nullable=False),
    Column("home_team_venue_matches", Integer, nullable=False),
    Column("away_team_venue_matches", Integer, nullable=False),
    Column("history_latest_kickoff", DateTime(timezone=True), nullable=True),
    Column("evidence_json", Text, nullable=False),
    UniqueConstraint(
        "live_prediction_id",
        name="uq_sportsq_scorecall_live_prediction",
    ),
    UniqueConstraint(
        "fixture_id",
        "policy_version",
        name="uq_sportsq_scorecall_fixture_policy",
    ),
)

TRIGGER_UPDATE = "trg_sportsq_scorecall_locks_no_update"
TRIGGER_DELETE = "trg_sportsq_scorecall_locks_no_delete"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_utc(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _serialise(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            data[key] = _utc(value).isoformat()
    if data.get("evidence_json"):
        try:
            data["evidence"] = json.loads(data["evidence_json"])
        except Exception:
            data["evidence"] = None
    return data


def ensure_scorecall_schema() -> dict[str, Any]:
    metadata.create_all(
        engine,
        tables=[scorecall_locks_table],
        checkfirst=True,
    )

    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            conn.execute(
                text(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS {TRIGGER_UPDATE}
                    BEFORE UPDATE ON {SCORECALL_TABLE_NAME}
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'sportsq_scorecall_locks is immutable'
                        );
                    END
                    '''
                )
            )
            conn.execute(
                text(
                    f'''
                    CREATE TRIGGER IF NOT EXISTS {TRIGGER_DELETE}
                    BEFORE DELETE ON {SCORECALL_TABLE_NAME}
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'sportsq_scorecall_locks is immutable'
                        );
                    END
                    '''
                )
            )

    existing = set(inspect(engine).get_table_names())
    if SCORECALL_TABLE_NAME not in existing:
        raise RuntimeError("ScoreCall lock table was not created.")

    return {
        "table": SCORECALL_TABLE_NAME,
        "immutable": True,
        "update_trigger": TRIGGER_UPDATE,
        "delete_trigger": TRIGGER_DELETE,
    }


def _poisson_probability(lam: float, goals: int) -> float:
    lam = max(0.08, min(float(lam), 6.0))
    return math.exp(-lam) * (lam ** goals) / math.factorial(goals)


def _normalise_outcome(value: str | None) -> str | None:
    raw = str(value or "").strip().upper()
    return {
        "1": "H",
        "H": "H",
        "HOME": "H",
        "X": "D",
        "D": "D",
        "DRAW": "D",
        "2": "A",
        "A": "A",
        "AWAY": "A",
    }.get(raw)


def _matches_outcome(
    home_goals: int,
    away_goals: int,
    locked_prediction: str,
) -> bool:
    locked = _normalise_outcome(locked_prediction)
    if locked == "H":
        return home_goals > away_goals
    if locked == "D":
        return home_goals == away_goals
    if locked == "A":
        return home_goals < away_goals
    return False


def exact_score_from_lambdas(
    home_lambda: float,
    away_lambda: float,
    locked_prediction: str,
    *,
    max_goals: int = MAX_GOALS,
) -> dict[str, Any]:
    locked = _normalise_outcome(locked_prediction)
    if locked is None:
        raise ValueError(
            f"Unsupported locked prediction: {locked_prediction!r}"
        )

    candidates: list[tuple[int, int, float]] = []

    for home_goals in range(max_goals + 1):
        home_p = _poisson_probability(
            home_lambda,
            home_goals,
        )

        for away_goals in range(max_goals + 1):
            if not _matches_outcome(
                home_goals,
                away_goals,
                locked,
            ):
                continue

            probability = home_p * _poisson_probability(
                away_lambda,
                away_goals,
            )
            candidates.append(
                (home_goals, away_goals, probability)
            )

    if not candidates:
        raise RuntimeError(
            "No exact-score candidates matched locked outcome."
        )

    outcome_mass = sum(item[2] for item in candidates)
    best = max(
        candidates,
        key=lambda item: (item[2], -item[0] - item[1]),
    )

    home_goals, away_goals, probability = best

    conditional = (
        probability / outcome_mass
        if outcome_mass > 0
        else 0.0
    )

    return {
        "predicted_home_goals": int(home_goals),
        "predicted_away_goals": int(away_goals),
        "score_call": f"{home_goals}-{away_goals}",
        "score_probability": float(probability),
        "conditional_probability": float(conditional),
        "outcome_probability_mass": float(outcome_mass),
        "locked_prediction": locked,
    }


def _shrink_rate(
    values: list[int],
    prior_mean: float,
    *,
    prior_weight: float = PRIOR_WEIGHT,
) -> float:
    observed_total = float(sum(values))
    observed_count = float(len(values))

    return (
        observed_total
        + float(prior_weight) * float(prior_mean)
    ) / (
        observed_count
        + float(prior_weight)
    )


def _historical_context(
    session: Session,
    *,
    home_team_id: int,
    away_team_id: int,
    feature_cutoff_at: datetime,
) -> dict[str, Any]:
    cutoff_db = _db_utc(feature_cutoff_at)

    stmt = (
        select(Fixture)
        .where(
            Fixture.is_finished.is_(True),
            Fixture.home_goals.is_not(None),
            Fixture.away_goals.is_not(None),
            Fixture.kickoff_utc < cutoff_db,
        )
        .order_by(
            Fixture.kickoff_utc.desc(),
            Fixture.id.desc(),
        )
    )

    history = list(session.scalars(stmt).all())

    if len(history) < MIN_GLOBAL_HISTORY:
        raise RuntimeError(
            "Insufficient historical score data: "
            f"{len(history)} < {MIN_GLOBAL_HISTORY}"
        )

    global_home_avg = (
        sum(float(row.home_goals) for row in history)
        / len(history)
    )
    global_away_avg = (
        sum(float(row.away_goals) for row in history)
        / len(history)
    )

    home_venue = [
        row
        for row in history
        if int(row.home_team_id) == int(home_team_id)
    ][:TEAM_VENUE_WINDOW]

    away_venue = [
        row
        for row in history
        if int(row.away_team_id) == int(away_team_id)
    ][:TEAM_VENUE_WINDOW]

    home_attack = _shrink_rate(
        [int(row.home_goals) for row in home_venue],
        global_home_avg,
    )
    home_defence = _shrink_rate(
        [int(row.away_goals) for row in home_venue],
        global_away_avg,
    )
    away_attack = _shrink_rate(
        [int(row.away_goals) for row in away_venue],
        global_away_avg,
    )
    away_defence = _shrink_rate(
        [int(row.home_goals) for row in away_venue],
        global_home_avg,
    )

    home_lambda = math.sqrt(
        max(home_attack, 0.01)
        * max(away_defence, 0.01)
    )
    away_lambda = math.sqrt(
        max(away_attack, 0.01)
        * max(home_defence, 0.01)
    )

    home_lambda = max(0.15, min(home_lambda, 4.5))
    away_lambda = max(0.15, min(away_lambda, 4.5))

    latest_kickoff = max(
        (_utc(row.kickoff_utc) for row in history),
        default=None,
    )

    return {
        "home_lambda": float(home_lambda),
        "away_lambda": float(away_lambda),
        "history_matches": len(history),
        "home_team_venue_matches": len(home_venue),
        "away_team_venue_matches": len(away_venue),
        "history_latest_kickoff": latest_kickoff,
        "global_home_goals_mean": float(global_home_avg),
        "global_away_goals_mean": float(global_away_avg),
        "home_attack_rate": float(home_attack),
        "home_defence_rate": float(home_defence),
        "away_attack_rate": float(away_attack),
        "away_defence_rate": float(away_defence),
    }


def get_scorecall_lock(
    session: Session,
    *,
    fixture_id: int | None = None,
    live_prediction_id: int | None = None,
) -> dict[str, Any] | None:
    if SCORECALL_TABLE_NAME not in set(
        inspect(session.get_bind()).get_table_names()
    ):
        return None

    if live_prediction_id is not None:
        row = session.execute(
            select(scorecall_locks_table).where(
                scorecall_locks_table.c.live_prediction_id
                == int(live_prediction_id)
            )
        ).mappings().first()

        return _serialise(row) if row else None

    if fixture_id is None:
        return None

    row = session.execute(
        select(scorecall_locks_table).where(
            scorecall_locks_table.c.fixture_id
            == int(fixture_id)
        )
    ).mappings().first()

    if row is None:
        row = session.execute(
            select(scorecall_locks_table).where(
                scorecall_locks_table.c.provider_fixture_id
                == int(fixture_id)
            )
        ).mappings().first()

    return _serialise(row) if row else None


def list_scorecall_locks(
    session: Session,
    *,
    competition_id: int | None = None,
    season: int | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    if SCORECALL_TABLE_NAME not in set(
        inspect(session.get_bind()).get_table_names()
    ):
        return []

    safe_limit = max(1, min(int(limit), 500))

    stmt = (
        select(scorecall_locks_table)
        .order_by(
            scorecall_locks_table.c.scorecall_locked_at.asc(),
            scorecall_locks_table.c.id.asc(),
        )
        .limit(safe_limit)
    )

    if competition_id is not None:
        stmt = stmt.where(
            scorecall_locks_table.c.competition_id
            == int(competition_id)
        )

    if season is not None:
        stmt = stmt.where(
            scorecall_locks_table.c.season
            == int(season)
        )

    rows = session.execute(stmt).mappings().all()
    return [_serialise(row) for row in rows]


def lock_scorecalls_for_existing_predictions(
    session: Session,
    *,
    competition_id: int,
    season: int,
    now: datetime | None = None,
    limit: int | None = None,
) -> dict[str, Any]:
    ensure_scorecall_schema()

    now_utc = _utc(now or datetime.now(timezone.utc))

    stmt = (
        select(LivePrediction, Fixture)
        .join(
            Fixture,
            LivePrediction.fixture_id == Fixture.id,
        )
        .where(
            LivePrediction.competition_id
            == int(competition_id),
            LivePrediction.season
            == int(season),
        )
        .order_by(
            LivePrediction.kickoff_utc.asc(),
            LivePrediction.id.asc(),
        )
    )

    if limit is not None:
        stmt = stmt.limit(max(1, int(limit)))

    rows = session.execute(stmt).all()

    summary: dict[str, Any] = {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),
        "examined": len(rows),
        "locked_new": 0,
        "already_locked": 0,
        "already_started": 0,
        "invalid_prediction": 0,
        "insufficient_history": 0,
        "before_prediction_lock": 0,
        "locks": [],
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "final_holdout_touched": False,
        "live_predictions_modified": False,
    }

    for prediction, fixture in rows:
        existing = get_scorecall_lock(
            session,
            live_prediction_id=int(prediction.id),
        )

        if existing is not None:
            summary["already_locked"] += 1
            continue

        kickoff = _utc(fixture.kickoff_utc)
        prediction_locked_at = _utc(
            prediction.locked_at
        )

        if now_utc >= kickoff:
            summary["already_started"] += 1
            continue

        if now_utc < prediction_locked_at:
            summary["before_prediction_lock"] += 1
            continue

        locked_prediction = _normalise_outcome(
            prediction.prediction
        )

        if locked_prediction is None:
            summary["invalid_prediction"] += 1
            continue

        try:
            context = _historical_context(
                session,
                home_team_id=int(fixture.home_team_id),
                away_team_id=int(fixture.away_team_id),
                feature_cutoff_at=now_utc,
            )
        except RuntimeError:
            summary["insufficient_history"] += 1
            continue

        latest_history = context[
            "history_latest_kickoff"
        ]

        if (
            latest_history is not None
            and latest_history >= now_utc
        ):
            raise RuntimeError(
                "Temporal safety violation: "
                "historical fixture is not before "
                "ScoreCall feature cutoff."
            )

        score = exact_score_from_lambdas(
            context["home_lambda"],
            context["away_lambda"],
            locked_prediction,
        )

        evidence = {
            "method": (
                "lock-conditioned shrinkage Poisson"
            ),
            "model_name": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "locked_1x2": locked_prediction,
            "source_live_prediction_id": int(
                prediction.id
            ),
            "source_prediction_locked_at": (
                prediction_locked_at.isoformat()
            ),
            "feature_cutoff_at": now_utc.isoformat(),
            "kickoff_utc": kickoff.isoformat(),
            "history_rule": (
                "finished fixtures with full-time "
                "goals and kickoff strictly before "
                "feature_cutoff_at"
            ),
            "history_matches": context[
                "history_matches"
            ],
            "home_team_venue_matches": context[
                "home_team_venue_matches"
            ],
            "away_team_venue_matches": context[
                "away_team_venue_matches"
            ],
            "global_home_goals_mean": context[
                "global_home_goals_mean"
            ],
            "global_away_goals_mean": context[
                "global_away_goals_mean"
            ],
            "home_attack_rate": context[
                "home_attack_rate"
            ],
            "home_defence_rate": context[
                "home_defence_rate"
            ],
            "away_attack_rate": context[
                "away_attack_rate"
            ],
            "away_defence_rate": context[
                "away_defence_rate"
            ],
            "home_lambda": context["home_lambda"],
            "away_lambda": context["away_lambda"],
            "max_goals_grid": MAX_GOALS,
            "prior_weight": PRIOR_WEIGHT,
            "final_holdout_touched": False,
        }

        values = {
            "live_prediction_id": int(
                prediction.id
            ),
            "fixture_id": int(fixture.id),
            "provider_fixture_id": int(
                fixture.provider_fixture_id
            ),
            "competition_id": int(
                prediction.competition_id
            ),
            "season": int(prediction.season),
            "policy_version": str(
                prediction.policy_version
            ),
            "locked_prediction": locked_prediction,
            "predicted_home_goals": score[
                "predicted_home_goals"
            ],
            "predicted_away_goals": score[
                "predicted_away_goals"
            ],
            "score_call": score["score_call"],
            "home_lambda": context["home_lambda"],
            "away_lambda": context["away_lambda"],
            "score_probability": score[
                "score_probability"
            ],
            "conditional_probability": score[
                "conditional_probability"
            ],
            "outcome_probability_mass": score[
                "outcome_probability_mass"
            ],
            "model_name": MODEL_NAME,
            "model_version": MODEL_VERSION,
            "feature_cutoff_at": _db_utc(now_utc),
            "source_prediction_locked_at": (
                _db_utc(prediction_locked_at)
            ),
            "scorecall_locked_at": _db_utc(
                now_utc
            ),
            "history_matches": context[
                "history_matches"
            ],
            "home_team_venue_matches": context[
                "home_team_venue_matches"
            ],
            "away_team_venue_matches": context[
                "away_team_venue_matches"
            ],
            "history_latest_kickoff": (
                _db_utc(latest_history)
                if latest_history is not None
                else None
            ),
            "evidence_json": json.dumps(
                evidence,
                sort_keys=True,
            ),
        }

        session.execute(
            insert(scorecall_locks_table).values(
                **values
            )
        )

        summary["locked_new"] += 1
        summary["locks"].append(
            {
                "fixture_id": int(fixture.id),
                "provider_fixture_id": int(
                    fixture.provider_fixture_id
                ),
                "locked_prediction": (
                    locked_prediction
                ),
                "score_call": score[
                    "score_call"
                ],
                "home_lambda": round(
                    context["home_lambda"],
                    6,
                ),
                "away_lambda": round(
                    context["away_lambda"],
                    6,
                ),
                "feature_cutoff_at": (
                    now_utc.isoformat()
                ),
                "kickoff_utc": kickoff.isoformat(),
            }
        )

    session.commit()
    return summary


def scorecall_status(
    session: Session,
    *,
    competition_id: int | None = None,
    season: int | None = None,
) -> dict[str, Any]:
    table_ready = SCORECALL_TABLE_NAME in set(
        inspect(session.get_bind()).get_table_names()
    )

    locks = (
        list_scorecall_locks(
            session,
            competition_id=competition_id,
            season=season,
            limit=500,
        )
        if table_ready
        else []
    )

    return {
        "brand": "MDRN SportsQ",
        "product": "SportsQ ScoreCall",
        "stage": 5,
        "table_ready": table_ready,
        "table": SCORECALL_TABLE_NAME,
        "lock_count": len(locks),
        "immutable": True,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "method": (
            "lock-conditioned shrinkage Poisson"
        ),
        "max_goals_grid": MAX_GOALS,
        "team_venue_window": TEAM_VENUE_WINDOW,
        "prior_weight": PRIOR_WEIGHT,
        "strict_temporal_cutoff": True,
        "outcome_conditioned_on_existing_1x2_lock": True,
        "live_prediction_engine_modified": False,
        "live_prediction_rows_modified": False,
        "separate_grading_required": True,
        "final_holdout_touched": False,
    }
