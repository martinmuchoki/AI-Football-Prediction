from __future__ import annotations

import asyncio

from apscheduler.schedulers.asyncio import AsyncIOScheduler

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.providers.openfootball_json import OpenFootballJsonClient
from app.services.fixture_reconciliation import reconcile_openfootball_payload
from app.services.result_verification import reconcile_stored_provider_pair
from app.services.market_refresh import refresh_live_market
from app.services.sportsq_fixture_lifecycle import run_fixture_lifecycle_post_refresh
from app.services.sportsq_content_automation import automate_ready_fixture_content
from app.services.market_safety import market_safety_status
from app.services.market_safety_events import record_market_safety_event
from app.services.market_safety_incidents import process_market_safety_incident
from app.services.market_reliability import (
    evaluate_and_dispatch_market_safety_sla,
    recover_incident_state_from_history,
)
from app.services.market_safety_alerts import deliver_market_safety_alert
from app.services.openfootball_sync import store_openfootball_document
from app.services.source_health import run_source_health_checks
from app.services.source_policy import seed_known_source_policies
from app.services.web_targets import collect_due_web_targets

settings = get_settings()


def _openfootball_season_label(season: int) -> str:
    season = int(season)
    return f"{season}-{str(season + 1)[-2:]}"


async def openfootball_reconciliation_job() -> dict:
    """Refresh EPL OpenFootball reconciliation before scheduled market locking.

    OpenFootball is currently configured for API-Football EPL league 39 only. Provider
    failure is isolated from the market-refresh workflow: Phase 10A.7 freshness
    protection decides whether persisted reconciliation can still be trusted.
    """
    results = []

    for competition, season in settings.sportsq_competitions():
        competition = int(competition)
        season = int(season)

        if competition != 39:
            continue

        season_label = _openfootball_season_label(season)

        try:
            async with OpenFootballJsonClient(
                timeout_seconds=settings.public_web_timeout_seconds
            ) as client:
                document = await client.fetch_league(
                    season_label=season_label,
                    league_file="en.1.json",
                )

            with SessionLocal() as session:
                seed_known_source_policies(session)
                store_openfootball_document(
                    session,
                    document=document,
                    season_label=season_label,
                )
                summary = reconcile_openfootball_payload(
                    session,
                    payload=document.payload,
                    competition_id=competition,
                    season=season,
                    primary_source="api-football",
                )

            item = {
                "status": "success",
                "competition_id": competition,
                "season": season,
                "season_label": season_label,
                "quality_gate": summary.get("quality_gate"),
                "fixture_agreement_rate": summary.get("fixture_agreement_rate"),
                "overall_counts": summary.get("overall_counts", {}),
                "final_holdout_touched": False,
            }
            results.append(item)
            print(
                f"[reconciliation-refresh] competition={competition} "
                f"season={season} {item}"
            )

        except Exception as exc:
            item = {
                "status": "failed",
                "competition_id": competition,
                "season": season,
                "season_label": season_label,
                "detail": type(exc).__name__,
                "final_holdout_touched": False,
            }
            results.append(item)
            print(
                f"[reconciliation-refresh] competition={competition} "
                f"season={season} {item}"
            )

    if not results:
        status = "not_configured"
    elif all(item["status"] == "success" for item in results):
        status = "success"
    else:
        status = "partial"

    return {
        "status": status,
        "results": results,
        "final_holdout_touched": False,
    }


