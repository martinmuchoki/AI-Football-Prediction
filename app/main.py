from __future__ import annotations

from pathlib import Path as _Stage3Path
from fastapi import Body as Stage3Body
from fastapi.responses import FileResponse as Stage3FileResponse
from fastapi.staticfiles import StaticFiles as Stage3StaticFiles

from app.services.sportsq_stage3 import (
    approve_queue_item,
    cancel_queue_item,
    content_preview,
    enqueue_package,
    generate_content_package,
    generate_fixture_content_package,
    list_queue,
    process_queue_item,
    stage3_status,
)


from app.services.sportsq_intelligence import (
    get_sportsq_intelligence,
    list_sportsq_intelligence,
    sportsq_accuracy_summary,
    sportsq_capabilities,
)


from contextlib import asynccontextmanager
from datetime import datetime, timezone
import secrets

from fastapi import Depends, FastAPI, Header, HTTPException, Query
from sqlalchemy import func, select

from app import __version__
from app.config import get_settings
from app.db import SessionLocal, init_db
from app.models import Fixture, LeagueSeason, OddsSnapshot, Team
from app.services.prediction_grading import live_prediction_rows, performance_summary
from app.services.source_registry import list_sources, resolve_field, source_health_summary
from app.services.odds_history import list_odds_history, odds_movement
from app.services.source_policy import list_source_policies
from app.services.web_targets import list_web_targets
from app.services.fixture_reconciliation import reconciliation_summary, reconciliation_issues
from app.services.reconciliation_health import reconciliation_health
from app.services.market_safety import market_safety_status
from app.services.market_safety_events import list_market_safety_events
from app.services.market_safety_incidents import (
    active_market_safety_incident,
    list_market_safety_incidents,
    market_safety_incident_metrics,
)
from app.services.market_safety_alerts import list_market_safety_deliveries
from app.services.market_reliability import (
    database_integrity_report,
    list_market_safety_sla_alerts,
    reliability_summary,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="MDRN SportsQ API",
    version=__version__,
    description="MDRN SportsQ multi-source football intelligence and prediction API.",
    lifespan=lifespan,
)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "version": __version__,
        "utc": datetime.now(timezone.utc).isoformat(),
        "phase": 10,
        "milestone": "10A",
    }


@app.get("/status")
def status() -> dict:
    with SessionLocal() as session:
        fixtures = session.scalar(select(func.count(Fixture.id))) or 0
        teams = session.scalar(select(func.count(Team.id))) or 0
        leagues = session.scalar(select(func.count(LeagueSeason.id))) or 0
        finished = session.scalar(select(func.count(Fixture.id)).where(Fixture.is_finished.is_(True))) or 0
    return {
        "fixtures": fixtures,
        "finished_fixtures": finished,
        "teams": teams,
        "league_seasons": leagues,
    }


@app.get("/fixtures")
def fixtures(limit: int = Query(default=50, ge=1, le=500)) -> list[dict]:
    with SessionLocal() as session:
        rows = session.scalars(
            select(Fixture).order_by(Fixture.kickoff_utc.desc()).limit(limit)
        ).all()
        result = []
        for row in rows:
            result.append(
                {
                    "fixture_id": row.provider_fixture_id,
                    "kickoff_utc": row.kickoff_utc,
                    "status": row.status_short,
                    "home": row.home_team.name,
                    "away": row.away_team.name,
                    "home_goals": row.home_goals,
                    "away_goals": row.away_goals,
                    "is_finished": row.is_finished,
                    "result_1x2": row.result_1x2,
                }
            )
        return result


@app.get("/predictions/live")
def predictions_live(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    high_confidence_only: bool = Query(default=False),
) -> list[dict]:
    with SessionLocal() as session:
        return live_prediction_rows(
            session,
            competition_id=competition,
            season=season,
            high_confidence_only=high_confidence_only,
        )


@app.get("/predictions/performance")
def predictions_performance(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    high_confidence_only: bool = Query(default=False),
) -> dict:
    with SessionLocal() as session:
        return performance_summary(
            session,
            competition_id=competition,
            season=season,
            high_confidence_only=high_confidence_only,
        )


