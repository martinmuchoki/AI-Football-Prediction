from __future__ import annotations

import asyncio
from datetime import date, timedelta

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.providers.api_football import ApiFootballClient
from app.services.fixture_ingestor import refresh_unfinished_results, sync_fixture_window


settings = get_settings()


async def fixture_job() -> None:
    today = date.today()
    from_date = today - timedelta(days=settings.fixture_lookback_days)
    to_date = today + timedelta(days=settings.fixture_lookahead_days)

    async with ApiFootballClient(settings) as client:
        for league, season in settings.tracked_competitions():
            with SessionLocal() as session:
                summary = await sync_fixture_window(
                    session,
                    client,
                    league=league,
                    season=season,
                    from_date=from_date,
                    to_date=to_date,
                )
                print(f"[fixtures] league={league} season={season} {dict(summary)}")


async def result_job() -> None:
    async with ApiFootballClient(settings) as client:
        with SessionLocal() as session:
            summary = await refresh_unfinished_results(
                session,
                client,
                lookback_hours=settings.result_lookback_hours,
            )
            print(f"[results] {dict(summary)}")


async def main() -> None:
    settings.require_api_key()
    init_db()

    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        fixture_job,
        "interval",
        hours=max(1, settings.fixture_sync_every_hours),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        result_job,
        "interval",
        minutes=max(15, settings.result_sync_every_minutes),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    await fixture_job()
    await result_job()

    print("Scheduler running. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
