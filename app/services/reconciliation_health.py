from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy.orm import Session

from app.services.result_verification import composite_reconciliation_summary


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_checked_at(value: Any) -> datetime:
    parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    return _utc(parsed)


def reconciliation_health(
    session: Session,
    *,
    competition_id: int,
    season: int,
    stale_after_minutes: int = 180,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return the operational state of the reconciliation safety layer.

    This is a read-only observability view. It does not rewrite fixtures,
    reconciliation rows, odds history, predictions, or the historical holdout.
    """
    when = _utc(now or datetime.now(timezone.utc))
    stale_after = max(1, int(stale_after_minutes))

    summary = composite_reconciliation_summary(
        session,
        competition_id=int(competition_id),
        season=int(season),
    )

    reported_gate = str(
        summary.get("quality_gate") or "UNKNOWN"
    ).upper()

    effective_gate = reported_gate
    last_checked_at = summary.get("last_checked_at")

    age_minutes: float | None = None

    if reported_gate == "UNKNOWN":
        freshness = "NOT_INITIALIZED"

    elif not last_checked_at:
        freshness = "MISSING_TIMESTAMP"
        effective_gate = "STALE"

    else:
        try:
            checked_at = _parse_checked_at(last_checked_at)

            age_minutes = max(
                0.0,
                (when - checked_at).total_seconds() / 60.0,
            )

            if age_minutes > stale_after:
                freshness = "STALE"
                effective_gate = "STALE"
            else:
                freshness = "FRESH"

        except (TypeError, ValueError):
            freshness = "INVALID_TIMESTAMP"
            effective_gate = "STALE"

    if effective_gate == "PASS":
        operational_status = "HEALTHY"
        prediction_lock_allowed = True
        action = "ALLOW"

    elif effective_gate == "WARN":
        operational_status = "DEGRADED"
        prediction_lock_allowed = True
        action = "ALLOW_DEGRADED"

    elif effective_gate == "FAIL":
        operational_status = "BLOCKED"
        prediction_lock_allowed = False
        action = "BLOCK_NEW_PREDICTIONS"

    elif effective_gate == "STALE":
        operational_status = "STALE"
        prediction_lock_allowed = True
        action = "ALLOW_UNVERIFIED"

    else:
        operational_status = "UNINITIALIZED"
        prediction_lock_allowed = True
        action = "ALLOW_UNVERIFIED"

    counts = dict(summary.get("overall_counts") or {})

    total = int(summary.get("total") or 0)
    clean_count = int(counts.get("MATCH", 0))

    issue_count = max(
        0,
        total - clean_count,
    )

    conflict_count = int(
        counts.get("CONFLICT", 0)
    )

    warning_count = (
        int(counts.get("MISSING_PRIMARY", 0))
        + int(counts.get("MISSING_SECONDARY", 0))
        + int(counts.get("SOURCE_LAG", 0))
    )

    return {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),

        "operational_status": operational_status,
        "prediction_lock_allowed": prediction_lock_allowed,
        "action": action,

        "quality_gate": effective_gate,
        "reported_quality_gate": reported_gate,

        "freshness": freshness,
        "age_minutes": (
            round(age_minutes, 2)
            if age_minutes is not None
            else None
        ),
        "stale_after_minutes": stale_after,

        "fixture_agreement_rate": summary.get(
            "fixture_agreement_rate"
        ),

        "total": total,
        "clean_count": clean_count,
        "issue_count": issue_count,
        "conflict_count": conflict_count,
        "warning_count": warning_count,

        "overall_counts": counts,

        "primary_source": summary.get(
            "primary_source",
            "live-score-api",
        ),
        "secondary_source": summary.get(
            "secondary_source",
            "openfootball-json",
        ),

        "last_checked_at": last_checked_at,

        "scheduler_module": "python -m app.market_scheduler",

        "final_holdout_touched": False,
    }
