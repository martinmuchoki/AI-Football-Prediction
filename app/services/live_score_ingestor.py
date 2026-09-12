from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Callable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.live_score_validation import (
    LiveScoreNormalized,
    LiveScoreValidationError,
    normalize_live_score_fixture,
    normalize_live_score_history,
)
from app.models import Fixture, LeagueSeason, SyncRun, Team
from app.providers.live_score_api import LiveScoreApiClient


PROVIDER = "live-score-api"


def _league(session: Session, n: LiveScoreNormalized) -> LeagueSeason:
    row = session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.provider == PROVIDER,
            LeagueSeason.provider_league_id == n.competition_id,
            LeagueSeason.season == n.season,
        )
    )
    if row is None:
        row = LeagueSeason(
            provider=PROVIDER,
            provider_league_id=n.competition_id,
            season=n.season,
            name=n.competition_name,
            country=n.country_name,
            competition_type="league",
        )
        session.add(row)
        session.flush()
    else:
        row.name = n.competition_name
        row.country = n.country_name
    return row


def _team(session: Session, provider_team_id: int, name: str, logo: str | None) -> Team:
    row = session.scalar(
        select(Team).where(
            Team.provider == PROVIDER,
            Team.provider_team_id == provider_team_id,
        )
    )
    if row is None:
        row = Team(
            provider=PROVIDER,
            provider_team_id=provider_team_id,
            name=name,
            logo_url=logo,
        )
        session.add(row)
        session.flush()
    else:
        row.name = name
        row.logo_url = logo
    return row


def upsert_normalized(session: Session, n: LiveScoreNormalized, raw: dict) -> tuple[Fixture, bool]:
    league = _league(session, n)
    home = _team(session, n.home_team_id, n.home_team_name, n.home_team_logo)
    away = _team(session, n.away_team_id, n.away_team_name, n.away_team_logo)

    row = session.scalar(
        select(Fixture).where(
            Fixture.provider == PROVIDER,
            Fixture.provider_fixture_id == n.provider_fixture_id,
        )
    )
    inserted = row is None
    if row is None:
        row = Fixture(
            provider=PROVIDER,
            provider_fixture_id=n.provider_fixture_id,
            league_season_id=league.id,
            home_team_id=home.id,
            away_team_id=away.id,
            kickoff_utc=n.kickoff_utc,
            status_short=n.status_short,
            raw_json="{}",
        )
        session.add(row)

    row.league_season_id = league.id
    row.home_team_id = home.id
    row.away_team_id = away.id
    row.kickoff_utc = n.kickoff_utc
    row.timezone = "UTC"
    row.round_name = n.round_name
    row.venue_name = n.venue_name
    row.status_short = n.status_short
    row.status_long = n.status_long
    row.home_goals = n.home_goals
    row.away_goals = n.away_goals
    row.halftime_home = n.halftime_home
    row.halftime_away = n.halftime_away
    row.fulltime_home = n.fulltime_home
    row.fulltime_away = n.fulltime_away
    row.extratime_home = n.extratime_home
    row.extratime_away = n.extratime_away
    row.penalty_home = n.penalty_home
    row.penalty_away = n.penalty_away
    row.is_finished = n.is_finished
    row.result_1x2 = n.result_1x2
    row.raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    row.provider_updated_at = datetime.now(timezone.utc)
    session.flush()
    return row, inserted


def _ingest(
    session: Session,
    items: Sequence[dict],
    *,
    season: int,
    run_type: str,
    normalizer: Callable,
) -> dict:
    run = SyncRun(run_type=run_type, provider=PROVIDER, status="running", received=len(items))
    session.add(run)
    session.flush()

    inserted = updated = rejected = 0
    errors: list[str] = []

    for raw in items:
        try:
            normalized = normalizer(raw, season=season)
            _, was_inserted = upsert_normalized(session, normalized, raw)
            inserted += int(was_inserted)
            updated += int(not was_inserted)
        except LiveScoreValidationError as exc:
            rejected += 1
            errors.append(str(exc))

    run.inserted = inserted
    run.updated = updated
    run.rejected = rejected
    run.status = "success" if not errors else "partial"
    run.finished_at = datetime.now(timezone.utc)
    run.error_message = " | ".join(errors[:10]) if errors else None
    session.commit()

    return {
        "received": len(items),
        "inserted": inserted,
        "updated": updated,
        "rejected": rejected,
        "errors": errors,
    }


