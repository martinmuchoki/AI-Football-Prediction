from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fixture, LeagueSeason
from app.providers.api_football import ApiFootballClient


PROVIDER = "api-football"
MARKET_SOURCE = "api-football.match-winner"

# Deterministic source selection.
# 1. Bet365
# 2. William Hill
# 3. First other bookmaker with valid Match Winner 1X2
BOOKMAKER_PRIORITY = (8, 7)


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _valid_decimal(value: Any) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None

    if number <= 1.0:
        return None

    return number


def extract_api_football_match_winner(
    rows: list[dict[str, Any]],
) -> dict[str, Any] | None:
    candidates: list[dict[str, Any]] = []

    for row in rows:
        for bookmaker in row.get("bookmakers") or []:
            bookmaker_id = bookmaker.get("id")

            for bet in bookmaker.get("bets") or []:
                try:
                    bet_id = int(bet.get("id"))
                except (TypeError, ValueError):
                    continue

                if bet_id != 1:
                    continue

                values = {
                    str(value.get("value")):
                    value.get("odd")
                    for value in bet.get("values") or []
                }

                home = _valid_decimal(values.get("Home"))
                draw = _valid_decimal(values.get("Draw"))
                away = _valid_decimal(values.get("Away"))

                if (
                    home is None
                    or draw is None
                    or away is None
                ):
                    continue

                candidates.append({
                    "bookmaker_id": bookmaker_id,
                    "bookmaker_name": bookmaker.get("name"),
                    "home": home,
                    "draw": draw,
                    "away": away,
                    "update": row.get("update"),
                })

    if not candidates:
        return None

    def rank(item: dict[str, Any]) -> tuple[int, int]:
        try:
            bookmaker_id = int(item.get("bookmaker_id"))
        except (TypeError, ValueError):
            bookmaker_id = -1

        if bookmaker_id in BOOKMAKER_PRIORITY:
            return (
                BOOKMAKER_PRIORITY.index(bookmaker_id),
                bookmaker_id,
            )

        return (999, bookmaker_id)

    candidates.sort(key=rank)

    return candidates[0]


async def refresh_api_football_odds(
    session: Session,
    client: ApiFootballClient,
    *,
    competition_id: int,
    season: int,
    now: datetime | None = None,
    lookahead_days: int = 14,
    quota_floor: int = 100,
) -> dict[str, Any]:
    """Attach normalized API-Football Match Winner odds to canonical fixtures.

    No synthetic prices are ever generated. A fixture remains without odds
    when the provider does not supply a valid H/D/A market.
    """
    current = _utc(now)
    horizon = current + timedelta(
        days=max(1, int(lookahead_days))
    )

    stmt = (
        select(Fixture)
        .join(
            LeagueSeason,
            Fixture.league_season_id == LeagueSeason.id,
        )
        .where(
            Fixture.provider == PROVIDER,
            LeagueSeason.provider == PROVIDER,
            LeagueSeason.provider_league_id
                == int(competition_id),
            LeagueSeason.season == int(season),
            Fixture.status_short == "NS",
            Fixture.is_finished.is_(False),
            Fixture.kickoff_utc > current,
            Fixture.kickoff_utc <= horizon,
        )
        .order_by(
            Fixture.kickoff_utc.asc(),
            Fixture.id.asc(),
        )
    )

    fixtures = list(session.scalars(stmt).all())

    summary: dict[str, Any] = {
        "status": "success",
        "provider": PROVIDER,
        "market_source": MARKET_SOURCE,
        "competition_id": int(competition_id),
        "season": int(season),
        "candidates": len(fixtures),
        "valid": 0,
        "missing": 0,
        "errors": 0,
        "updated": 0,
        "quota_stop": False,
        "requests_remaining": None,
        "final_holdout_touched": False,
    }

    for fixture in fixtures:
        try:
            response = await client.get_odds(
                fixture=int(fixture.provider_fixture_id),
                bet=1,
            )

            summary["requests_remaining"] = (
                response.requests_remaining
            )

            if (
                response.requests_remaining is not None
                and response.requests_remaining
                    < int(quota_floor)
            ):
                summary["quota_stop"] = True
                summary["status"] = "partial"
                break

            market = extract_api_football_match_winner(
                response.data
            )

            if market is None:
                summary["missing"] += 1
                continue

            try:
                raw = json.loads(fixture.raw_json or "{}")
            except (TypeError, json.JSONDecodeError):
                raw = {}

            if not isinstance(raw, dict):
                raw = {}

            odds = raw.get("odds")
            if not isinstance(odds, dict):
                odds = {}

            odds["pre"] = {
                "1": market["home"],
                "X": market["draw"],
                "2": market["away"],
            }

            odds["source"] = {
                "provider": PROVIDER,
                "market": "Match Winner",
                "bet_id": 1,
                "bookmaker_id": market["bookmaker_id"],
                "bookmaker_name": market["bookmaker_name"],
                "provider_update": market["update"],
            }

            raw["odds"] = odds

            fixture.raw_json = json.dumps(
                raw,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )

            summary["valid"] += 1
            summary["updated"] += 1

        except Exception:
            # Fail closed for this fixture. Never manufacture prices.
            summary["errors"] += 1
            summary["status"] = "partial"

    session.commit()

    return summary