async def live_market_job() -> None:
    reconciliation = await openfootball_reconciliation_job()
    print(
        "[reconciliation-before-market] "
        f"status={reconciliation.get('status')}"
    )

    for competition, season in settings.sportsq_competitions():
        with SessionLocal() as session:

            # Recover any crash gap where a safety event was persisted
            # but incident lifecycle persistence did not complete.
            try:
                recovery = recover_incident_state_from_history(
                    session,
                    competition_id=competition,
                    season=season,
                )

                print(
                    f"[market-reliability-recovery] "
                    f"competition={competition} "
                    f"season={season} "
                    f"status={recovery.get('status')} "
                    f"processed={recovery.get('processed', 0)}"
                )

            except Exception as exc:
                print(
                    f"[market-reliability-recovery] "
                    f"competition={competition} "
                    f"season={season} "
                    f"status=recovery_failed "
                    f"detail={type(exc).__name__}"
                )

            result = await refresh_live_market(
                session,
                settings,
                competition_id=competition,
                season=season,
            )
            try:
                stage7_lifecycle = await run_fixture_lifecycle_post_refresh(
                    session,
                    settings,
                    competition_id=competition,
                    season=season,
                    market_result=result,
                    provider="api-football",
                )
            except Exception as exc:
                stage7_lifecycle = {
                    "stage": 7,
                    "status": "isolated_failure",
                    "detail": type(exc).__name__,
                    "final_holdout_touched": False,
                }
                print(
                    "[stage7-lifecycle] isolated_failure "
                    f"detail={type(exc).__name__}"
                )
            if isinstance(result, dict):
                result["stage7_lifecycle"] = stage7_lifecycle

            result_refresh = (
                stage7_lifecycle.get("result_refresh") or {}
                if isinstance(stage7_lifecycle, dict)
                else {}
            )

            if (
                isinstance(result_refresh, dict)
                and result_refresh.get("status") == "success"
            ):
                try:
                    result_verification = reconcile_stored_provider_pair(
                        session,
                        competition_id=competition,
                        season=season,
                    )
                except Exception as exc:
                    result_verification = {
                        "status": "failed",
                        "detail": type(exc).__name__,
                        "final_holdout_touched": False,
                    }
            else:
                result_verification = {
                    "status": "skipped",
                    "reason": "live_score_result_refresh_not_successful",
                    "final_holdout_touched": False,
                }

            if isinstance(result, dict):
                result["result_verification"] = result_verification

            # Content automation is downstream of Stage 7 and isolated from
            # market refresh, lifecycle processing, and observability.
            if stage7_lifecycle.get("status") != "isolated_failure":
                try:
                    content_automation = automate_ready_fixture_content(
                        session,
                        competition_id=competition,
                        season=season,
                        provider="api-football",
                    )
                except Exception as exc:
                    content_automation = {
                        "status": "isolated_failure",
                        "detail": type(exc).__name__,
                        "safety": {
                            "approval_performed": False,
                            "queue_processing_performed": False,
                            "social_auto_posting_performed": False,
                            "network_posting_performed": False,
                        },
                    }
                    print(
                        "[content-automation] isolated_failure "
                        f"detail={type(exc).__name__}"
                    )
            else:
                content_automation = {
                    "status": "skipped",
                    "reason": "stage7_lifecycle_isolated_failure",
                    "safety": {
                        "approval_performed": False,
                        "queue_processing_performed": False,
                        "social_auto_posting_performed": False,
                        "network_posting_performed": False,
                    },
                }

            if isinstance(result, dict):
                result["content_automation"] = content_automation

            print(
                f"[market-refresh] "
                f"competition={competition} "
                f"season={season} {result}"
            )

            # Operational observability must never interrupt
            # the core market-refresh path.
            try:
                safety = market_safety_status(
                    session,
                    competition_id=competition,
                    season=season,
                    source_stale_after_minutes=getattr(
                        settings,
                        "source_health_stale_after_minutes",
                        180,
                    ),
                    reconciliation_stale_after_minutes=getattr(
                        settings,
                        "reconciliation_stale_after_minutes",
                        180,
                    ),
                    primary_source="api-football",
                )

                event = record_market_safety_event(
                    session,
                    snapshot=safety,
                )

                print(
                    f"[market-safety] "
                    f"competition={competition} "
                    f"season={season} "
                    f"status={event.get('overall_status')} "
                    f"transition={event.get('transition')} "
                    f"alert={event.get('alert_type')}"
                )

                # Incident lifecycle is operational observability.
                # Failure here must never interrupt prediction refresh
                # or transition-alert delivery.
                try:
                    incident_state = process_market_safety_incident(
                        session,
                        event=event,
                    )

                    event["incident"] = incident_state

                    incident = dict(
                        incident_state.get(
                            "incident",
                            {},
                        )
                        or {}
                    )

                    print(
                        f"[market-safety-incident] "
                        f"competition={competition} "
                        f"season={season} "
                        f"action={incident_state.get('action')} "
                        f"incident_id={incident.get('id')} "
                        f"status={incident.get('status')} "
                        f"severity={incident.get('highest_severity')}"
                    )

                    # SLA evaluation runs on every observation, not only
                    # transitions. Persisted stages guarantee one alert
                    # per incident/stage.
                    try:
                        sla = await evaluate_and_dispatch_market_safety_sla(
                            session,
                            settings,
                            incident=(
                                incident
                                if incident
                                and incident.get("status") == "OPEN"
                                else None
                            ),
                        )

                        delivery = dict(
                            sla.get(
                                "delivery",
                                {},
                            )
                            or {}
                        )

                        alert = dict(
                            sla.get(
                                "alert",
                                {},
                            )
                            or {}
                        )

                        print(
                            f"[market-safety-sla] "
                            f"competition={competition} "
                            f"season={season} "
                            f"status={sla.get('status')} "
                            f"stage={alert.get('stage')} "
                            f"telegram={delivery.get('telegram_delivery_status', 'not_due')}"
                        )

                    except Exception as exc:
                        print(
                            f"[market-safety-sla] "
                            f"competition={competition} "
                            f"season={season} "
                            f"status=sla_observability_failed "
                            f"detail={type(exc).__name__}"
                        )

                except Exception as exc:
                    print(
                        f"[market-safety-incident] "
                        f"competition={competition} "
                        f"season={season} "
                        f"status=incident_tracking_failed "
                        f"detail={type(exc).__name__}"
                    )

                if event.get("alert_type"):
                    try:
                        delivery = await deliver_market_safety_alert(
                            session,
                            settings,
                            event=event,
                        )

                        telegram = dict(
                            delivery.get(
                                "telegram",
                                {},
                            )
                            or {}
                        )

                        print(
                            f"[market-safety-alert] "
                            f"competition={competition} "
                            f"season={season} "
                            f"webhook={delivery.get('delivery_status', delivery.get('status'))} "
                            f"telegram={telegram.get('delivery_status', telegram.get('status', 'not_enabled'))} "
                            f"attempts={delivery.get('attempts', 0)}"
                        )

                    except Exception as exc:
                        print(
                            f"[market-safety-alert] "
                            f"competition={competition} "
                            f"season={season} "
                            f"status=delivery_audit_failed "
                            f"detail={type(exc).__name__}"
                        )

            except Exception as exc:
                print(
                    f"[market-safety] "
                    f"competition={competition} "
                    f"season={season} "
                    f"status=observability_failed "
                    f"detail={type(exc).__name__}"
                )