async def _sync_pages(
    session: Session,
    *,
    client: LiveScoreApiClient,
    competition_id: int,
    season: int,
    fetch_page: Callable,
    list_key: str,
    run_type: str,
    normalizer: Callable,
    page: int | None,
    start_page: int,
    max_pages: int,
    page_size_hint: int | None = None,
    continue_on_full_page: bool = False,
) -> dict:
    # If --page is supplied, perform one exact page for diagnostics/manual recovery.
    current_page = int(page) if page is not None else max(1, int(start_page))
    one_page_only = page is not None
    limit = 1 if one_page_only else max(1, int(max_pages))

    totals = {
        "received": 0,
        "inserted": 0,
        "updated": 0,
        "rejected": 0,
        "errors": [],
        "pages_processed": 0,
        "first_page": current_page,
        "last_successful_page": None,
        "failed_page": None,
        "resume_page": None,
        "stopped_at_max_pages": False,
        "pagination_guard_triggered": False,
    }

    seen_page_signatures: set[tuple] = set()

    for _ in range(limit):
        try:
            response = await fetch_page(current_page)
        except Exception as exc:
            # Earlier pages are already committed by _ingest, so the run can resume safely.
            totals["failed_page"] = current_page
            totals["resume_page"] = current_page
            totals["errors"].append(str(exc))
            break

        items = response.data.get(list_key) or []
        if not isinstance(items, list):
            totals["failed_page"] = current_page
            totals["resume_page"] = current_page
            totals["errors"].append(f"Live Score response does not contain a {list_key} list")
            break

        # Guard against a provider accidentally returning the same page repeatedly.
        signature = tuple(
            str(item.get("fixture_id") or item.get("id") or "")
            for item in items
            if isinstance(item, dict)
        )
        if signature and signature in seen_page_signatures:
            totals["failed_page"] = current_page
            totals["resume_page"] = current_page
            totals["pagination_guard_triggered"] = True
            totals["errors"].append(
                f"Repeated page content detected at page {current_page}; stopped to avoid an infinite loop"
            )
            break
        if signature:
            seen_page_signatures.add(signature)

        page_result = _ingest(
            session,
            items,
            season=season,
            run_type=run_type,
            normalizer=normalizer,
        )
        totals["received"] += page_result["received"]
        totals["inserted"] += page_result["inserted"]
        totals["updated"] += page_result["updated"]
        totals["rejected"] += page_result["rejected"]
        totals["errors"].extend(page_result["errors"])
        totals["pages_processed"] += 1
        totals["last_successful_page"] = current_page

        if one_page_only:
            break

        # Fixtures explicitly provide next_page. The history endpoint may omit
        # next_page even when more results are available. Since Live Score caps
        # a history response at 30 matches, a full page is treated as a signal
        # to probe the next page; pagination stops on a short/empty page.
        has_more = response.has_next_page
        if (
            not has_more
            and continue_on_full_page
            and page_size_hint
            and len(items) >= page_size_hint
        ):
            has_more = True

        if not has_more:
            break

        current_page += 1
    else:
        totals["stopped_at_max_pages"] = True
        totals["resume_page"] = current_page + 1

    totals["competition_id"] = competition_id
    totals["season"] = season
    totals["status"] = "success" if totals["failed_page"] is None and not totals["errors"] else "partial"
    return totals


async def sync_live_score_fixtures(
    session: Session,
    client: LiveScoreApiClient,
    *,
    competition_id: int,
    season: int,
    page: int | None = None,
    start_page: int = 1,
    max_pages: int | None = None,
) -> dict:
    settings_max = max_pages if max_pages is not None else client.settings.ls_max_pages

    async def fetch_page(page_number: int):
        return await client.get_fixtures(competition_id=competition_id, page=page_number)

    return await _sync_pages(
        session,
        client=client,
        competition_id=competition_id,
        season=season,
        fetch_page=fetch_page,
        list_key="fixtures",
        run_type="live-score-fixtures",
        normalizer=normalize_live_score_fixture,
        page=page,
        start_page=start_page,
        max_pages=settings_max,
        page_size_hint=30,
        continue_on_full_page=False,
    )


async def sync_live_score_history(
    session: Session,
    client: LiveScoreApiClient,
    *,
    competition_id: int,
    season: int,
    from_date: date | None = None,
    to_date: date | None = None,
    page: int | None = None,
    start_page: int = 1,
    max_pages: int | None = None,
) -> dict:
    settings_max = max_pages if max_pages is not None else client.settings.ls_max_pages

    async def fetch_page(page_number: int):
        return await client.get_history(
            competition_id=competition_id,
            from_date=from_date,
            to_date=to_date,
            page=page_number,
        )

    return await _sync_pages(
        session,
        client=client,
        competition_id=competition_id,
        season=season,
        fetch_page=fetch_page,
        list_key="match",
        run_type="live-score-history",
        normalizer=normalize_live_score_history,
        page=page,
        start_page=start_page,
        max_pages=settings_max,
        page_size_hint=30,
        continue_on_full_page=True,
    )
