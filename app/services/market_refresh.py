from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.config import Settings
from app.providers.live_score_api import LiveScoreApiClient
from app.providers.api_football import ApiFootballClient
from app.services.fixture_ingestor import sync_league_season
from app.services.api_football_market import (
    MARKET_SOURCE as API_FOOTBALL_MARKET_SOURCE,
    refresh_api_football_odds,
)
from app.services.live_prediction_engine import generate_live_predictions
from app.services.live_score_ingestor import sync_live_score_fixtures
from app.services.odds_history import capture_pre_match_odds
from app.services.fixture_reconciliation import reconciliation_summary
from app.services.source_registry import (
    get_source,
    mark_source_failure,
    mark_source_success,
    seed_default_sources,
)


async def _refresh_live_score_market_legacy(
    session: Session,
    settings: Settings,
    *,
    competition_id: int,
    season: int,
    threshold: float = 0.65,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Refresh Live Score fixture payloads, capture changed odds, then lock new predictions.

    Existing live predictions are never rewritten. The final historical holdout is
    not read by this workflow.
    """
    seed_default_sources(session)
    source = get_source(session, "live-score-api")
    when = datetime.now(timezone.utc)

    if not settings.ls_api_key.strip() or not settings.ls_api_secret.strip():
        return {
            "status": "config_required",
            "provider": "live-score-api",
            "competition_id": int(competition_id),
            "season": int(season),
            "detail": "Live Score credentials are not configured",
            "final_holdout_touched": False,
        }

    try:
        async with LiveScoreApiClient(settings) as client:
            sync = await sync_live_score_fixtures(
                session,
                client,
                competition_id=int(competition_id),
                season=int(season),
                max_pages=max_pages,
            )
        if sync.get("status") == "success":
            mark_source_success(session, source, checked_at=when)
        else:
            mark_source_failure(session, source, "Live Score fixture sync returned partial status", checked_at=when)
        session.commit()
    except Exception as exc:
        # LiveScoreApiClient already sanitizes HTTP request failures; do not echo URLs.
        detail = str(exc)
        if "key=" in detail.lower() or "secret=" in detail.lower() or "http" in detail.lower():
            detail = type(exc).__name__
        mark_source_failure(session, source, detail, checked_at=when)
        session.commit()
        return {
            "status": "failed",
            "provider": "live-score-api",
            "competition_id": int(competition_id),
            "season": int(season),
            "detail": detail[:500],
            "final_holdout_touched": False,
        }

    odds = capture_pre_match_odds(
        session,
        competition_id=int(competition_id),
        season=int(season),
        now=when,
    )
    reconciliation = reconciliation_summary(
        session,
        competition_id=int(competition_id),
        season=int(season),
    )
    reported_quality_gate = str(reconciliation.get("quality_gate") or "UNKNOWN").upper()
    quality_gate = reported_quality_gate
    stale_after_minutes = max(1, int(settings.reconciliation_stale_after_minutes))
    reconciliation_age_minutes = None
    freshness = "NOT_INITIALIZED" if reported_quality_gate == "UNKNOWN" else "UNKNOWN"

    last_checked_at = reconciliation.get("last_checked_at")
    if last_checked_at:
        try:
            checked_at = datetime.fromisoformat(str(last_checked_at).replace("Z", "+00:00"))
            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(tzinfo=timezone.utc)
            checked_at = checked_at.astimezone(timezone.utc)
            reconciliation_age_minutes = max(
                0.0,
                (when - checked_at).total_seconds() / 60.0,
            )
            if reconciliation_age_minutes > stale_after_minutes:
                quality_gate = "STALE"
                freshness = "STALE"
            else:
                freshness = "FRESH"
        except (TypeError, ValueError):
            quality_gate = "STALE"
            freshness = "INVALID_TIMESTAMP"
    elif reported_quality_gate != "UNKNOWN":
        quality_gate = "STALE"
        freshness = "MISSING_TIMESTAMP"

    (
        prediction_lock_blocked,
        prediction_lock_block_reason,
    ) = _reconciliation_prediction_lock_decision(
        quality_gate,
        reconciliation.get(
            "fixture_agreement_rate"
        ),
    )

    if prediction_lock_blocked:
        gate_action = "BLOCK_NEW_PREDICTIONS"
        predictions = {
            "locked_new": 0,
            "already_locked": 0,
            "high_confidence": 0,
            "pass": 0,
        }
    else:
        gate_action = (
            "ALLOW_DEGRADED"
            if quality_gate == "WARN"
            else "ALLOW_UNVERIFIED"
            if quality_gate in {"UNKNOWN", "STALE"}
            else "ALLOW"
        )
        predictions = generate_live_predictions(
            session,
            competition_id=int(competition_id),
            season=int(season),
            threshold=float(threshold),
            now=when,
        )

    if prediction_lock_blocked:
        refresh_status = "blocked"
    elif quality_gate in {"WARN", "STALE"} or sync.get("status") != "success":
        refresh_status = "partial"
    else:
        refresh_status = "success"

    return {
        "status": refresh_status,
        "provider": "live-score-api",
        "competition_id": int(competition_id),
        "season": int(season),
        "fixture_sync": {
            "status": sync.get("status"),
            "received": sync.get("received", 0),
            "inserted": sync.get("inserted", 0),
            "updated": sync.get("updated", 0),
            "rejected": sync.get("rejected", 0),
            "pages_processed": sync.get("pages_processed", 0),
            "failed_page": sync.get("failed_page"),
        },
        "odds_history": {
            "captured_new": odds.get("captured_new", 0),
            "unchanged": odds.get("unchanged", 0),
            "missing_or_invalid_odds": odds.get("missing_or_invalid_odds", 0),
        },
        "reconciliation_quality": {
            "status": reconciliation.get("status"),
            "quality_gate": quality_gate,
            "reported_quality_gate": reported_quality_gate,
            "freshness": freshness,
            "age_minutes": (
                round(reconciliation_age_minutes, 2)
                if reconciliation_age_minutes is not None
                else None
            ),
            "stale_after_minutes": stale_after_minutes,
            "fixture_agreement_rate": reconciliation.get("fixture_agreement_rate"),
            "overall_counts": reconciliation.get("overall_counts", {}),
            "last_checked_at": reconciliation.get("last_checked_at"),
            "action": gate_action,
        },
        "prediction_lock": {
            
            "blocked": prediction_lock_blocked,
            "block_reason": prediction_lock_block_reason,
    
            "locked_new": predictions.get("locked_new", 0),
            "already_locked": predictions.get("already_locked", 0),
            "high_confidence_new": predictions.get("high_confidence", 0),
            "pass_new": predictions.get("pass", 0),
        },
        "refreshed_at": when.isoformat(),
        "final_holdout_touched": False,
    }



M6D_MIN_WARN_AGREEMENT = 0.99


def _reconciliation_prediction_lock_decision(
    quality_gate: str,
    fixture_agreement_rate,
) -> tuple[bool, str | None]:
    """Fail-closed reconciliation gate for new prediction locks.

    PASS:
        allow.

    WARN:
        allow degraded only when fixture agreement is numeric,
        finite/range-valid, and >= 0.99.

    FAIL / UNKNOWN / STALE / unrecognized:
        block.
    """

    gate = str(
        quality_gate
        or "UNKNOWN"
    ).upper()

    if gate in {
        "FAIL",
        "UNKNOWN",
        "STALE",
    }:
        return (
            True,
            f"RECONCILIATION_{gate}",
        )

    if gate == "WARN":

        try:
            agreement = float(
                fixture_agreement_rate
            )

        except (
            TypeError,
            ValueError,
        ):
            return (
                True,
                "RECONCILIATION_WARN_INVALID_AGREEMENT",
            )

        if (
            agreement != agreement
            or agreement < 0.0
            or agreement > 1.0
        ):
            return (
                True,
                "RECONCILIATION_WARN_INVALID_AGREEMENT",
            )

        if agreement < M6D_MIN_WARN_AGREEMENT:
            return (
                True,
                "RECONCILIATION_WARN_LOW_AGREEMENT",
            )

        return (
            False,
            None,
        )

    if gate == "PASS":
        return (
            False,
            None,
        )

    return (
        True,
        f"RECONCILIATION_UNRECOGNIZED_{gate}",
    )


async def _refresh_api_football_market(
    session: Session,
    settings: Settings,
    *,
    competition_id: int,
    season: int,
    threshold: float = 0.65,
) -> dict[str, Any]:
    """API-Football production market refresh.

    Existing historical LiveScore predictions remain immutable and untouched.
    """
    provider = "api-football"
    when = datetime.now(timezone.utc)

    seed_default_sources(session)
    source = get_source(session, provider)

    if not settings.af_api_key.strip():
        return {
            "status": "config_required",
            "provider": provider,
            "competition_id": int(competition_id),
            "season": int(season),
            "detail": "API-Football credential is not configured",
            "final_holdout_touched": False,
        }

    try:
        async with ApiFootballClient(settings) as client:
            sync = await sync_league_season(
                session,
                client,
                league=int(competition_id),
                season=int(season),
            )

            market_feed = await refresh_api_football_odds(
                session,
                client,
                competition_id=int(competition_id),
                season=int(season),
                now=when,
                lookahead_days=14,
                quota_floor=100,
            )

        if sync.get("status") == "success":
            mark_source_success(
                session,
                source,
                checked_at=when,
            )
        else:
            mark_source_failure(
                session,
                source,
                "API-Football fixture sync returned partial status",
                checked_at=when,
            )

        session.commit()

    except Exception as exc:
        detail = type(exc).__name__

        mark_source_failure(
            session,
            source,
            detail,
            checked_at=when,
        )

        session.commit()

        return {
            "status": "failed",
            "provider": provider,
            "competition_id": int(competition_id),
            "season": int(season),
            "detail": detail,
            "final_holdout_touched": False,
        }

    odds = capture_pre_match_odds(
        session,
        competition_id=int(competition_id),
        season=int(season),
        now=when,
        provider=provider,
        market_source=API_FOOTBALL_MARKET_SOURCE,
        parser_version="api-football-odds-v1",
    )

    reconciliation = reconciliation_summary(
        session,
        competition_id=int(competition_id),
        season=int(season),
        primary_source=provider,
    )

    reported_quality_gate = str(
        reconciliation.get("quality_gate")
        or "UNKNOWN"
    ).upper()

    quality_gate = reported_quality_gate

    stale_after_minutes = max(
        1,
        int(settings.reconciliation_stale_after_minutes),
    )

    reconciliation_age_minutes = None

    freshness = (
        "NOT_INITIALIZED"
        if reported_quality_gate == "UNKNOWN"
        else "UNKNOWN"
    )

    last_checked_at = reconciliation.get(
        "last_checked_at"
    )

    if last_checked_at:
        try:
            checked_at = datetime.fromisoformat(
                str(last_checked_at).replace(
                    "Z",
                    "+00:00",
                )
            )

            if checked_at.tzinfo is None:
                checked_at = checked_at.replace(
                    tzinfo=timezone.utc
                )

            checked_at = checked_at.astimezone(
                timezone.utc
            )

            reconciliation_age_minutes = max(
                0.0,
                (when - checked_at).total_seconds()
                / 60.0,
            )

            if (
                reconciliation_age_minutes
                > stale_after_minutes
            ):
                quality_gate = "STALE"
                freshness = "STALE"
            else:
                freshness = "FRESH"

        except (TypeError, ValueError):
            quality_gate = "STALE"
            freshness = "INVALID_TIMESTAMP"

    elif reported_quality_gate != "UNKNOWN":
        quality_gate = "STALE"
        freshness = "MISSING_TIMESTAMP"

    (
        prediction_lock_blocked,
        prediction_lock_block_reason,
    ) = _reconciliation_prediction_lock_decision(
        quality_gate,
        reconciliation.get(
            "fixture_agreement_rate"
        ),
    )
    

    if prediction_lock_blocked:
        gate_action = "BLOCK_NEW_PREDICTIONS"

        predictions = {
            "locked_new": 0,
            "already_locked": 0,
            "high_confidence": 0,
            "pass": 0,
        }

    else:
        gate_action = (
            "ALLOW_DEGRADED"
            if quality_gate == "WARN"
            else "ALLOW"
        )

        predictions = generate_live_predictions(
            session,
            competition_id=int(competition_id),
            season=int(season),
            threshold=float(threshold),
            now=when,
            provider=provider,
            market_source=API_FOOTBALL_MARKET_SOURCE,
        )

    if prediction_lock_blocked:
        refresh_status = "blocked"

    elif (
        quality_gate == "WARN"
        or sync.get("status") != "success"
        or market_feed.get("status") != "success"
    ):
        refresh_status = "partial"

    else:
        refresh_status = "success"

    return {
        "status": refresh_status,
        "provider": provider,
        "competition_id": int(competition_id),
        "season": int(season),

        "fixture_sync": {
            "status": sync.get("status"),
            "received": sync.get(
                "received",
                sync.get("api_results", 0),
            ),
            "inserted": sync.get("inserted", 0),
            "updated": sync.get("updated", 0),
            "rejected": sync.get("rejected", 0),
            "requests_remaining": sync.get(
                "requests_remaining"
            ),
        },

        "provider_odds": {
            "status": market_feed.get("status"),
            "candidates": market_feed.get(
                "candidates",
                0,
            ),
            "valid": market_feed.get("valid", 0),
            "missing": market_feed.get(
                "missing",
                0,
            ),
            "errors": market_feed.get(
                "errors",
                0,
            ),
            "requests_remaining": market_feed.get(
                "requests_remaining"
            ),
            "quota_stop": market_feed.get(
                "quota_stop",
                False,
            ),
        },

        "odds_history": {
            "captured_new": odds.get(
                "captured_new",
                0,
            ),
            "unchanged": odds.get(
                "unchanged",
                0,
            ),
            "missing_or_invalid_odds": odds.get(
                "missing_or_invalid_odds",
                0,
            ),
        },

        "reconciliation_quality": {
            "status": reconciliation.get(
                "status"
            ),
            "quality_gate": quality_gate,
            "reported_quality_gate":
                reported_quality_gate,
            "freshness": freshness,
            "age_minutes": (
                round(
                    reconciliation_age_minutes,
                    2,
                )
                if reconciliation_age_minutes
                is not None
                else None
            ),
            "stale_after_minutes":
                stale_after_minutes,
            "fixture_agreement_rate":
                reconciliation.get(
                    "fixture_agreement_rate"
                ),
            "overall_counts":
                reconciliation.get(
                    "overall_counts",
                    {},
                ),
            "last_checked_at":
                reconciliation.get(
                    "last_checked_at"
                ),
            "action": gate_action,
        },

        "prediction_lock": {
            "blocked":
                prediction_lock_blocked,
            
            "block_reason":
                prediction_lock_block_reason,
    
            "locked_new": predictions.get(
                "locked_new",
                0,
            ),
            "already_locked":
                predictions.get(
                    "already_locked",
                    0,
                ),
            "high_confidence_new":
                predictions.get(
                    "high_confidence",
                    0,
                ),
            "pass_new": predictions.get(
                "pass",
                0,
            ),
        },

        "refreshed_at": when.isoformat(),
        "final_holdout_touched": False,
    }


async def refresh_live_market(
    session: Session,
    settings: Settings,
    *,
    competition_id: int,
    season: int,
    threshold: float = 0.65,
    max_pages: int | None = None,
) -> dict[str, Any]:
    """Provider-compatible SportsQ market dispatcher.

    API-Football league 39 is the production path.
    The legacy LiveScore implementation remains available only for
    backwards-compatible tests/manual historical diagnostics.
    """
    if int(competition_id) == 39:
        return await _refresh_api_football_market(
            session,
            settings,
            competition_id=int(competition_id),
            season=int(season),
            threshold=float(threshold),
        )

    return await _refresh_live_score_market_legacy(
        session,
        settings,
        competition_id=int(competition_id),
        season=int(season),
        threshold=float(threshold),
        max_pages=max_pages,
    )
