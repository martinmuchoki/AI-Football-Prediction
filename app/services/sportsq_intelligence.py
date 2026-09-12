from __future__ import annotations

import importlib
import inspect as pyinspect
import math
from datetime import datetime
from typing import Any

from sqlalchemy import inspect, text
from sqlalchemy.orm import Session

BRAND_HIERARCHY = {
    "brand": "MDRN SportsQ",
    "predict": "SportsQ Predict",
    "score_call": "SportsQ ScoreCall",
    "confidence": "SportsQ Confidence",
    "news_impact": "SportsQ News Impact",
    "form_index": "SportsQ Form Index",
}

FORM_WINDOW = 5

CORE_INTELLIGENCE_MODULES = (
    "baseline_models",
    "ensemble_models",
    "feature_engineering",
    "high_confidence_selector",
    "live_prediction_engine",
    "prediction_grading",
    "social_content",
)

PREDICTION_FIELDS = (
    "prediction", "predicted_result", "predicted_1x2", "selection",
    "pick", "recommended_outcome", "recommended_pick", "outcome",
)
HOME_PROBABILITY_FIELDS = (
    "home_probability", "home_prob", "prob_home", "p_home",
    "home_win_probability", "model_home_probability",
)
DRAW_PROBABILITY_FIELDS = (
    "draw_probability", "draw_prob", "prob_draw", "p_draw",
    "model_draw_probability",
)
AWAY_PROBABILITY_FIELDS = (
    "away_probability", "away_prob", "prob_away", "p_away",
    "away_win_probability", "model_away_probability",
)
CONFIDENCE_FIELDS = (
    "confidence", "confidence_pct", "confidence_percent",
    "model_confidence", "prediction_confidence",
)
SCORE_STRING_FIELDS = (
    "score_call", "predicted_score", "score_prediction",
    "predicted_scoreline",
)
SCORE_PAIR_FIELDS = (
    ("predicted_home_goals", "predicted_away_goals"),
    ("predicted_home_score", "predicted_away_score"),
    ("home_score_prediction", "away_score_prediction"),
)

def _table_names(session: Session) -> set[str]:
    return set(inspect(session.get_bind()).get_table_names())

def _columns(session: Session, table: str) -> set[str]:
    return {
        column["name"]
        for column in inspect(session.get_bind()).get_columns(table)
    }

