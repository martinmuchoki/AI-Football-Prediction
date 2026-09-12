from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


class LiveScoreValidationError(ValueError):
    pass


@dataclass(frozen=True)
class LiveScoreNormalized:
    provider_fixture_id: int
    competition_id: int
    competition_name: str
    country_name: str | None
    season: int

    home_team_id: int
    home_team_name: str
    home_team_logo: str | None
    away_team_id: int
    away_team_name: str
    away_team_logo: str | None

    kickoff_utc: datetime
    round_name: str | None
    venue_name: str | None
    status_short: str
    status_long: str | None

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
    try:
        result = int(value)
    except (TypeError, ValueError) as exc:
        raise LiveScoreValidationError(f"{name} must be an integer") from exc
    if result <= 0:
        raise LiveScoreValidationError(f"{name} must be positive")
    return result


def _parse_score(value: Any) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    text = str(value).strip()
    if not text:
        return None, None
    # Handles "2 - 1", "2-1" and similar.
    for sep in (" - ", "-", ":"):
        if sep in text:
            left, right = text.split(sep, 1)
            try:
                h, a = int(left.strip()), int(right.strip())
            except ValueError:
                return None, None
            if h < 0 or a < 0:
                raise LiveScoreValidationError("score cannot be negative")
            return h, a
    return None, None


def _kickoff(date_value: Any, time_value: Any) -> datetime:
    date_text = str(date_value or "").strip()
    time_text = str(time_value or "").strip()
    if not date_text or not time_text:
        raise LiveScoreValidationError("fixture date/time missing")
    if len(time_text.split(":")) == 2:
        time_text += ":00"
    try:
        dt = datetime.fromisoformat(f"{date_text}T{time_text}")
    except ValueError as exc:
        raise LiveScoreValidationError("fixture date/time invalid") from exc
    # Live Score API documents fixture/history times as UTC.
    return dt.replace(tzinfo=timezone.utc)


def _result(home: int | None, away: int | None) -> str | None:
    if home is None or away is None:
        return None
    if home > away:
        return "HOME"
    if away > home:
        return "AWAY"
    return "DRAW"


def normalize_live_score_fixture(item: dict[str, Any], *, season: int) -> LiveScoreNormalized:
    if not isinstance(item, dict):
        raise LiveScoreValidationError("fixture item must be an object")

    competition = item.get("competition") or {}
    home = item.get("home") or {}
    away = item.get("away") or {}
    country = item.get("country") or {}

    fixture_id = _positive_int(item.get("id"), "fixture.id")
    competition_id = _positive_int(competition.get("id"), "competition.id")
    home_id = _positive_int(home.get("id"), "home.id")
    away_id = _positive_int(away.get("id"), "away.id")
    if home_id == away_id:
        raise LiveScoreValidationError("home and away team cannot be the same")

    competition_name = str(competition.get("name") or "").strip()
    home_name = str(home.get("name") or "").strip()
    away_name = str(away.get("name") or "").strip()
    if not competition_name or not home_name or not away_name:
        raise LiveScoreValidationError("competition/home/away names are required")

    return LiveScoreNormalized(
        provider_fixture_id=fixture_id,
        competition_id=competition_id,
        competition_name=competition_name,
        country_name=str(country.get("name") or "").strip() or None,
        season=int(season),
        home_team_id=home_id,
        home_team_name=home_name,
        home_team_logo=home.get("logo"),
        away_team_id=away_id,
        away_team_name=away_name,
        away_team_logo=away.get("logo"),
        kickoff_utc=_kickoff(item.get("date"), item.get("time")),
        round_name=str(item.get("round") or "").strip() or None,
        venue_name=str(item.get("location") or "").strip() or None,
        status_short="NS",
        status_long="Not Started",
        home_goals=None,
        away_goals=None,
        halftime_home=None,
        halftime_away=None,
        fulltime_home=None,
        fulltime_away=None,
        extratime_home=None,
        extratime_away=None,
        penalty_home=None,
        penalty_away=None,
        is_finished=False,
        result_1x2=None,
    )


def normalize_live_score_history(item: dict[str, Any], *, season: int) -> LiveScoreNormalized:
    if not isinstance(item, dict):
        raise LiveScoreValidationError("history item must be an object")

    competition = item.get("competition") or {}
    home = item.get("home") or {}
    away = item.get("away") or {}
    country = item.get("country") or {}
    scores = item.get("scores") or {}

    match_id = _positive_int(item.get("id"), "match.id")
    raw_fixture_id = item.get("fixture_id")
    try:
        fixture_id = int(raw_fixture_id or 0)
    except (TypeError, ValueError):
        fixture_id = 0
    # If the provider did not create a prior fixture, keep the match record distinct
    # by using the negative match ID as the external fixture key.
    provider_fixture_id = fixture_id if fixture_id > 0 else -match_id

    competition_id = _positive_int(competition.get("id"), "competition.id")
    home_id = _positive_int(home.get("id"), "home.id")
    away_id = _positive_int(away.get("id"), "away.id")
    if home_id == away_id:
        raise LiveScoreValidationError("home and away team cannot be the same")

    competition_name = str(competition.get("name") or "").strip()
    home_name = str(home.get("name") or "").strip()
    away_name = str(away.get("name") or "").strip()
    if not competition_name or not home_name or not away_name:
        raise LiveScoreValidationError("competition/home/away names are required")

    overall_h, overall_a = _parse_score(scores.get("score"))
    ht_h, ht_a = _parse_score(scores.get("ht_score"))
    ft_h, ft_a = _parse_score(scores.get("ft_score"))
    et_h, et_a = _parse_score(scores.get("et_score"))
    ps_h, ps_a = _parse_score(scores.get("ps_score"))

    if overall_h is None or overall_a is None:
        overall_h, overall_a = ft_h, ft_a

    status = str(item.get("status") or "").upper().strip()
    time_code = str(item.get("time") or "").upper().strip()
    finished = status == "FINISHED" or time_code in {"FT", "AET", "AP"}
    if finished and (overall_h is None or overall_a is None):
        raise LiveScoreValidationError("finished match is missing score")

    if time_code == "AET":
        short = "AET"
    elif time_code == "AP":
        short = "PEN"
    elif finished:
        short = "FT"
    else:
        short = "LIVE"

    # 1X2 is based on regulation/full-time score where available.
    result_home, result_away = (ft_h, ft_a) if ft_h is not None and ft_a is not None else (overall_h, overall_a)

    return LiveScoreNormalized(
        provider_fixture_id=provider_fixture_id,
        competition_id=competition_id,
        competition_name=competition_name,
        country_name=str(country.get("name") or "").strip() or None,
        season=int(season),
        home_team_id=home_id,
        home_team_name=home_name,
        home_team_logo=home.get("logo"),
        away_team_id=away_id,
        away_team_name=away_name,
        away_team_logo=away.get("logo"),
        kickoff_utc=_kickoff(item.get("date"), item.get("scheduled")),
        round_name=str(item.get("round") or "").strip() or None,
        venue_name=str(item.get("location") or "").strip() or None,
        status_short=short,
        status_long=status.title() if status else None,
        home_goals=overall_h,
        away_goals=overall_a,
        halftime_home=ht_h,
        halftime_away=ht_a,
        fulltime_home=ft_h,
        fulltime_away=ft_a,
        extratime_home=et_h,
        extratime_away=et_a,
        penalty_home=ps_h,
        penalty_away=ps_a,
        is_finished=finished,
        result_1x2=_result(result_home, result_away) if finished else None,
    )
