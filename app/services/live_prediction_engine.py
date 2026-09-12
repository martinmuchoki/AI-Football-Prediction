from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Fixture, LeagueSeason, LivePrediction, Team
from app.services.high_confidence_selector import DEFAULT_THRESHOLD, POLICY_VERSION, select_prediction


PROVIDER = "live-score-api"
MODEL_NAME = "bookmaker_open"
MARKET_SOURCE = "odds.pre"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _safe_json(value: str | dict[str, Any] | None) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    try:
        parsed = json.loads(value or "{}")
    except (TypeError, json.JSONDecodeError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _decimal_odd(value: Any) -> float:
    try:
        odd = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("pre-match 1X2 odds are missing or invalid") from exc
    if not math.isfinite(odd) or odd <= 1.0:
        raise ValueError("pre-match 1X2 decimal odds must be greater than 1.0")
    return odd


def extract_pre_match_odds(raw: str | dict[str, Any] | None) -> tuple[float, float, float]:
    payload = _safe_json(raw)
    odds = payload.get("odds")
    if not isinstance(odds, dict):
        raise ValueError("fixture does not contain an odds object")
    pre = odds.get("pre")
    if not isinstance(pre, dict):
        raise ValueError("fixture does not contain odds.pre")
    return _decimal_odd(pre.get("1")), _decimal_odd(pre.get("X")), _decimal_odd(pre.get("2"))


def fair_probabilities_from_decimal_odds(home: float, draw: float, away: float) -> tuple[float, float, float, float]:
    home = _decimal_odd(home)
    draw = _decimal_odd(draw)
    away = _decimal_odd(away)
    implied = (1.0 / home, 1.0 / draw, 1.0 / away)
    overround = sum(implied)
    if not math.isfinite(overround) or overround <= 0:
        raise ValueError("invalid bookmaker overround")
    return (
        implied[0] / overround,
        implied[1] / overround,
        implied[2] / overround,
        overround,
    )


def generate_live_predictions(
    session: Session,
    *,
    competition_id: int,
    season: int,
    threshold: float = DEFAULT_THRESHOLD,
    now: datetime | None = None,
    limit: int | None = None,
    provider: str = PROVIDER,
    market_source: str = MARKET_SOURCE,
) -> dict[str, Any]:
    """Create immutable opening-market predictions for upcoming fixtures.

    Only fixtures from the requested Live Score competition/season are considered.
    Existing rows for the frozen policy are skipped, never updated. This makes the
    first captured pre-match odds snapshot auditable and leakage-safe.
    """
    now_utc = _utc(now or datetime.now(timezone.utc))

    Home = aliased(Team)
    Away = aliased(Team)
    stmt = (
        select(Fixture, LeagueSeason, Home.name, Away.name)
        .join(LeagueSeason, Fixture.league_season_id == LeagueSeason.id)
        .join(Home, Fixture.home_team_id == Home.id)
        .join(Away, Fixture.away_team_id == Away.id)
        .where(
            Fixture.provider == provider,
            LeagueSeason.provider == provider,
            LeagueSeason.provider_league_id == int(competition_id),
            LeagueSeason.season == int(season),
            Fixture.status_short == "NS",
            Fixture.is_finished.is_(False),
        )
        .order_by(Fixture.kickoff_utc, Fixture.id)
    )

    candidates = list(session.execute(stmt).all())
    if limit is not None:
        candidates = candidates[: max(0, int(limit))]

    summary: dict[str, Any] = {
        "status": "success",
        "provider": provider,
        "competition_id": int(competition_id),
        "season": int(season),
        "policy_version": POLICY_VERSION,
        "model_name": MODEL_NAME,
        "market_source": market_source,
        "threshold": float(threshold),
        "candidates": len(candidates),
        "locked_new": 0,
        "already_locked": 0,
        "missing_or_invalid_odds": 0,
        "already_started": 0,
        "high_confidence": 0,
        "pass": 0,
        "predictions": [],
        "final_holdout_touched": False,
    }

    for fixture, league, home_name, away_name in candidates:
        kickoff = _utc(fixture.kickoff_utc)
        if kickoff <= now_utc:
            summary["already_started"] += 1
            continue

        existing = session.scalar(
            select(LivePrediction).where(
                LivePrediction.fixture_id == fixture.id,
                LivePrediction.policy_version == POLICY_VERSION,
            )
        )
        if existing is not None:
            summary["already_locked"] += 1
            continue

        try:
            home_odds, draw_odds, away_odds = extract_pre_match_odds(fixture.raw_json)
            p_home, p_draw, p_away, overround = fair_probabilities_from_decimal_odds(
                home_odds, draw_odds, away_odds
            )
        except ValueError:
            summary["missing_or_invalid_odds"] += 1
            continue

        decision = select_prediction(
            p_home,
            p_draw,
            p_away,
            threshold=threshold,
        )

        prediction = LivePrediction(
            fixture_id=fixture.id,
            provider=provider,
            provider_fixture_id=fixture.provider_fixture_id,
            competition_id=league.provider_league_id,
            season=league.season,
            model_name=MODEL_NAME,
            policy_version=POLICY_VERSION,
            market_source=market_source,
            home_odds=home_odds,
            draw_odds=draw_odds,
            away_odds=away_odds,
            market_overround=overround,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            prediction=decision.prediction,
            confidence=decision.confidence,
            threshold=decision.threshold,
            selector_label=decision.label,
            publish=decision.publish,
            odds_snapshot_at=now_utc,
            locked_at=now_utc,
            kickoff_utc=kickoff,
        )
        session.add(prediction)
        summary["locked_new"] += 1
        if decision.publish:
            summary["high_confidence"] += 1
        else:
            summary["pass"] += 1

        summary["predictions"].append({
            "fixture_id": fixture.provider_fixture_id,
            "home": str(home_name),
            "away": str(away_name),
            "kickoff_utc": kickoff.isoformat(),
            "odds": {"H": home_odds, "D": draw_odds, "A": away_odds},
            "fair_probabilities": {
                "H": round(p_home, 6),
                "D": round(p_draw, 6),
                "A": round(p_away, 6),
            },
            "prediction": decision.prediction,
            "confidence": decision.confidence,
            "label": decision.label,
            "publish": decision.publish,
            "locked_at": now_utc.isoformat(),
        })

    session.commit()
    return summary


def list_live_predictions(
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

    rows = []
    for pred, home_name, away_name in session.execute(stmt).all():
        rows.append({
            "fixture_id": pred.provider_fixture_id,
            "home": str(home_name),
            "away": str(away_name),
            "kickoff_utc": _utc(pred.kickoff_utc).isoformat(),
            "prediction": pred.prediction,
            "confidence": round(float(pred.confidence), 6),
            "label": pred.selector_label,
            "publish": bool(pred.publish),
            "locked_at": _utc(pred.locked_at).isoformat(),
        })
    return rows