def _value(row: dict[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name not in row:
            continue
        value = row[name]
        if value is None:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        return value
    return None

def _to_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None

def _probability_value(value: Any) -> float | None:
    number = _to_float(value)
    if number is None:
        return None
    if number > 1.0:
        number /= 100.0
    return max(0.0, min(1.0, number))

def _probabilities(row: dict[str, Any]) -> dict[str, float] | None:
    home = _probability_value(_value(row, HOME_PROBABILITY_FIELDS))
    draw = _probability_value(_value(row, DRAW_PROBABILITY_FIELDS))
    away = _probability_value(_value(row, AWAY_PROBABILITY_FIELDS))
    if home is None or draw is None or away is None:
        return None
    total = home + draw + away
    if total <= 0:
        return None
    return {
        "HOME": home / total,
        "DRAW": draw / total,
        "AWAY": away / total,
    }

def _normalise_1x2(value: Any) -> str | None:
    if value is None:
        return None
    raw = str(value).strip().upper()
    return {
        "1": "HOME", "H": "HOME", "HOME": "HOME", "HOME WIN": "HOME",
        "X": "DRAW", "D": "DRAW", "DRAW": "DRAW",
        "2": "AWAY", "A": "AWAY", "AWAY": "AWAY", "AWAY WIN": "AWAY",
    }.get(raw)

def _prediction_label(row: dict[str, Any]) -> tuple[str | None, str]:
    direct = _normalise_1x2(_value(row, PREDICTION_FIELDS))
    if direct is not None:
        return direct, "existing_locked_prediction"
    probabilities = _probabilities(row)
    if probabilities:
        return max(probabilities, key=probabilities.get), "existing_locked_probabilities"
    return None, "not_exposed_by_existing_lock"

def _confidence_percent(row: dict[str, Any]) -> tuple[float | None, str]:
    direct = _to_float(_value(row, CONFIDENCE_FIELDS))
    if direct is not None:
        if direct <= 1.0:
            direct *= 100.0
        return round(max(0.0, min(100.0, direct)), 2), "existing_locked_confidence"
    probabilities = _probabilities(row)
    if probabilities:
        return round(max(probabilities.values()) * 100.0, 2), "existing_locked_probabilities"
    return None, "not_exposed_by_existing_lock"

def _confidence_band(percent: float | None) -> str:
    if percent is None:
        return "UNAVAILABLE"
    if percent >= 65:
        return "HIGH"
    if percent >= 55:
        return "MEDIUM"
    return "LOW"

def _score_call(row: dict[str, Any]) -> tuple[str | None, str]:
    direct = _value(row, SCORE_STRING_FIELDS)
    if direct is not None:
        return str(direct), "existing_locked_score_call"
    for home_field, away_field in SCORE_PAIR_FIELDS:
        if home_field not in row or away_field not in row:
            continue
        home = row.get(home_field)
        away = row.get(away_field)
        if home is None or away is None:
            continue
        try:
            return f"{int(home)}-{int(away)}", "existing_locked_score_call"
        except (TypeError, ValueError):
            continue
    return None, "not_exposed_by_existing_lock"

def _form_band(value: float | None) -> str:
    if value is None:
        return "UNAVAILABLE"
    if value >= 75:
        return "EXCELLENT"
    if value >= 60:
        return "STRONG"
    if value >= 45:
        return "MIXED"
    if value >= 30:
        return "WEAK"
    return "POOR"

def _form_index_from_matches(
    matches: list[dict[str, Any]],
    team_id: int,
) -> dict[str, Any]:
    if not matches:
        return {
            "index": None, "band": "UNAVAILABLE", "matches": 0,
            "wins": 0, "draws": 0, "losses": 0,
            "goals_for": 0, "goals_against": 0, "points": 0,
        }

    wins = draws = losses = 0
    goals_for = goals_against = points = 0

    for match in matches:
        home_team = int(match["home_team_id"])
        away_team = int(match["away_team_id"])
        home_goals = int(match["home_goals"] or 0)
        away_goals = int(match["away_goals"] or 0)

        if home_team == int(team_id):
            gf, ga = home_goals, away_goals
        elif away_team == int(team_id):
            gf, ga = away_goals, home_goals
        else:
            continue

        goals_for += gf
        goals_against += ga

        if gf > ga:
            wins += 1
            points += 3
        elif gf == ga:
            draws += 1
            points += 1
        else:
            losses += 1

    count = wins + draws + losses
    if count <= 0:
        return {
            "index": None, "band": "UNAVAILABLE", "matches": 0,
            "wins": 0, "draws": 0, "losses": 0,
            "goals_for": 0, "goals_against": 0, "points": 0,
        }

    points_ratio = points / (count * 3.0)
    average_goal_difference = (goals_for - goals_against) / count
    goal_difference_ratio = max(
        0.0,
        min(1.0, (average_goal_difference + 2.0) / 4.0),
    )
    form_index = round(
        max(0.0, min(100.0, points_ratio * 75.0 + goal_difference_ratio * 25.0)),
        1,
    )

    return {
        "index": form_index,
        "band": _form_band(form_index),
        "matches": count,
        "wins": wins,
        "draws": draws,
        "losses": losses,
        "goals_for": goals_for,
        "goals_against": goals_against,
        "points": points,
    }

def _fixture(session: Session, fixture_id: int) -> dict[str, Any] | None:
    if "fixtures" not in _table_names(session):
        return None
    columns = _columns(session, "fixtures")
    conditions = ["id = :fixture_id"]
    if "provider_fixture_id" in columns:
        conditions.append("provider_fixture_id = :fixture_id")
    row = session.execute(
        text(
            'SELECT * FROM "fixtures" WHERE '
            + " OR ".join(conditions)
            + " LIMIT 1"
        ),
        {"fixture_id": int(fixture_id)},
    ).mappings().first()
    return dict(row) if row else None

def _team_name(session: Session, team_id: int | None) -> str | None:
    if team_id is None or "teams" not in _table_names(session):
        return None
    row = session.execute(
        text('SELECT name FROM "teams" WHERE id = :team_id LIMIT 1'),
        {"team_id": int(team_id)},
    ).first()
    return str(row[0]) if row else None

def _recent_team_form(
    session: Session,
    *,
    team_id: int,
    kickoff: Any,
    window: int = FORM_WINDOW,
) -> dict[str, Any]:
    if "fixtures" not in _table_names(session):
        return {"index": None, "band": "UNAVAILABLE", "matches": 0}

    required = {
        "home_team_id", "away_team_id", "home_goals", "away_goals",
        "kickoff_utc", "is_finished",
    }
    if not required.issubset(_columns(session, "fixtures")):
        return {"index": None, "band": "UNAVAILABLE", "matches": 0}

    rows = session.execute(
        text(
            'SELECT home_team_id, away_team_id, home_goals, away_goals, kickoff_utc '
            'FROM "fixtures" '
            'WHERE is_finished = true '
            'AND kickoff_utc < :kickoff '
            'AND (home_team_id = :team_id OR away_team_id = :team_id) '
            'ORDER BY kickoff_utc DESC '
            'LIMIT :window'
        ),
        {
            "team_id": int(team_id),
            "kickoff": kickoff,
            "window": max(1, min(int(window), 20)),
        },
    ).mappings().all()

    return _form_index_from_matches([dict(row) for row in rows], int(team_id))

def _news_impact(
    session: Session | None = None,
    fixture_id: int | None = None,
) -> dict[str, Any]:
    if session is None or fixture_id is None:
        return {
            "status": "NO_STRUCTURED_TEAM_NEWS_INPUT",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
            "fabricated": False,
            "note": (
                "No structured verified team-news feed is currently "
                "available for this request. No injury/news impact "
                "is inferred or fabricated."
            ),
        }

    # --- MDRN SPORTSQ STAGE 6 NEWS IMPACT ---
    from app.services.sportsq_news_impact import (
        news_impact_for_fixture as _sportsq_news_impact_for_fixture,
    )

    return _sportsq_news_impact_for_fixture(
        session,
        fixture_id=int(fixture_id),
    )
    # --- END MDRN SPORTSQ STAGE 6 NEWS IMPACT ---


def _module_status(name: str) -> dict[str, Any]:
    try:
        importlib.import_module(f"app.services.{name}")
        return {"module": name, "status": "AVAILABLE"}
    except Exception as exc:
        return {
            "module": name,
            "status": "UNAVAILABLE",
            "error_type": type(exc).__name__,
        }

def _invoke_existing(
    module_name: str,
    function_name: str,
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int | None = None,
) -> Any:
    module = importlib.import_module(f"app.services.{module_name}")
    function = getattr(module, function_name)
    signature = pyinspect.signature(function)
    values = {
        "competition_id": int(competition_id),
        "competition": int(competition_id),
        "season": int(season),
        "limit": int(limit) if limit is not None else None,
    }
    kwargs = {}
    for name in signature.parameters:
        if name in {"session", "db"}:
            continue
        if name in values and values[name] is not None:
            kwargs[name] = values[name]
    return function(session, **kwargs)

def _raw_prediction_rows(
    session: Session,
    *,
    limit: int,
) -> list[dict[str, Any]]:
    if "live_predictions" not in _table_names(session):
        return []
    columns = _columns(session, "live_predictions")
    order_column = "id" if "id" in columns else next(iter(columns))
    rows = session.execute(
        text(
            'SELECT * FROM "live_predictions" '
            f'ORDER BY "{order_column}" DESC LIMIT :limit'
        ),
        {"limit": max(1, min(int(limit), 1000))},
    ).mappings().all()
    return [dict(row) for row in rows]

def _existing_prediction_rows(
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int,
) -> tuple[list[dict[str, Any]], str]:
    try:
        rows = _invoke_existing(
            "prediction_grading",
            "live_prediction_rows",
            session,
            competition_id=competition_id,
            season=season,
            limit=limit,
        )
        if isinstance(rows, dict):
            for key in ("rows", "predictions", "items"):
                if isinstance(rows.get(key), list):
                    rows = rows[key]
                    break
        if isinstance(rows, list):
            return (
                [dict(row) for row in rows if isinstance(row, dict)],
                "prediction_grading.live_prediction_rows",
            )
    except Exception:
        pass

    return (
        _raw_prediction_rows(session, limit=limit),
        "live_predictions.read_only_fallback",
    )

def _row_fixture_id(row: dict[str, Any]) -> int | None:
    value = _value(row, ("fixture_id", "internal_fixture_id", "provider_fixture_id"))
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None

def _intelligence_item(session: Session, row: dict[str, Any]) -> dict[str, Any]:
    fixture_id = _row_fixture_id(row)
    fixture = _fixture(session, fixture_id) if fixture_id is not None else None
    fixture = fixture or {}

    internal_fixture_id = fixture.get("id") or fixture_id
    provider_fixture_id = (
        fixture.get("provider_fixture_id") or row.get("provider_fixture_id")
    )
    home_team_id = fixture.get("home_team_id")
    away_team_id = fixture.get("away_team_id")
    kickoff = fixture.get("kickoff_utc") or row.get("kickoff_utc")

    home_name = (
        _value(row, ("home_team", "home_team_name", "home"))
        or _team_name(session, home_team_id)
    )
    away_name = (
        _value(row, ("away_team", "away_team_name", "away"))
        or _team_name(session, away_team_id)
    )

    prediction, prediction_source = _prediction_label(row)
    confidence, confidence_source = _confidence_percent(row)
    score_call, score_source = _score_call(row)
    # --- MDRN SPORTSQ STAGE 5 SCORECALL LOCK ---
    if score_call is None:
        from app.services.sportsq_scorecall import get_scorecall_lock as _get_sportsq_scorecall_lock
        _sportsq_locked_score = _get_sportsq_scorecall_lock(
            session,
            fixture_id=_row_fixture_id(row),
        )
        if _sportsq_locked_score is not None:
            score_call = _sportsq_locked_score.get("score_call")
            score_source = "sportsq_scorecall_lock"
    # --- END MDRN SPORTSQ STAGE 5 SCORECALL LOCK ---

    home_form = (
        _recent_team_form(
            session,
            team_id=int(home_team_id),
            kickoff=kickoff,
        )
        if home_team_id is not None and kickoff is not None
        else {"index": None, "band": "UNAVAILABLE", "matches": 0}
    )

    away_form = (
        _recent_team_form(
            session,
            team_id=int(away_team_id),
            kickoff=kickoff,
        )
        if away_team_id is not None and kickoff is not None
        else {"index": None, "band": "UNAVAILABLE", "matches": 0}
    )

    probabilities = _probabilities(row)

    return {
        "brand": "MDRN SportsQ",
        "fixture": {
            "fixture_id": int(internal_fixture_id) if internal_fixture_id is not None else None,
            "provider_fixture_id": (
                int(provider_fixture_id) if provider_fixture_id is not None else None
            ),
            "home_team": str(home_name) if home_name is not None else None,
            "away_team": str(away_name) if away_name is not None else None,
            "kickoff_utc": (
                kickoff.isoformat()
                if isinstance(kickoff, datetime)
                else str(kickoff) if kickoff is not None else None
            ),
        },
        "sportsq_predict": {
            "name": BRAND_HIERARCHY["predict"],
            "prediction": prediction,
            "status": "AVAILABLE" if prediction is not None else "UNAVAILABLE",
            "source": prediction_source,
            "probabilities": probabilities,
        },
        "sportsq_score_call": {
            "name": BRAND_HIERARCHY["score_call"],
            "score": score_call,
            "status": (
                "AVAILABLE"
                if score_call is not None
                else "NOT_EXPOSED_BY_EXISTING_LOCK"
            ),
            "source": score_source,
        },
        "sportsq_confidence": {
            "name": BRAND_HIERARCHY["confidence"],
            "percent": confidence,
            "band": _confidence_band(confidence),
            "status": "AVAILABLE" if confidence is not None else "UNAVAILABLE",
            "source": confidence_source,
        },
        "sportsq_form_index": {
            "name": BRAND_HIERARCHY["form_index"],
            "window": FORM_WINDOW,
            "method": (
                "75% recent points + 25% recent goal-difference, "
                "using finished matches strictly before target kickoff"
            ),
            "home": {
                "team": str(home_name) if home_name is not None else None,
                **home_form,
            },
            "away": {
                "team": str(away_name) if away_name is not None else None,
                **away_form,
            },
            "lookahead_protection": True,
        },
        "sportsq_news_impact": {
            "name": BRAND_HIERARCHY["news_impact"],
            **_news_impact(session, _row_fixture_id(row)),
        },
        "prediction_lock": {
            "policy_version": row.get("policy_version"),
            "locked_at": (
                row.get("locked_at").isoformat()
                if isinstance(row.get("locked_at"), datetime)
                else row.get("locked_at")
            ),
            "publish": row.get("publish"),
            "immutable_source": True,
        },
        "safety": {
            "prediction_row_rewritten": False,
            "prediction_lock_rewritten": False,
            "historical_holdout_touched": False,
            "post_kickoff_features_used": False,
        },
    }

def list_sportsq_intelligence(
    session: Session,
    *,
    competition_id: int = 2,
    season: int = 2026,
    limit: int = 100,
) -> dict[str, Any]:
    safe_limit = max(1, min(int(limit), 1000))
    rows, source = _existing_prediction_rows(
        session,
        competition_id=int(competition_id),
        season=int(season),
        limit=safe_limit,
    )
    items = [_intelligence_item(session, row) for row in rows[:safe_limit]]
    return {
        "status": "success",
        "brand_hierarchy": BRAND_HIERARCHY,
        "competition_id": int(competition_id),
        "season": int(season),
        "count": len(items),
        "source": source,
        "items": items,
        "prediction_engine_rewritten": False,
        "existing_prediction_locks_immutable": True,
        "historical_holdout_touched": False,
    }

def get_sportsq_intelligence(
    session: Session,
    *,
    fixture_id: int,
    competition_id: int = 2,
    season: int = 2026,
) -> dict[str, Any]:
    fixture = _fixture(session, int(fixture_id))
    internal_id = (
        int(fixture["id"])
        if fixture and fixture.get("id") is not None
        else int(fixture_id)
    )

    if "live_predictions" not in _table_names(session):
        return {
            "status": "not_found",
            "fixture_id": int(fixture_id),
            "historical_holdout_touched": False,
        }

    row = session.execute(
        text(
            'SELECT * FROM "live_predictions" '
            "WHERE fixture_id = :fixture_id ORDER BY id DESC LIMIT 1"
        ),
        {"fixture_id": internal_id},
    ).mappings().first()

    if row is None:
        return {
            "status": "not_found",
            "fixture_id": int(fixture_id),
            "historical_holdout_touched": False,
        }

    return {
        "status": "success",
        "item": _intelligence_item(session, dict(row)),
        "prediction_engine_rewritten": False,
        "historical_holdout_touched": False,
    }

def sportsq_accuracy_summary(
    session: Session,
    *,
    competition_id: int = 2,
    season: int = 2026,
) -> dict[str, Any]:
    try:
        existing = _invoke_existing(
            "prediction_grading",
            "performance_summary",
            session,
            competition_id=int(competition_id),
            season=int(season),
        )
        status = "AVAILABLE"
    except Exception as exc:
        existing = {"error_type": type(exc).__name__}
        status = "UNAVAILABLE"

    return {
        "status": status,
        "brand": "MDRN SportsQ",
        "competition_id": int(competition_id),
        "season": int(season),
        "grading_source": "existing_prediction_grading",
        "performance": existing,
        "calibration_policy": (
            "Existing locked predictions are graded against completed results. "
            "No prediction is rewritten after kickoff."
        ),
        "prediction_engine_rewritten": False,
        "historical_holdout_touched": False,
    }

def sportsq_capabilities(session: Session) -> dict[str, Any]:
    tables = _table_names(session)
    live_columns = (
        _columns(session, "live_predictions")
        if "live_predictions" in tables
        else set()
    )

    module_status = [_module_status(name) for name in CORE_INTELLIGENCE_MODULES]
    available_modules = sum(
        1 for row in module_status if row["status"] == "AVAILABLE"
    )

    score_call_columns = any(
        field in live_columns
        for pair in SCORE_PAIR_FIELDS
        for field in pair
    ) or any(field in live_columns for field in SCORE_STRING_FIELDS)

    probability_columns = (
        any(field in live_columns for field in HOME_PROBABILITY_FIELDS)
        and any(field in live_columns for field in DRAW_PROBABILITY_FIELDS)
        and any(field in live_columns for field in AWAY_PROBABILITY_FIELDS)
    )
    prediction_columns = any(field in live_columns for field in PREDICTION_FIELDS)
    confidence_columns = any(field in live_columns for field in CONFIDENCE_FIELDS)

    live_prediction_count = 0
    if "live_predictions" in tables:
        live_prediction_count = int(
            session.execute(
                text('SELECT COUNT(*) FROM "live_predictions"')
            ).scalar()
            or 0
        )

    fixture_count = 0
    if "fixtures" in tables:
        fixture_count = int(
            session.execute(text('SELECT COUNT(*) FROM "fixtures"')).scalar()
            or 0
        )

    return {
        "status": "success",
        "brand_hierarchy": BRAND_HIERARCHY,
        "modules": module_status,
        "available_modules": available_modules,
        "expected_modules": len(CORE_INTELLIGENCE_MODULES),
        "data": {
            "fixtures": fixture_count,
            "live_predictions": live_prediction_count,
        },
        "sportsq_predict": {
            "available": prediction_columns or probability_columns,
            "uses_existing_locked_prediction": True,
        },
        "sportsq_score_call": {
            "available": score_call_columns,
            "fabricated_when_missing": False,
        },
        "sportsq_confidence": {
            "available": confidence_columns or probability_columns,
            "uses_locked_values_only": True,
        },
        "sportsq_form_index": {
            "available": "fixtures" in tables,
            "window": FORM_WINDOW,
            "lookahead_protection": True,
        },
        "sportsq_news_impact": {
            "status": "NO_STRUCTURED_TEAM_NEWS_INPUT",
            "fabricated": False,
        },
        "accuracy": {
            "grading_module": "prediction_grading",
        },
        "safety": {
            "prediction_engine_rewritten": False,
            "live_prediction_rows_modified": False,
            "existing_prediction_locks_immutable": True,
            "historical_holdout_touched": False,
        },
    }