async def source_health_job() -> None:
    with SessionLocal() as session:
        result = await run_source_health_checks(session, settings)
        safe = {
            "status": result.get("status"),
            "checked_at": result.get("checked_at"),
            "health_counts": result.get("summary", {}).get("health_counts", {}),
        }
        print(f"[source-health] {safe}")


async def public_web_job() -> None:
    with SessionLocal() as session:
        result = await collect_due_web_targets(session, settings)
        safe = {
            "status": result.get("status"),
            "targets": result.get("targets"),
            "collected": result.get("collected"),
            "unchanged": result.get("unchanged"),
            "config_required": result.get("config_required"),
            "failed": result.get("failed"),
        }
        print(f"[public-web] {safe}")


async def main() -> None:
    init_db()
    scheduler = AsyncIOScheduler(timezone="UTC")
    scheduler.add_job(
        live_market_job,
        "interval",
        minutes=max(15, int(settings.live_market_refresh_every_minutes)),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        source_health_job,
        "interval",
        minutes=max(15, int(settings.source_health_every_minutes)),
        max_instances=1,
        coalesce=True,
    )
    scheduler.add_job(
        public_web_job,
        "interval",
        minutes=max(15, int(settings.public_web_collect_every_minutes)),
        max_instances=1,
        coalesce=True,
    )
    scheduler.start()

    await source_health_job()
    await live_market_job()
    await public_web_job()

    print("MDRN SportsQ market scheduler running. Press Ctrl+C to stop.")
    try:
        await asyncio.Event().wait()
    finally:
        scheduler.shutdown(wait=False)


if __name__ == "__main__":
    asyncio.run(main())
