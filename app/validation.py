from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


FINISHED_STATUSES = {"FT", "AET", "PEN"}
CANCELLED_STATUSES = {"CANC", "ABD", "AWD", "WO"}


class FixtureValidationError(ValueError):
    pass


@dataclass(frozen=True)
class NormalizedFixture:
    provider_fixture_id: int

    league_id: int
    league_name: str
    league_country: str | None
    league_type: str | None
    league_logo: str | None
    league_flag: str | None
    season: int

    home_team_id: int
    home_team_name: str
    home_team_logo: str | None
    away_team_id: int
    away_team_name: str
    away_team_logo: str | None

    kickoff_utc: datetime
    timezone_name: str | None
    round_name: str | None
    referee: str | None
    venue_id: int | None
    venue_name: str | None
    venue_city: str | None

    status_short: str
    status_long: str | None
    elapsed: int | None

    home_goals: int | None
    away_goals: int | None
    halftime_home: int | None
    halftime_away: int | None
    fulltime_home: int | None
    fulltime_away: int | None
    extratime_home: int | None
    extratime_away: int | None
    penalty_home: int | None
    penalty_away: int | None

    is_finished: bool
    result_1x2: str | None


def _positive_int(value: Any, name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise FixtureValidationError(f"{name} must be a positive integer")
    return value


def _score(value: Any, name: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise FixtureValidationError(f"{name} must be a non-negative integer or null")
    return value


def _parse_date(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        raise FixtureValidationError("fixture.date is missing")
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise FixtureValidationError("fixture.date is not valid ISO-8601") from exc
    if dt.tzinfo is None:
        raise FixtureValidationError("fixture.date must include a timezone offset")
    return dt.astimezone(timezone.utc)


def _nested_score(score_obj: dict, part: str, side: str) -> int | None:
    block = score_obj.get(part) or {}
    return _score(block.get(side), f"score.{part}.{side}")


def normalize_fixture(item: dict[str, Any]) -> NormalizedFixture:
    if not isinstance(item, dict):
        raise FixtureValidationError("fixture payload item must be an object")

    fixture = item.get("fixture") or {}
    league = item.get("league") or {}
    teams = item.get("teams") or {}
    goals = item.get("goals") or {}
    score_obj = item.get("score") or {}

    fixture_id = _positive_int(fixture.get("id"), "fixture.id")
    league_id = _positive_int(league.get("id"), "league.id")
    season = league.get("season")
    if isinstance(season, bool) or not isinstance(season, int) or season < 1900 or season > 2200:
        raise FixtureValidationError("league.season is invalid")

    home = teams.get("home") or {}
    away = teams.get("away") or {}
    home_id = _positive_int(home.get("id"), "teams.home.id")
    away_id = _positive_int(away.get("id"), "teams.away.id")
    if home_id == away_id:
        raise FixtureValidationError("home and away team IDs cannot be equal")

    home_name = str(home.get("name") or "").strip()
    away_name = str(away.get("name") or "").strip()
    league_name = str(league.get("name") or "").strip()
    if not home_name or not away_name or not league_name:
        raise FixtureValidationError("league/team names cannot be empty")

    status = fixture.get("status") or {}
    status_short = str(status.get("short") or "").strip().upper()
    if not status_short:
        raise FixtureValidationError("fixture.status.short is missing")

    kickoff_utc = _parse_date(fixture.get("date"))

    home_goals = _score(goals.get("home"), "goals.home")
    away_goals = _score(goals.get("away"), "goals.away")

    is_finished = status_short in FINISHED_STATUSES
    result_1x2 = None
    if is_finished:
        if home_goals is None or away_goals is None:
            raise FixtureValidationError("finished fixture must contain final goals")
        result_1x2 = "HOME" if home_goals > away_goals else "AWAY" if away_goals > home_goals else "DRAW"

    venue = fixture.get("venue") or {}

    return NormalizedFixture(
        provider_fixture_id=fixture_id,
        league_id=league_id,
        league_name=league_name,
        league_country=(str(league.get("country")).strip() if league.get("country") is not None else None),
        league_type=(str(league.get("type")).strip() if league.get("type") is not None else None),
        league_logo=league.get("logo"),
        league_flag=league.get("flag"),
        season=season,
        home_team_id=home_id,
        home_team_name=home_name,
        home_team_logo=home.get("logo"),
        away_team_id=away_id,
        away_team_name=away_name,
        away_team_logo=away.get("logo"),
        kickoff_utc=kickoff_utc,
        timezone_name=fixture.get("timezone"),
        round_name=league.get("round"),
        referee=fixture.get("referee"),
        venue_id=venue.get("id"),
        venue_name=venue.get("name"),
        venue_city=venue.get("city"),
        status_short=status_short,
        status_long=(str(status.get("long")).strip() if status.get("long") is not None else None),
        elapsed=status.get("elapsed"),
        home_goals=home_goals,
        away_goals=away_goals,
        halftime_home=_nested_score(score_obj, "halftime", "home"),
        halftime_away=_nested_score(score_obj, "halftime", "away"),
        fulltime_home=_nested_score(score_obj, "fulltime", "home"),
        fulltime_away=_nested_score(score_obj, "fulltime", "away"),
        extratime_home=_nested_score(score_obj, "extratime", "home"),
        extratime_away=_nested_score(score_obj, "extratime", "away"),
        penalty_home=_nested_score(score_obj, "penalty", "home"),
        penalty_away=_nested_score(score_obj, "penalty", "away"),
        is_finished=is_finished,
        result_1x2=result_1x2,
    )
