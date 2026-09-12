from __future__ import annotations

import csv
import json
import math
from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Fixture, LeagueSeason, Team


HISTORY_PROVIDER = "football-data-csv"
WINDOWS = (3, 5, 10)
ELO_INITIAL = 1500.0
ELO_K = 20.0
ELO_HOME_ADVANTAGE = 65.0


@dataclass(frozen=True)
class MatchRecord:
    fixture: Fixture
    season: int
    home_name: str
    away_name: str
    raw: dict[str, Any]


def _float(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        number = float(text)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return number


def _safe_json(value: str) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _normalized_probs(odds: Iterable[float | None]) -> tuple[float | None, ...]:
    values = list(odds)
    if not values or any(v is None or v <= 1.0 for v in values):
        return tuple(None for _ in values) + (None,)
    implied = [1.0 / float(v) for v in values]
    overround = sum(implied)
    if overround <= 0:
        return tuple(None for _ in values) + (None,)
    fair = [x / overround for x in implied]
    return (*fair, overround)


def _mean(values: Iterable[float | None]) -> float | None:
    clean = [float(v) for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


def _history_summary(events: list[dict[str, float | None]], window: int) -> dict[str, float]:
    sample = events[-window:]
    matches = len(sample)

    def avg(key: str) -> float:
        value = _mean(item.get(key) for item in sample)
        return round(value, 6) if value is not None else 0.0

    return {
        "matches": float(matches),
        "points_pg": avg("points"),
        "win_rate": avg("win"),
        "draw_rate": avg("draw"),
        "loss_rate": avg("loss"),
        "goals_for_pg": avg("gf"),
        "goals_against_pg": avg("ga"),
        "goal_diff_pg": round(avg("gf") - avg("ga"), 6),
        "shots_for_pg": avg("shots_for"),
        "shots_against_pg": avg("shots_against"),
        "sot_for_pg": avg("sot_for"),
        "sot_against_pg": avg("sot_against"),
        "corners_for_pg": avg("corners_for"),
        "corners_against_pg": avg("corners_against"),
        "fouls_pg": avg("fouls_for"),
        "yellow_pg": avg("yellow_for"),
        "red_pg": avg("red_for"),
    }


def _team_event(raw: dict[str, Any], *, is_home: bool, gf: int, ga: int) -> dict[str, float | None]:
    if gf > ga:
        points, win, draw, loss = 3.0, 1.0, 0.0, 0.0
    elif gf == ga:
        points, win, draw, loss = 1.0, 0.0, 1.0, 0.0
    else:
        points, win, draw, loss = 0.0, 0.0, 0.0, 1.0

    if is_home:
        own = {"shots": "HS", "sot": "HST", "fouls": "HF", "corners": "HC", "yellow": "HY", "red": "HR"}
        opp = {"shots": "AS", "sot": "AST", "fouls": "AF", "corners": "AC", "yellow": "AY", "red": "AR"}
    else:
        own = {"shots": "AS", "sot": "AST", "fouls": "AF", "corners": "AC", "yellow": "AY", "red": "AR"}
        opp = {"shots": "HS", "sot": "HST", "fouls": "HF", "corners": "HC", "yellow": "HY", "red": "HR"}

    return {
        "points": points,
        "win": win,
        "draw": draw,
        "loss": loss,
        "gf": float(gf),
        "ga": float(ga),
        "shots_for": _float(raw.get(own["shots"])),
        "shots_against": _float(raw.get(opp["shots"])),
        "sot_for": _float(raw.get(own["sot"])),
        "sot_against": _float(raw.get(opp["sot"])),
        "fouls_for": _float(raw.get(own["fouls"])),
        "fouls_against": _float(raw.get(opp["fouls"])),
        "corners_for": _float(raw.get(own["corners"])),
        "corners_against": _float(raw.get(opp["corners"])),
        "yellow_for": _float(raw.get(own["yellow"])),
        "yellow_against": _float(raw.get(opp["yellow"])),
        "red_for": _float(raw.get(own["red"])),
        "red_against": _float(raw.get(opp["red"])),
    }


def _days_since(previous: datetime | None, current: datetime) -> float | None:
    if previous is None:
        return None
    seconds = (current - previous).total_seconds()
    return round(max(seconds, 0.0) / 86400.0, 6)


def _elo_expected(home_elo: float, away_elo: float) -> float:
    adjusted_home = home_elo + ELO_HOME_ADVANTAGE
    return 1.0 / (1.0 + 10.0 ** ((away_elo - adjusted_home) / 400.0))


def _fixture_outcome_score(fixture: Fixture) -> float:
    if fixture.home_goals is None or fixture.away_goals is None:
        raise ValueError("Finished fixture is missing full-time goals")
    if fixture.home_goals > fixture.away_goals:
        return 1.0
    if fixture.home_goals < fixture.away_goals:
        return 0.0
    return 0.5


def _target_code(result: str | None, home_goals: int, away_goals: int) -> tuple[str, int]:
    normalized = (result or "").upper()
    if normalized == "HOME" or home_goals > away_goals:
        return "H", 0
    if normalized == "AWAY" or home_goals < away_goals:
        return "A", 2
    return "D", 1


def load_match_records(
    session: Session,
    *,
    provider: str = HISTORY_PROVIDER,
    from_season: int,
    to_season: int,
) -> list[MatchRecord]:
    Home = aliased(Team)
    Away = aliased(Team)
    stmt = (
        select(Fixture, LeagueSeason.season, Home.name, Away.name)
        .join(LeagueSeason, Fixture.league_season_id == LeagueSeason.id)
        .join(Home, Fixture.home_team_id == Home.id)
        .join(Away, Fixture.away_team_id == Away.id)
        .where(
            Fixture.provider == provider,
            LeagueSeason.provider == provider,
            LeagueSeason.season >= from_season,
            LeagueSeason.season <= to_season,
            Fixture.is_finished.is_(True),
        )
        .order_by(LeagueSeason.season, Fixture.kickoff_utc, Fixture.id)
    )
    records: list[MatchRecord] = []
    for fixture, season, home_name, away_name in session.execute(stmt).all():
        records.append(
            MatchRecord(
                fixture=fixture,
                season=int(season),
                home_name=str(home_name),
                away_name=str(away_name),
                raw=_safe_json(fixture.raw_json),
            )
        )
    return records


def _add_market_features(row: dict[str, Any], raw: dict[str, Any]) -> None:
    # Stable 1X2 columns present in every audited EPL season (2022/23-2025/26).
    market_columns = {
        "avg_open": ("AvgH", "AvgD", "AvgA"),
        "b365_open": ("B365H", "B365D", "B365A"),
        "ps_open": ("PSH", "PSD", "PSA"),
        "avg_close": ("AvgCH", "AvgCD", "AvgCA"),
        "b365_close": ("B365CH", "B365CD", "B365CA"),
        "ps_close": ("PSCH", "PSCD", "PSCA"),
    }
    for prefix, keys in market_columns.items():
        h, d, a = (_float(raw.get(key)) for key in keys)
        row[f"market_{prefix}_home_odds"] = h
        row[f"market_{prefix}_draw_odds"] = d
        row[f"market_{prefix}_away_odds"] = a
        ph, pd, pa, overround = _normalized_probs((h, d, a))
        row[f"market_{prefix}_home_prob"] = ph
        row[f"market_{prefix}_draw_prob"] = pd
        row[f"market_{prefix}_away_prob"] = pa
        row[f"market_{prefix}_overround"] = overround

    open_home = row.get("market_avg_open_home_prob")
    open_draw = row.get("market_avg_open_draw_prob")
    open_away = row.get("market_avg_open_away_prob")
    close_home = row.get("market_avg_close_home_prob")
    close_draw = row.get("market_avg_close_draw_prob")
    close_away = row.get("market_avg_close_away_prob")
    row["market_home_prob_move"] = None if open_home is None or close_home is None else round(close_home - open_home, 8)
    row["market_draw_prob_move"] = None if open_draw is None or close_draw is None else round(close_draw - open_draw, 8)
    row["market_away_prob_move"] = None if open_away is None or close_away is None else round(close_away - open_away, 8)

    # Stable totals market.
    over_open = _float(raw.get("Avg>2.5"))
    under_open = _float(raw.get("Avg<2.5"))
    over_close = _float(raw.get("AvgC>2.5"))
    under_close = _float(raw.get("AvgC<2.5"))
    row["market_avg_open_over25_odds"] = over_open
    row["market_avg_open_under25_odds"] = under_open
    p_over, p_under, total_overround = _normalized_probs((over_open, under_open))
    row["market_avg_open_over25_prob"] = p_over
    row["market_avg_open_under25_prob"] = p_under
    row["market_avg_open_total_overround"] = total_overround
    row["market_avg_close_over25_odds"] = over_close
    row["market_avg_close_under25_odds"] = under_close
    p_over_c, p_under_c, total_overround_c = _normalized_probs((over_close, under_close))
    row["market_avg_close_over25_prob"] = p_over_c
    row["market_avg_close_under25_prob"] = p_under_c
    row["market_avg_close_total_overround"] = total_overround_c

    # Stable Asian handicap columns.
    row["market_ah_open_line"] = _float(raw.get("AHh"))
    row["market_ah_close_line"] = _float(raw.get("AHCh"))
    ah_home = _float(raw.get("AvgAHH"))
    ah_away = _float(raw.get("AvgAHA"))
    ah_home_c = _float(raw.get("AvgCAHH"))
    ah_away_c = _float(raw.get("AvgCAHA"))
    pah, paa, ah_overround = _normalized_probs((ah_home, ah_away))
    pahc, paac, ah_overround_c = _normalized_probs((ah_home_c, ah_away_c))
    row["market_ah_open_home_prob"] = pah
    row["market_ah_open_away_prob"] = paa
    row["market_ah_open_overround"] = ah_overround
    row["market_ah_close_home_prob"] = pahc
    row["market_ah_close_away_prob"] = paac
    row["market_ah_close_overround"] = ah_overround_c


def build_feature_rows(records: list[MatchRecord]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    current_season: int | None = None

    overall: dict[int, deque[dict[str, float | None]]] = defaultdict(lambda: deque(maxlen=max(WINDOWS)))
    venue: dict[tuple[int, str], deque[dict[str, float | None]]] = defaultdict(lambda: deque(maxlen=5))
    last_played: dict[int, datetime] = {}
    elo: dict[int, float] = defaultdict(lambda: ELO_INITIAL)
    matches_played: dict[int, int] = defaultdict(int)

    for record in records:
        fixture = record.fixture
        if fixture.home_goals is None or fixture.away_goals is None:
            continue

        if current_season != record.season:
            current_season = record.season
            overall = defaultdict(lambda: deque(maxlen=max(WINDOWS)))
            venue = defaultdict(lambda: deque(maxlen=5))
            last_played = {}
            elo = defaultdict(lambda: ELO_INITIAL)
            matches_played = defaultdict(int)

        home_id = fixture.home_team_id
        away_id = fixture.away_team_id
        home_hist = list(overall[home_id])
        away_hist = list(overall[away_id])
        home_venue_hist = list(venue[(home_id, "home")])
        away_venue_hist = list(venue[(away_id, "away")])

        home_elo = float(elo[home_id])
        away_elo = float(elo[away_id])
        home_expected = _elo_expected(home_elo, away_elo)
        home_mp = int(matches_played[home_id])
        away_mp = int(matches_played[away_id])

        target_result, target_class = _target_code(
            fixture.result_1x2,
            fixture.home_goals,
            fixture.away_goals,
        )

        row: dict[str, Any] = {
            "fixture_id": fixture.id,
            "provider_fixture_id": fixture.provider_fixture_id,
            "season": record.season,
            "kickoff_utc": fixture.kickoff_utc.isoformat(),
            "home_team": record.home_name,
            "away_team": record.away_name,
            "home_matches_played": home_mp,
            "away_matches_played": away_mp,
            "season_progress": round(min((home_mp + away_mp) / 76.0, 1.0), 6),
            "home_rest_days": _days_since(last_played.get(home_id), fixture.kickoff_utc),
            "away_rest_days": _days_since(last_played.get(away_id), fixture.kickoff_utc),
            "home_elo_pre": round(home_elo, 6),
            "away_elo_pre": round(away_elo, 6),
            "elo_diff_pre": round(home_elo - away_elo, 6),
            "elo_home_expected": round(home_expected, 8),
        }

        for window in WINDOWS:
            hs = _history_summary(home_hist, window)
            aw = _history_summary(away_hist, window)
            for key, value in hs.items():
                row[f"home_form{window}_{key}"] = value
            for key, value in aw.items():
                row[f"away_form{window}_{key}"] = value
            for key in (
                "points_pg",
                "win_rate",
                "goals_for_pg",
                "goals_against_pg",
                "goal_diff_pg",
                "shots_for_pg",
                "sot_for_pg",
                "corners_for_pg",
                "yellow_pg",
            ):
                row[f"form{window}_{key}_diff"] = round(hs[key] - aw[key], 6)

        hv = _history_summary(home_venue_hist, 5)
        av = _history_summary(away_venue_hist, 5)
        for key in ("matches", "points_pg", "goals_for_pg", "goals_against_pg", "goal_diff_pg", "shots_for_pg", "sot_for_pg"):
            row[f"home_home5_{key}"] = hv[key]
            row[f"away_away5_{key}"] = av[key]
        row["venue5_points_pg_diff"] = round(hv["points_pg"] - av["points_pg"], 6)
        row["venue5_goal_diff_pg_diff"] = round(hv["goal_diff_pg"] - av["goal_diff_pg"], 6)

        _add_market_features(row, record.raw)

        # Targets are intentionally appended after all pre-match features.
        row["target_result"] = target_result
        row["target_class"] = target_class
        row["target_home_goals"] = fixture.home_goals
        row["target_away_goals"] = fixture.away_goals
        rows.append(row)

        # CRITICAL leakage barrier: update all team state only after the row is emitted.
        home_event = _team_event(
            record.raw,
            is_home=True,
            gf=fixture.home_goals,
            ga=fixture.away_goals,
        )
        away_event = _team_event(
            record.raw,
            is_home=False,
            gf=fixture.away_goals,
            ga=fixture.home_goals,
        )
        overall[home_id].append(home_event)
        overall[away_id].append(away_event)
        venue[(home_id, "home")].append(home_event)
        venue[(away_id, "away")].append(away_event)
        last_played[home_id] = fixture.kickoff_utc
        last_played[away_id] = fixture.kickoff_utc
        matches_played[home_id] += 1
        matches_played[away_id] += 1

        actual = _fixture_outcome_score(fixture)
        delta = ELO_K * (actual - home_expected)
        elo[home_id] = home_elo + delta
        elo[away_id] = away_elo - delta

    return rows


def build_feature_dataset(
    session: Session,
    *,
    output_path: str,
    provider: str = HISTORY_PROVIDER,
    from_season: int,
    to_season: int,
) -> dict[str, Any]:
    records = load_match_records(
        session,
        provider=provider,
        from_season=from_season,
        to_season=to_season,
    )
    rows = build_feature_rows(records)
    if not rows:
        raise ValueError("No finished matches found for requested provider/season range")

    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    columns = list(rows[0].keys())
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    target_columns = ["target_result", "target_class", "target_home_goals", "target_away_goals"]
    id_columns = ["fixture_id", "provider_fixture_id", "season", "kickoff_utc", "home_team", "away_team"]
    feature_columns = [c for c in columns if c not in target_columns and c not in id_columns]
    metadata = {
        "version": "0.2.0-phase2-feature-engine",
        "provider": provider,
        "from_season": from_season,
        "to_season": to_season,
        "rows": len(rows),
        "columns": len(columns),
        "feature_columns": len(feature_columns),
        "id_columns": id_columns,
        "target_columns": target_columns,
        "leakage_policy": (
            "Rolling team state and Elo are read before each fixture and updated only after the fixture row is emitted. "
            "Current-match shots/cards/corners/goals are never used as predictors for that same match. "
            "Market odds fields are pre-match bookmaker data."
        ),
        "season_reset_policy": "Team rolling state and Elo reset at each EPL season boundary.",
        "elo": {
            "initial": ELO_INITIAL,
            "k": ELO_K,
            "home_advantage": ELO_HOME_ADVANTAGE,
        },
        "windows": list(WINDOWS),
        "output": str(path),
    }
    meta_path = path.with_suffix(path.suffix + ".meta.json")
    meta_path.write_text(json.dumps(metadata, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "status": "success",
        "provider": provider,
        "from_season": from_season,
        "to_season": to_season,
        "records_loaded": len(records),
        "rows_written": len(rows),
        "columns": len(columns),
        "feature_columns": len(feature_columns),
        "output": str(path),
        "metadata": str(meta_path),
    }
