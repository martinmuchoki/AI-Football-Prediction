from __future__ import annotations

import json
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Sequence

from sqlalchemy import and_, select
from sqlalchemy.orm import Session

from app.models import Fixture, LeagueSeason, SyncRun, Team
from app.providers.api_football import ApiFootballClient
from app.validation import FixtureValidationError, NormalizedFixture, normalize_fixture


class IngestionSummary(dict):
    pass


def _get_or_create_league(session: Session, f: NormalizedFixture) -> LeagueSeason:
    row = session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.provider == "api-football",
            LeagueSeason.provider_league_id == f.league_id,
            LeagueSeason.season == f.season,
        )
    )
    if row is None:
        row = LeagueSeason(
            provider="api-football",
            provider_league_id=f.league_id,
            season=f.season,
            name=f.league_name,
            country=f.league_country,
            competition_type=f.league_type,
            logo_url=f.league_logo,
            flag_url=f.league_flag,
        )
        session.add(row)
        session.flush()
    else:
        row.name = f.league_name
        row.country = f.league_country
        row.competition_type = f.league_type
        row.logo_url = f.league_logo
        row.flag_url = f.league_flag
    return row


def _get_or_create_team(
    session: Session,
    *,
    provider_team_id: int,
    name: str,
    logo_url: str | None,
) -> Team:
    row = session.scalar(
        select(Team).where(
            Team.provider == "api-football",
            Team.provider_team_id == provider_team_id,
        )
    )
    if row is None:
        row = Team(
            provider="api-football",
            provider_team_id=provider_team_id,
            name=name,
            logo_url=logo_url,
        )
        session.add(row)
        session.flush()
    else:
        row.name = name
        row.logo_url = logo_url
    return row


def upsert_fixture(session: Session, raw: dict) -> tuple[Fixture, bool]:
    f = normalize_fixture(raw)
    league = _get_or_create_league(session, f)
    home = _get_or_create_team(
        session,
        provider_team_id=f.home_team_id,
        name=f.home_team_name,
        logo_url=f.home_team_logo,
    )
    away = _get_or_create_team(
        session,
        provider_team_id=f.away_team_id,
        name=f.away_team_name,
        logo_url=f.away_team_logo,
    )

    row = session.scalar(
        select(Fixture).where(
            Fixture.provider == "api-football",
            Fixture.provider_fixture_id == f.provider_fixture_id,
        )
    )
    inserted = row is None
    if row is None:
        row = Fixture(
            provider="api-football",
            provider_fixture_id=f.provider_fixture_id,
            league_season_id=league.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_utc=f.kickoff_utc,
            status_short=f.status_short,
            raw_json="{}",
        )
        session.add(row)

    row.league_season_id = league.id
    row.home_team_id = home.id
    row.away_team_id = away.id
    row.kickoff_utc = f.kickoff_utc
    row.timezone = f.timezone_name
    row.round_name = f.round_name
    row.referee = f.referee
    row.venue_id = f.venue_id
    row.venue_name = f.venue_name
    row.venue_city = f.venue_city
    row.status_short = f.status_short
    row.status_long = f.status_long
    row.elapsed = f.elapsed
    row.home_goals = f.home_goals
    row.away_goals = f.away_goals
    row.halftime_home = f.halftime_home
    row.halftime_away = f.halftime_away
    row.fulltime_home = f.fulltime_home
    row.fulltime_away = f.fulltime_away
    row.extratime_home = f.extratime_home
    row.extratime_away = f.extratime_away
    row.penalty_home = f.penalty_home
    row.penalty_away = f.penalty_away
    row.is_finished = f.is_finished
    row.result_1x2 = f.result_1x2
    row.raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row.provider_updated_at = datetime.now(timezone.utc)
    session.flush()
    return row, inserted


def ingest_payload(session: Session, items: Sequence[dict], run_type: str) -> IngestionSummary:
    run = SyncRun(run_type=run_type, status="running", received=len(items))
    session.add(run)
    session.flush()

    inserted = updated = rejected = 0
    errors: list[str] = []

    for raw in items:
        try:
            _, was_inserted = upsert_fixture(session, raw)
            if was_inserted:
                inserted += 1
            else:
                updated += 1
        except FixtureValidationError as exc:
            rejected += 1
            errors.append(str(exc))

    run.inserted = inserted
    run.updated = updated
    run.rejected = rejected
    run.status = "success" if not errors else "partial"
    run.finished_at = datetime.now(timezone.utc)
    if errors:
        run.error_message = " | ".join(errors[:10])

    session.commit()
    return IngestionSummary(
        received=len(items),
        inserted=inserted,
        updated=updated,
        rejected=rejected,
        errors=errors,
    )


