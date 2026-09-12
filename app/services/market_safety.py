from __future__ import annotations

from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import OddsSnapshot
from app.services.reconciliation_health import reconciliation_health
from app.services.source_registry import source_health_summary


PRIMARY_SOURCE = "live-score-api"


def _primary_source_status(
    source_health: dict[str, Any],
    *,
    primary_source: str = PRIMARY_SOURCE,
) -> tuple[str, dict[str, Any] | None]:
    for row in source_health.get("items", []):
        if row.get("slug") == primary_source:
            status = str(
                row.get("effective_health_status")
                or row.get("health_status")
                or "UNKNOWN"
            ).upper()

            return status, row

    return "UNKNOWN", None


def market_safety_status(
    session: Session,
    *,
    competition_id: int,
    season: int,
    source_stale_after_minutes: int = 180,
    reconciliation_stale_after_minutes: int = 180,
    primary_source: str = PRIMARY_SOURCE,
) -> dict[str, Any]:
    """Return one read-only operational market-safety view.

    This dashboard does not change the prediction policy and does not itself
    block or create predictions. Prediction-lock eligibility is taken from the
    reconciliation safety layer so the dashboard reflects the actual gate.

    Odds snapshots are intentionally deduplicated when prices do not change.
    Therefore snapshot age is informational and is not used as a hard market
    freshness gate.
    """

    sources = source_health_summary(
        session,
        stale_after_minutes=int(source_stale_after_minutes),
    )

    reconciliation = reconciliation_health(
        session,
        competition_id=int(competition_id),
        season=int(season),
        stale_after_minutes=int(
            reconciliation_stale_after_minutes
        ),
    )

    primary_status, primary_row = _primary_source_status(
        sources,
        primary_source=primary_source,
    )

    latest_odds = session.scalar(
        select(OddsSnapshot)
        .where(
            OddsSnapshot.competition_id == int(
                competition_id
            ),
            OddsSnapshot.season == int(season),
        )
        .order_by(
            OddsSnapshot.captured_at.desc(),
            OddsSnapshot.id.desc(),
        )
        .limit(1)
    )

    odds_count = session.scalar(
        select(func.count(OddsSnapshot.id)).where(
            OddsSnapshot.competition_id == int(
                competition_id
            ),
            OddsSnapshot.season == int(season),
        )
    ) or 0

    reconciliation_status = str(
        reconciliation.get(
            "operational_status",
            "UNINITIALIZED",
        )
    ).upper()

    prediction_lock_allowed = bool(
        reconciliation.get(
            "prediction_lock_allowed",
            False,
        )
    )

    reasons: list[str] = []

    if reconciliation_status == "BLOCKED":
        overall_status = "BLOCKED"
        reasons.append("RECONCILIATION_BLOCKED")

    else:
        degraded = False

        if reconciliation_status in {
            "DEGRADED",
            "STALE",
            "UNINITIALIZED",
        }:
            degraded = True
            reasons.append(
                f"RECONCILIATION_{reconciliation_status}"
            )

        if primary_status != "OK":
            degraded = True
            reasons.append(
                f"PRIMARY_SOURCE_{primary_status}"
            )

        overall_status = (
            "DEGRADED"
            if degraded
            else "READY"
        )

    if not reasons:
        reasons.append("ALL_REQUIRED_GATES_READY")

    return {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),

        "overall_status": overall_status,
        "reasons": reasons,

        # This mirrors the actual reconciliation prediction gate.
        # Source-health observations do not create a new blocking policy.
        "prediction_lock_allowed": prediction_lock_allowed,
        "prediction_gate_action": reconciliation.get(
            "action"
        ),

        "primary_source": {
            "slug": primary_source,
            "effective_health_status": primary_status,
            "registered": primary_row is not None,
            "last_checked_at": (
                primary_row.get("last_checked_at")
                if primary_row
                else None
            ),
            "last_success_at": (
                primary_row.get("last_success_at")
                if primary_row
                else None
            ),
            "consecutive_failures": (
                int(
                    primary_row.get(
                        "consecutive_failures",
                        0,
                    )
                    or 0
                )
                if primary_row
                else None
            ),
        },

        "source_health": {
            "sources": int(
                sources.get("sources", 0)
            ),
            "health_counts": dict(
                sources.get(
                    "health_counts",
                    {},
                )
            ),
            "stale_after_minutes": (
                source_stale_after_minutes
            ),
        },

        "reconciliation": {
            "operational_status": (
                reconciliation_status
            ),
            "quality_gate": (
                reconciliation.get(
                    "quality_gate"
                )
            ),
            "reported_quality_gate": (
                reconciliation.get(
                    "reported_quality_gate"
                )
            ),
            "freshness": (
                reconciliation.get(
                    "freshness"
                )
            ),
            "age_minutes": (
                reconciliation.get(
                    "age_minutes"
                )
            ),
            "stale_after_minutes": (
                reconciliation.get(
                    "stale_after_minutes"
                )
            ),
            "fixture_agreement_rate": (
                reconciliation.get(
                    "fixture_agreement_rate"
                )
            ),
            "issue_count": int(
                reconciliation.get(
                    "issue_count",
                    0,
                )
                or 0
            ),
            "conflict_count": int(
                reconciliation.get(
                    "conflict_count",
                    0,
                )
                or 0
            ),
            "warning_count": int(
                reconciliation.get(
                    "warning_count",
                    0,
                )
                or 0
            ),
            "last_checked_at": (
                reconciliation.get(
                    "last_checked_at"
                )
            ),
        },

        "odds_market": {
            "snapshots": int(odds_count),
            "latest_snapshot_at": (
                latest_odds.captured_at.isoformat()
                if latest_odds
                else None
            ),
            "freshness_policy": (
                "INFORMATIONAL_DEDUPLICATED_NOT_HARD_GATE"
            ),
        },

        "scheduler_module": (
            "python -m app.market_scheduler"
        ),

        "final_holdout_touched": False,
    }
