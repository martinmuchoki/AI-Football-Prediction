from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import Fixture, LeagueSeason, OddsSnapshot, Team
from app.services.live_prediction_engine import (
    MARKET_SOURCE,
    PROVIDER,
    extract_pre_match_odds,
    fair_probabilities_from_decimal_odds,
)
from app.services.source_registry import record_observation


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _snapshot_hash(home: float, draw: float, away: float) -> str:
    payload = json.dumps(
        {"H": round(float(home), 8), "D": round(float(draw), 8), "A": round(float(away), 8)},
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def capture_pre_match_odds(
    session: Session,
    *,
    competition_id: int,
    season: int,
    now: datetime | None = None,
    limit: int | None = None,
    provider: str = PROVIDER,
    market_source: str = MARKET_SOURCE,
    parser_version: str = "live-score-odds-v1",
) -> dict[str, Any]:
    """Capture each distinct upcoming pre-match 1X2 market state.

    This does NOT change locked predictions. It only builds an auditable odds
    history for future movement analysis and feature research.
    """
    captured_at = _utc(now or datetime.now(timezone.utc))

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
        "market_source": market_source,
        "candidates": len(candidates),
        "captured_new": 0,
        "unchanged": 0,
        "missing_or_invalid_odds": 0,
        "already_started": 0,
        "snapshots": [],
        "final_holdout_touched": False,
    }

    for fixture, league, home_name, away_name in candidates:
        kickoff = _utc(fixture.kickoff_utc)
        if kickoff <= captured_at:
            summary["already_started"] += 1
            continue

        try:
            home_odds, draw_odds, away_odds = extract_pre_match_odds(fixture.raw_json)
            p_home, p_draw, p_away, overround = fair_probabilities_from_decimal_odds(
                home_odds, draw_odds, away_odds
            )
        except ValueError:
            summary["missing_or_invalid_odds"] += 1
            continue

        raw_hash = _snapshot_hash(home_odds, draw_odds, away_odds)
        existing = session.scalar(
            select(OddsSnapshot).where(
                OddsSnapshot.fixture_id == fixture.id,
                OddsSnapshot.market_source == market_source,
                OddsSnapshot.raw_hash == raw_hash,
            )
        )
        if existing is not None:
            summary["unchanged"] += 1
            continue

        row = OddsSnapshot(
            fixture_id=fixture.id,
            provider=provider,
            provider_fixture_id=fixture.provider_fixture_id,
            competition_id=league.provider_league_id,
            season=league.season,
            market_source=market_source,
            home_odds=home_odds,
            draw_odds=draw_odds,
            away_odds=away_odds,
            market_overround=overround,
            p_home=p_home,
            p_draw=p_draw,
            p_away=p_away,
            raw_hash=raw_hash,
            captured_at=captured_at,
            kickoff_utc=kickoff,
        )
        session.add(row)

        entity_key = str(fixture.provider_fixture_id)
        source_url = None
        for field_name, value in (
            ("odds.pre.H", home_odds),
            ("odds.pre.D", draw_odds),
            ("odds.pre.A", away_odds),
            ("fair_probability.H", p_home),
            ("fair_probability.D", p_draw),
            ("fair_probability.A", p_away),
        ):
            record_observation(
                session,
                source_slug=provider,
                entity_type="fixture",
                entity_key=entity_key,
                field_name=field_name,
                value=value,
                source_url=source_url,
                observed_at=captured_at,
                raw_hash=raw_hash,
                parser_version=parser_version,
            )

        summary["captured_new"] += 1
        summary["snapshots"].append(
            {
                "fixture_id": fixture.provider_fixture_id,
                "home": str(home_name),
                "away": str(away_name),
                "kickoff_utc": kickoff.isoformat(),
                "captured_at": captured_at.isoformat(),
                "odds": {"H": home_odds, "D": draw_odds, "A": away_odds},
                "fair_probabilities": {
                    "H": round(p_home, 6),
                    "D": round(p_draw, 6),
                    "A": round(p_away, 6),
                },
            }
        )

    session.commit()
    return summary


def list_odds_history(
    session: Session,
    *,
    competition_id: int,
    season: int,
    provider_fixture_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    Home = aliased(Team)
    Away = aliased(Team)
    stmt = (
        select(OddsSnapshot, Home.name, Away.name)
        .join(Fixture, OddsSnapshot.fixture_id == Fixture.id)
        .join(Home, Fixture.home_team_id == Home.id)
        .join(Away, Fixture.away_team_id == Away.id)
        .where(
            OddsSnapshot.competition_id == int(competition_id),
            OddsSnapshot.season == int(season),
        )
        .order_by(OddsSnapshot.captured_at.desc(), OddsSnapshot.id.desc())
        .limit(max(1, min(int(limit), 2000)))
    )
    if provider_fixture_id is not None:
        stmt = stmt.where(OddsSnapshot.provider_fixture_id == int(provider_fixture_id))

    rows = []
    for snap, home_name, away_name in session.execute(stmt).all():
        rows.append(
            {
                "fixture_id": snap.provider_fixture_id,
                "home": str(home_name),
                "away": str(away_name),
                "kickoff_utc": _utc(snap.kickoff_utc).isoformat(),
                "captured_at": _utc(snap.captured_at).isoformat(),
                "odds": {
                    "H": snap.home_odds,
                    "D": snap.draw_odds,
                    "A": snap.away_odds,
                },
                "fair_probabilities": {
                    "H": round(float(snap.p_home), 6),
                    "D": round(float(snap.p_draw), 6),
                    "A": round(float(snap.p_away), 6),
                },
                "overround": round(float(snap.market_overround), 6),
                "raw_hash": snap.raw_hash,
            }
        )
    return rows


def odds_movement(
    session: Session,
    *,
    provider_fixture_id: int,
) -> dict[str, Any]:
    stmt = (
        select(OddsSnapshot)
        .where(OddsSnapshot.provider_fixture_id == int(provider_fixture_id))
        .order_by(OddsSnapshot.captured_at.asc(), OddsSnapshot.id.asc())
    )
    rows = list(session.scalars(stmt).all())
    if not rows:
        return {
            "status": "not_found",
            "fixture_id": int(provider_fixture_id),
            "snapshots": 0,
        }

    first = rows[0]
    latest = rows[-1]
    return {
        "status": "success",
        "fixture_id": int(provider_fixture_id),
        "snapshots": len(rows),
        "first_captured_at": _utc(first.captured_at).isoformat(),
        "latest_captured_at": _utc(latest.captured_at).isoformat(),
        "opening": {
            "odds": {"H": first.home_odds, "D": first.draw_odds, "A": first.away_odds},
            "fair_probabilities": {
                "H": round(float(first.p_home), 6),
                "D": round(float(first.p_draw), 6),
                "A": round(float(first.p_away), 6),
            },
        },
        "latest": {
            "odds": {"H": latest.home_odds, "D": latest.draw_odds, "A": latest.away_odds},
            "fair_probabilities": {
                "H": round(float(latest.p_home), 6),
                "D": round(float(latest.p_draw), 6),
                "A": round(float(latest.p_away), 6),
            },
        },
        "probability_delta": {
            "H": round(float(latest.p_home - first.p_home), 6),
            "D": round(float(latest.p_draw - first.p_draw), 6),
            "A": round(float(latest.p_away - first.p_away), 6),
        },
        "final_holdout_touched": False,
    }