async def sync_fixture_window(
    session: Session,
    client: ApiFootballClient,
    *,
    league: int,
    season: int,
    from_date: date,
    to_date: date,
) -> IngestionSummary:
    response = await client.get_fixtures(
        league=league,
        season=season,
        from_date=from_date,
        to_date=to_date,
        timezone_name="UTC",
    )
    summary = ingest_payload(session, response.data, "fixtures")
    summary["api_results"] = response.results
    summary["requests_remaining"] = response.requests_remaining
    return summary


async def sync_league_season(
    session: Session,
    client: ApiFootballClient,
    *,
    league: int,
    season: int,
) -> IngestionSummary:
    """Synchronize one complete API-Football league/season in one request."""
    response = await client.get_fixtures(
        league=int(league),
        season=int(season),
        timezone_name="UTC",
    )

    summary = ingest_payload(
        session,
        response.data,
        "api-football-fixtures",
    )

    summary["status"] = (
        "success"
        if not summary.get("errors")
        else "partial"
    )
    summary["api_results"] = response.results
    summary["requests_remaining"] = response.requests_remaining

    return summary


def unfinished_provider_ids(
    session: Session,
    *,
    league: int | None = None,
    season: int | None = None,
    lookback_hours: int = 36,
    now: datetime | None = None,
) -> list[int]:
    """Return only API-Football unfinished fixture IDs.

    Provider and optional league/season filters prevent historical LiveScore
    rows from leaking into the API-Football result refresh.
    """
    now = now or datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=lookback_hours)

    stmt = (
        select(Fixture.provider_fixture_id)
        .join(
            LeagueSeason,
            Fixture.league_season_id == LeagueSeason.id,
        )
        .where(
            Fixture.provider == "api-football",
            LeagueSeason.provider == "api-football",
            Fixture.is_finished.is_(False),
            Fixture.kickoff_utc <= now,
            Fixture.kickoff_utc >= cutoff,
        )
    )

    if league is not None:
        stmt = stmt.where(
            LeagueSeason.provider_league_id == int(league)
        )

    if season is not None:
        stmt = stmt.where(
            LeagueSeason.season == int(season)
        )

    stmt = stmt.order_by(Fixture.kickoff_utc.asc())

    return [
        int(x)
        for x in session.scalars(stmt).all()
    ]


def _chunks(values: Sequence[int], size: int) -> Iterable[Sequence[int]]:
    for i in range(0, len(values), size):
        yield values[i:i + size]


async def refresh_unfinished_results(
    session: Session,
    client: ApiFootballClient,
    *,
    league: int | None = None,
    season: int | None = None,
    lookback_hours: int = 36,
    now: datetime | None = None,
) -> IngestionSummary:
    ids = unfinished_provider_ids(
        session,
        league=league,
        season=season,
        lookback_hours=lookback_hours,
        now=now,
    )

    if not ids:
        return IngestionSummary(
            requested=0,
            received=0,
            inserted=0,
            updated=0,
            rejected=0,
            errors=[],
            requests_remaining=None,
        )

    aggregate = IngestionSummary(
        requested=len(ids),
        received=0,
        inserted=0,
        updated=0,
        rejected=0,
        errors=[],
        requests_remaining=None,
    )

    for batch in _chunks(ids, 20):
        response = await client.get_fixtures(
            ids=batch,
            timezone_name="UTC",
        )

        summary = ingest_payload(
            session,
            response.data,
            "api-football-results",
        )

        aggregate["received"] += summary["received"]
        aggregate["inserted"] += summary["inserted"]
        aggregate["updated"] += summary["updated"]
        aggregate["rejected"] += summary["rejected"]
        aggregate["errors"].extend(summary["errors"])
        aggregate["requests_remaining"] = (
            response.requests_remaining
        )

    aggregate["status"] = (
        "success"
        if not aggregate["errors"]
        else "partial"
    )

    return aggregate
