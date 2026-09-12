from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Fixture, LivePrediction, Team
from app.services.high_confidence_selector import POLICY_VERSION


VALID_RESULTS = {"H", "D", "A"}


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def grade_live_predictions(
    session: Session,
    *,
    competition_id: int,
    season: int,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Grade immutable locked predictions after the fixture result is known."""
    graded_at = _utc(now or datetime.now(timezone.utc))

    stmt = (
        select(LivePrediction, Fixture)
        .join(Fixture, LivePrediction.fixture_id == Fixture.id)
        .where(
            LivePrediction.competition_id == int(competition_id),
            LivePrediction.season == int(season),
            LivePrediction.policy_version == POLICY_VERSION,
            LivePrediction.actual_result.is_(None),
            Fixture.is_finished.is_(True),
        )
        .order_by(LivePrediction.kickoff_utc, LivePrediction.id)
    )

    candidates = list(session.execute(stmt).all())
    graded = 0
    skipped_invalid_result = 0

    for pred, fixture in candidates:
        actual = fixture.result_1x2
        if actual not in VALID_RESULTS:
            skipped_invalid_result += 1
            continue

        pred.actual_result = actual
        pred.is_correct = pred.prediction == actual
        pred.graded_at = graded_at
        graded += 1

    session.commit()

    return {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),
        "candidates": len(candidates),
        "graded_new": graded,
        "skipped_invalid_result": skipped_invalid_result,
        "graded_at": graded_at.isoformat(),
        "final_holdout_touched": False,
    }


def _prob_for_actual(pred: LivePrediction, actual: str) -> float:
    if actual == "H":
        return float(pred.p_home)
    if actual == "D":
        return float(pred.p_draw)
    if actual == "A":
        return float(pred.p_away)
    raise ValueError(f"invalid actual result: {actual}")


def _brier(pred: LivePrediction, actual: str) -> float:
    probs = [float(pred.p_home), float(pred.p_draw), float(pred.p_away)]
    index = {"H": 0, "D": 1, "A": 2}[actual]
    target = [0.0, 0.0, 0.0]
    target[index] = 1.0
    return sum((p - y) ** 2 for p, y in zip(probs, target))


def performance_summary(
    session: Session,
    *,
    competition_id: int,
    season: int,
    high_confidence_only: bool = False,
) -> dict[str, Any]:
    base_stmt = select(LivePrediction).where(
        LivePrediction.competition_id == int(competition_id),
        LivePrediction.season == int(season),
        LivePrediction.policy_version == POLICY_VERSION,
    )
    if high_confidence_only:
        base_stmt = base_stmt.where(LivePrediction.publish.is_(True))

    rows = list(session.scalars(base_stmt.order_by(LivePrediction.kickoff_utc)).all())
    graded = [p for p in rows if p.actual_result in VALID_RESULTS and p.is_correct is not None]
    pending = len(rows) - len(graded)

    result: dict[str, Any] = {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),
        "scope": "high_confidence" if high_confidence_only else "all_locked",
        "locked": len(rows),
        "graded": len(graded),
        "pending": pending,
        "correct": 0,
        "accuracy": None,
        "log_loss": None,
        "brier_multiclass": None,
        "average_confidence": None,
        "prediction_mix": {"H": 0, "D": 0, "A": 0},
        "actual_mix": {"H": 0, "D": 0, "A": 0},
        "final_holdout_touched": False,
    }

    if not graded:
        return result

    correct = sum(bool(p.is_correct) for p in graded)
    log_losses = []
    briers = []
    confidences = []

    for pred in graded:
        actual = str(pred.actual_result)
        prob_actual = max(_prob_for_actual(pred, actual), 1e-15)
        log_losses.append(-math.log(prob_actual))
        briers.append(_brier(pred, actual))
        confidences.append(float(pred.confidence))
        result["prediction_mix"][pred.prediction] += 1
        result["actual_mix"][actual] += 1

    result.update({
        "correct": correct,
        "accuracy": round(correct / len(graded), 6),
        "log_loss": round(sum(log_losses) / len(log_losses), 6),
        "brier_multiclass": round(sum(briers) / len(briers), 6),
        "average_confidence": round(sum(confidences) / len(confidences), 6),
    })
    return result


def live_prediction_rows(
    session: Session,
    *,
    competition_id: int,
    season: int,
    high_confidence_only: bool = False,
) -> list[dict[str, Any]]:
    Home = aliased(Team)
    Away = aliased(Team)
    stmt = (
        select(LivePrediction, Home.name, Away.name)
        .join(Fixture, LivePrediction.fixture_id == Fixture.id)
        .join(Home, Fixture.home_team_id == Home.id)
        .join(Away, Fixture.away_team_id == Away.id)
        .where(
            LivePrediction.competition_id == int(competition_id),
            LivePrediction.season == int(season),
            LivePrediction.policy_version == POLICY_VERSION,
        )
        .order_by(LivePrediction.kickoff_utc, LivePrediction.id)
    )
    if high_confidence_only:
        stmt = stmt.where(LivePrediction.publish.is_(True))

    output = []
    for pred, home_name, away_name in session.execute(stmt).all():
        output.append({
            "fixture_id": pred.provider_fixture_id,
            "home": str(home_name),
            "away": str(away_name),
            "kickoff_utc": _utc(pred.kickoff_utc).isoformat(),
            "prediction": pred.prediction,
            "confidence": round(float(pred.confidence), 6),
            "label": pred.selector_label,
            "publish": bool(pred.publish),
            "locked_at": _utc(pred.locked_at).isoformat(),
            "actual_result": pred.actual_result,
            "is_correct": pred.is_correct,
            "graded_at": _utc(pred.graded_at).isoformat() if pred.graded_at else None,
        })
    return output