def _own_api_guard(x_api_key: str | None = Header(default=None, alias="X-API-Key")) -> None:
    settings = get_settings()
    if not settings.own_api_require_key:
        return
    configured = settings.own_api_key.strip()
    if not configured:
        raise HTTPException(status_code=503, detail="OWN_API_KEY is not configured")
    supplied = (x_api_key or "").strip()
    if not supplied or not secrets.compare_digest(supplied, configured):
        raise HTTPException(status_code=401, detail="Invalid API key")


@app.get("/api/v1/health")
def api_v1_health() -> dict:
    return {
        "status": "ok",
        "service": "MDRN SportsQ API",
        "version": __version__,
        "phase": "10A",
        "utc": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/api/v1/sources", dependencies=[Depends(_own_api_guard)])
def api_v1_sources() -> list[dict]:
    with SessionLocal() as session:
        return list_sources(session)


@app.get("/api/v1/sources/health", dependencies=[Depends(_own_api_guard)])
def api_v1_sources_health() -> dict:
    settings = get_settings()
    with SessionLocal() as session:
        return source_health_summary(
            session,
            stale_after_minutes=settings.source_health_stale_after_minutes,
        )


@app.get("/api/v1/fixtures/upcoming", dependencies=[Depends(_own_api_guard)])
def api_v1_upcoming_fixtures(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    provider: str = Query(default="live-score-api"),
    limit: int = Query(default=50, ge=1, le=500),
) -> list[dict]:
    now = datetime.now(timezone.utc)
    with SessionLocal() as session:
        stmt = (
            select(Fixture)
            .join(LeagueSeason, Fixture.league_season_id == LeagueSeason.id)
            .where(
                Fixture.provider == provider,
                LeagueSeason.provider == provider,
                LeagueSeason.provider_league_id == int(competition),
                LeagueSeason.season == int(season),
                Fixture.is_finished.is_(False),
                Fixture.kickoff_utc >= now,
            )
            .order_by(Fixture.kickoff_utc.asc())
            .limit(limit)
        )
        rows = list(session.scalars(stmt).all())
        return [
            {
                "fixture_id": row.provider_fixture_id,
                "competition_id": competition,
                "season": season,
                "kickoff_utc": row.kickoff_utc,
                "status": row.status_short,
                "home": row.home_team.name,
                "away": row.away_team.name,
                "provider": row.provider,
            }
            for row in rows
        ]


@app.get("/api/v1/predictions/high-confidence", dependencies=[Depends(_own_api_guard)])
def api_v1_high_confidence_predictions(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> list[dict]:
    with SessionLocal() as session:
        return live_prediction_rows(
            session,
            competition_id=competition,
            season=season,
            high_confidence_only=True,
        )


@app.get("/api/v1/predictions/performance", dependencies=[Depends(_own_api_guard)])
def api_v1_prediction_performance(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    high_confidence_only: bool = Query(default=True),
) -> dict:
    with SessionLocal() as session:
        return performance_summary(
            session,
            competition_id=competition,
            season=season,
            high_confidence_only=high_confidence_only,
        )


@app.get("/api/v1/provenance/resolve", dependencies=[Depends(_own_api_guard)])
def api_v1_resolve_provenance(
    entity_type: str = Query(..., min_length=1, max_length=80),
    entity_key: str = Query(..., min_length=1, max_length=240),
    field_name: str = Query(..., min_length=1, max_length=120),
) -> dict:
    with SessionLocal() as session:
        return resolve_field(
            session,
            entity_type=entity_type,
            entity_key=entity_key,
            field_name=field_name,
        )



@app.get("/api/v1/odds/history", dependencies=[Depends(_own_api_guard)])
def api_v1_odds_history(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    fixture_id: int | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=2000),
) -> list[dict]:
    with SessionLocal() as session:
        return list_odds_history(
            session,
            competition_id=competition,
            season=season,
            provider_fixture_id=fixture_id,
            limit=limit,
        )


@app.get("/api/v1/odds/movement", dependencies=[Depends(_own_api_guard)])
def api_v1_odds_movement(
    fixture_id: int = Query(...),
) -> dict:
    with SessionLocal() as session:
        return odds_movement(
            session,
            provider_fixture_id=fixture_id,
        )


@app.get("/api/v1/market/safety", dependencies=[Depends(_own_api_guard)])
def api_v1_market_safety(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    settings = get_settings()

    with SessionLocal() as session:
        return market_safety_status(
            session,
            competition_id=competition,
            season=season,
            source_stale_after_minutes=settings.source_health_stale_after_minutes,
            reconciliation_stale_after_minutes=settings.reconciliation_stale_after_minutes,
        )


@app.get("/api/v1/market/safety/history", dependencies=[Depends(_own_api_guard)])
def api_v1_market_safety_history(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    with SessionLocal() as session:
        return list_market_safety_events(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
            transitions_only=False,
        )


@app.get("/api/v1/market/safety/transitions", dependencies=[Depends(_own_api_guard)])
def api_v1_market_safety_transitions(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    with SessionLocal() as session:
        return list_market_safety_events(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
            transitions_only=True,
        )


@app.get("/api/v1/market/reliability", dependencies=[Depends(_own_api_guard)])
def api_v1_market_reliability(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    settings = get_settings()

    with SessionLocal() as session:
        return reliability_summary(
            session,
            settings,
            competition_id=competition,
            season=season,
        )


@app.get("/api/v1/market/reliability/integrity", dependencies=[Depends(_own_api_guard)])
def api_v1_market_reliability_integrity() -> dict:
    with SessionLocal() as session:
        return database_integrity_report(
            session
        )


@app.get("/api/v1/market/reliability/sla", dependencies=[Depends(_own_api_guard)])
def api_v1_market_reliability_sla(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    with SessionLocal() as session:
        return list_market_safety_sla_alerts(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )


@app.get("/api/v1/market/safety/incidents", dependencies=[Depends(_own_api_guard)])
def api_v1_market_safety_incidents(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    with SessionLocal() as session:
        return list_market_safety_incidents(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )


@app.get("/api/v1/market/safety/incidents/active", dependencies=[Depends(_own_api_guard)])
def api_v1_market_safety_incident_active(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        return active_market_safety_incident(
            session,
            competition_id=competition,
            season=season,
        )


@app.get("/api/v1/market/safety/incidents/metrics", dependencies=[Depends(_own_api_guard)])
def api_v1_market_safety_incident_metrics(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        return market_safety_incident_metrics(
            session,
            competition_id=competition,
            season=season,
        )


@app.get("/api/v1/market/safety/deliveries", dependencies=[Depends(_own_api_guard)])
def api_v1_market_safety_deliveries(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    with SessionLocal() as session:
        return list_market_safety_deliveries(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )


@app.get("/api/v1/market/status", dependencies=[Depends(_own_api_guard)])
def api_v1_market_status(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        latest = session.scalar(
            select(OddsSnapshot)
            .where(
                OddsSnapshot.competition_id == int(competition),
                OddsSnapshot.season == int(season),
            )
            .order_by(OddsSnapshot.captured_at.desc(), OddsSnapshot.id.desc())
            .limit(1)
        )
        count = session.scalar(
            select(func.count(OddsSnapshot.id)).where(
                OddsSnapshot.competition_id == int(competition),
                OddsSnapshot.season == int(season),
            )
        ) or 0
    return {
        "status": "ok",
        "competition_id": int(competition),
        "season": int(season),
        "odds_snapshots": int(count),
        "latest_snapshot_at": latest.captured_at.isoformat() if latest else None,
        "scheduler_module": "python -m app.market_scheduler",
        "final_holdout_touched": False,
    }


@app.get("/api/v1/web/targets", dependencies=[Depends(_own_api_guard)])
def api_v1_web_targets() -> list[dict]:
    with SessionLocal() as session:
        return list_web_targets(session)


@app.get("/api/v1/sources/policies", dependencies=[Depends(_own_api_guard)])
def api_v1_source_policies() -> list[dict]:
    with SessionLocal() as session:
        return list_source_policies(session)


@app.get("/api/v1/reconciliation/summary", dependencies=[Depends(_own_api_guard)])
def api_v1_reconciliation_summary(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        return reconciliation_summary(
            session,
            competition_id=competition,
            season=season,
        )


@app.get("/api/v1/reconciliation/health", dependencies=[Depends(_own_api_guard)])
def api_v1_reconciliation_health(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    settings = get_settings()

    with SessionLocal() as session:
        return reconciliation_health(
            session,
            competition_id=competition,
            season=season,
            stale_after_minutes=settings.reconciliation_stale_after_minutes,
        )


@app.get("/api/v1/reconciliation/issues", dependencies=[Depends(_own_api_guard)])
def api_v1_reconciliation_issues(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=1000),
) -> list[dict]:
    with SessionLocal() as session:
        return reconciliation_issues(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )


# ======================================================================
# MDRN SportsQ v0.12.0 Prediction Intelligence
# ======================================================================

@app.get(
    "/api/v1/sportsq/intelligence",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_intelligence(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict:
    with SessionLocal() as session:
        return list_sportsq_intelligence(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )

@app.get(
    "/api/v1/sportsq/intelligence/{fixture_id}",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_fixture_intelligence(
    fixture_id: int,
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        return get_sportsq_intelligence(
            session,
            fixture_id=fixture_id,
            competition_id=competition,
            season=season,
        )

@app.get(
    "/api/v1/sportsq/form/{fixture_id}",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_form_index(
    fixture_id: int,
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        result = get_sportsq_intelligence(
            session,
            fixture_id=fixture_id,
            competition_id=competition,
            season=season,
        )
    if result.get("status") != "success":
        return result
    item = result["item"]
    return {
        "status": "success",
        "fixture": item["fixture"],
        "sportsq_form_index": item["sportsq_form_index"],
        "historical_holdout_touched": False,
    }


# --- MDRN SPORTSQ STAGE 6 NEWS IMPACT STATIC API ---
from app.services.sportsq_news_impact import (
    list_team_news_events as sportsq_news_events,
    news_impact_status as sportsq_news_impact_status,
)


@app.get(
    "/api/v1/sportsq/news-impact/status",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_news_impact_status() -> dict:
    with SessionLocal() as session:
        return sportsq_news_impact_status(session)


@app.get(
    "/api/v1/sportsq/news-impact/events",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_news_impact_events(
    fixture_id: int | None = None,
    limit: int = 200,
) -> dict:
    with SessionLocal() as session:
        return {
            "status": "success",
            "items": sportsq_news_events(
                session,
                fixture_id=fixture_id,
                limit=limit,
            ),
        }
# --- END MDRN SPORTSQ STAGE 6 NEWS IMPACT STATIC API ---


@app.get(
    "/api/v1/sportsq/news-impact/{fixture_id}",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_news_impact(
    fixture_id: int,
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        result = get_sportsq_intelligence(
            session,
            fixture_id=fixture_id,
            competition_id=competition,
            season=season,
        )
    if result.get("status") != "success":
        return result
    item = result["item"]
    return {
        "status": "success",
        "fixture": item["fixture"],
        "sportsq_news_impact": item["sportsq_news_impact"],
        "historical_holdout_touched": False,
    }

@app.get(
    "/api/v1/sportsq/accuracy",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_accuracy(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        return sportsq_accuracy_summary(
            session,
            competition_id=competition,
            season=season,
        )

@app.get(
    "/api/v1/sportsq/capabilities",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_capabilities() -> dict:
    with SessionLocal() as session:
        return sportsq_capabilities(session)


# ======================================================================
# MDRN SportsQ Stage 3 - Content + GUI + Social Automation Framework
# ======================================================================

_STAGE3_APP_DIR = _Stage3Path(__file__).resolve().parent
_STAGE3_STATIC_DIR = _STAGE3_APP_DIR / "static" / "sportsq"
_STAGE3_PACKAGES_DIR = _STAGE3_APP_DIR.parent / "data" / "sportsq_stage3" / "packages"

app.mount(
    "/sportsq/static",
    Stage3StaticFiles(directory=str(_STAGE3_STATIC_DIR)),
    name="sportsq-static",
)

app.mount(
    "/sportsq/content",
    Stage3StaticFiles(directory=str(_STAGE3_PACKAGES_DIR)),
    name="sportsq-content",
)

@app.get("/sportsq", include_in_schema=False)
def sportsq_dashboard():
    return Stage3FileResponse(
        str(_STAGE3_STATIC_DIR / "index.html")
    )

@app.get(
    "/api/v1/sportsq/stage3/status",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_status() -> dict:
    with SessionLocal() as session:
        return stage3_status(session)

@app.get(
    "/api/v1/sportsq/stage3/preview",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_preview(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=5, ge=1, le=20),
) -> dict:
    with SessionLocal() as session:
        return content_preview(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )

@app.post(
    "/api/v1/sportsq/stage3/generate",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_generate(
    payload: dict | None = Stage3Body(default=None),
) -> dict:
    payload = payload or {}
    competition = int(payload.get("competition", 2))
    season = int(payload.get("season", 2026))
    limit = max(1, min(int(payload.get("limit", 3)), 10))

    with SessionLocal() as session:
        result = generate_content_package(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )

    platforms = payload.get("platforms")
    if isinstance(platforms, list) and platforms:
        result["queue_item"] = enqueue_package(
            result["package_id"],
            platforms=platforms,
            scheduled_for=payload.get("scheduled_for"),
        )

    return result

@app.get(
    "/api/v1/sportsq/stage3/queue",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_queue() -> list[dict]:
    return list_queue()

@app.post(
    "/api/v1/sportsq/stage3/queue",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_enqueue(
    payload: dict | None = Stage3Body(default=None),
) -> dict:
    payload = payload or {}
    package_id = str(payload.get("package_id", "")).strip()
    if not package_id:
        return {
            "status": "error",
            "reason": "package_id_required",
        }

    platforms = payload.get("platforms")
    if not isinstance(platforms, list):
        platforms = ["manual_export"]

    return enqueue_package(
        package_id,
        platforms=platforms,
        scheduled_for=payload.get("scheduled_for"),
    )

@app.post(
    "/api/v1/sportsq/stage3/queue/{queue_id}/approve",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_approve(queue_id: str) -> dict:
    return approve_queue_item(queue_id)

@app.post(
    "/api/v1/sportsq/stage3/queue/{queue_id}/cancel",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_cancel(queue_id: str) -> dict:
    return cancel_queue_item(queue_id)

@app.post(
    "/api/v1/sportsq/stage3/queue/{queue_id}/process",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_process(queue_id: str) -> dict:
    return process_queue_item(queue_id)


@app.post(
    "/api/v1/sportsq/stage3/generate/{fixture_id}",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage3_generate_fixture(
    fixture_id: int,
    payload: dict | None = Stage3Body(default=None),
) -> dict:
    payload = payload or {}
    competition = int(payload.get("competition", 2))
    season = int(payload.get("season", 2026))

    with SessionLocal() as session:
        return generate_fixture_content_package(
            session,
            fixture_id=fixture_id,
            competition_id=competition,
            season=season,
        )


# --- MDRN SPORTSQ STAGE 4 SOURCE + RIGHTS API ---
from app.services.sportsq_source_rights import (
    get_media_asset as sportsq_stage4_get_media_asset,
    get_rights_for_asset as sportsq_stage4_get_rights_for_asset,
    list_media_assets as sportsq_stage4_list_media_assets,
    list_sources as sportsq_stage4_list_sources,
    stage4_status as sportsq_stage4_status,
)


@app.get(
    "/api/v1/sportsq/stage4/status",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage4_status() -> dict:
    return sportsq_stage4_status()


@app.get(
    "/api/v1/sportsq/stage4/sources",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage4_sources(
    trust_class: str | None = None,
    source_kind: str | None = None,
    status: str | None = "ACTIVE",
) -> dict:
    return {
        "items": sportsq_stage4_list_sources(
            trust_class=trust_class,
            source_kind=source_kind,
            status=status,
        )
    }


@app.get(
    "/api/v1/sportsq/stage4/media",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage4_media(
    reuse_verified: bool | None = None,
    verification_status: str | None = None,
    limit: int = 100,
) -> dict:
    return {
        "items": sportsq_stage4_list_media_assets(
            reuse_verified=reuse_verified,
            verification_status=verification_status,
            limit=limit,
        )
    }


@app.get(
    "/api/v1/sportsq/stage4/media/{asset_id}",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage4_media_asset(
    asset_id: str,
) -> dict:
    item = sportsq_stage4_get_media_asset(asset_id)
    return {
        "status": "success" if item else "not_found",
        "item": item,
    }


@app.get(
    "/api/v1/sportsq/stage4/rights/{asset_id}",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage4_rights(
    asset_id: str,
) -> dict:
    return {
        "asset_id": asset_id,
        "items": sportsq_stage4_get_rights_for_asset(
            asset_id
        ),
    }
# --- END MDRN SPORTSQ STAGE 4 SOURCE + RIGHTS API ---


# --- MDRN SPORTSQ STAGE 5 SCORECALL API ---
from app.services.sportsq_scorecall import (
    get_scorecall_lock as sportsq_scorecall_get_lock,
    list_scorecall_locks as sportsq_scorecall_list_locks,
    scorecall_status as sportsq_scorecall_status,
)


@app.get(
    "/api/v1/sportsq/scorecall/status",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_scorecall_status(
    competition_id: int = 2,
    season: int = 2026,
) -> dict:
    with SessionLocal() as session:
        return sportsq_scorecall_status(
            session,
            competition_id=competition_id,
            season=season,
        )


@app.get(
    "/api/v1/sportsq/scorecall",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_scorecall_list(
    competition_id: int = 2,
    season: int = 2026,
    limit: int = 100,
) -> dict:
    with SessionLocal() as session:
        return {
            "status": "success",
            "items": sportsq_scorecall_list_locks(
                session,
                competition_id=competition_id,
                season=season,
                limit=limit,
            ),
        }


@app.get(
    "/api/v1/sportsq/scorecall/{fixture_id}",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_scorecall_fixture(
    fixture_id: int,
) -> dict:
    with SessionLocal() as session:
        item = sportsq_scorecall_get_lock(
            session,
            fixture_id=fixture_id,
        )

        return {
            "status": (
                "success"
                if item is not None
                else "not_found"
            ),
            "item": item,
        }
# --- END MDRN SPORTSQ STAGE 5 SCORECALL API ---

# MDRN SPORTSQ STAGE 7 FIXTURE LIFECYCLE ROUTES
from app.services.sportsq_fixture_lifecycle import (
    fixture_lifecycle_rows,
    lifecycle_status,
)


@app.get(
    "/api/v1/sportsq/stage7/status",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage7_status(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
) -> dict:
    with SessionLocal() as session:
        return lifecycle_status(
            session,
            competition_id=competition,
            season=season,
        )


@app.get(
    "/api/v1/sportsq/stage7/fixtures",
    dependencies=[Depends(_own_api_guard)],
)
def api_v1_sportsq_stage7_fixtures(
    competition: int = Query(default=2),
    season: int = Query(default=2026),
    limit: int = Query(default=100, ge=1, le=500),
) -> dict:
    with SessionLocal() as session:
        rows = fixture_lifecycle_rows(
            session,
            competition_id=competition,
            season=season,
            limit=limit,
        )
    return {
        "brand": "MDRN SportsQ",
        "product": "SportsQ Automated Fixture Lifecycle",
        "stage": 7,
        "competition_id": competition,
        "season": season,
        "count": len(rows),
        "items": rows,
        "read_only": True,
        "historical_holdout_touched": False,
    }


