from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketSafetyEvent


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _row_dict(
    row: MarketSafetyEvent,
) -> dict[str, Any]:
    try:
        reasons = json.loads(
            row.reasons_json or "[]"
        )
    except (TypeError, ValueError):
        reasons = []

    return {
        "id": int(row.id),
        "competition_id": int(row.competition_id),
        "season": int(row.season),

        "overall_status": row.overall_status,

        "prediction_lock_allowed": bool(
            row.prediction_lock_allowed
        ),

        "prediction_gate_action": (
            row.prediction_gate_action
        ),

        "reasons": reasons,

        "primary_source_status": (
            row.primary_source_status
        ),

        "reconciliation_status": (
            row.reconciliation_status
        ),

        "quality_gate": row.quality_gate,

        "reconciliation_freshness": (
            row.reconciliation_freshness
        ),

        "fixture_agreement_rate": (
            row.fixture_agreement_rate
        ),

        "issue_count": int(
            row.issue_count or 0
        ),

        "conflict_count": int(
            row.conflict_count or 0
        ),

        "warning_count": int(
            row.warning_count or 0
        ),

        "odds_snapshots": int(
            row.odds_snapshots or 0
        ),

        "previous_status": row.previous_status,
        "transition": row.transition,
        "alert_type": row.alert_type,

        "is_transition": (
            row.transition is not None
        ),

        "observed_at": (
            row.observed_at.isoformat()
        ),

        "final_holdout_touched": False,
    }


def _alert_type(
    previous_status: str | None,
    current_status: str,
) -> str | None:

    if not previous_status:
        return None

    if previous_status == current_status:
        return None

    if current_status == "BLOCKED":
        return "BLOCKED"

    if current_status == "DEGRADED":
        return "DEGRADED"

    if current_status == "READY":
        return "RECOVERY"

    return "STATE_CHANGE"


def record_market_safety_event(
    session: Session,
    *,
    snapshot: dict[str, Any],
    observed_at: datetime | None = None,
) -> dict[str, Any]:

    competition_id = int(
        snapshot["competition_id"]
    )

    season = int(
        snapshot["season"]
    )

    current_status = str(
        snapshot.get(
            "overall_status",
            "DEGRADED",
        )
    ).upper()

    previous = session.scalar(
        select(MarketSafetyEvent)
        .where(
            MarketSafetyEvent.competition_id
            == competition_id,
            MarketSafetyEvent.season
            == season,
        )
        .order_by(
            MarketSafetyEvent.observed_at.desc(),
            MarketSafetyEvent.id.desc(),
        )
        .limit(1)
    )

    previous_status = (
        previous.overall_status
        if previous
        else None
    )

    transition = None

    if (
        previous_status
        and previous_status != current_status
    ):
        transition = (
            f"{previous_status}->{current_status}"
        )

    reconciliation = dict(
        snapshot.get(
            "reconciliation",
            {},
        )
        or {}
    )

    primary = dict(
        snapshot.get(
            "primary_source",
            {},
        )
        or {}
    )

    odds = dict(
        snapshot.get(
            "odds_market",
            {},
        )
        or {}
    )

    row = MarketSafetyEvent(
        competition_id=competition_id,
        season=season,

        overall_status=current_status,

        prediction_lock_allowed=bool(
            snapshot.get(
                "prediction_lock_allowed",
                True,
            )
        ),

        prediction_gate_action=(
            snapshot.get(
                "prediction_gate_action"
            )
        ),

        reasons_json=json.dumps(
            list(
                snapshot.get(
                    "reasons",
                    [],
                )
                or []
            ),
            separators=(",", ":"),
        ),

        primary_source_status=str(
            primary.get(
                "effective_health_status",
                "UNKNOWN",
            )
        ).upper(),

        reconciliation_status=str(
            reconciliation.get(
                "operational_status",
                "UNINITIALIZED",
            )
        ).upper(),

        quality_gate=(
            reconciliation.get(
                "quality_gate"
            )
        ),

        reconciliation_freshness=(
            reconciliation.get(
                "freshness"
            )
        ),

        fixture_agreement_rate=(
            reconciliation.get(
                "fixture_agreement_rate"
            )
        ),

        issue_count=int(
            reconciliation.get(
                "issue_count",
                0,
            )
            or 0
        ),

        conflict_count=int(
            reconciliation.get(
                "conflict_count",
                0,
            )
            or 0
        ),

        warning_count=int(
            reconciliation.get(
                "warning_count",
                0,
            )
            or 0
        ),

        odds_snapshots=int(
            odds.get(
                "snapshots",
                0,
            )
            or 0
        ),

        previous_status=previous_status,
        transition=transition,

        alert_type=_alert_type(
            previous_status,
            current_status,
        ),

        observed_at=(
            observed_at
            or _utcnow()
        ),
    )

    session.add(row)
    session.commit()
    session.refresh(row)

    return _row_dict(row)


def list_market_safety_events(
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int = 100,
    transitions_only: bool = False,
) -> list[dict[str, Any]]:

    stmt = select(
        MarketSafetyEvent
    ).where(
        MarketSafetyEvent.competition_id
        == int(competition_id),

        MarketSafetyEvent.season
        == int(season),
    )

    if transitions_only:
        stmt = stmt.where(
            MarketSafetyEvent.transition.is_not(
                None
            )
        )

    stmt = stmt.order_by(
        MarketSafetyEvent.observed_at.desc(),
        MarketSafetyEvent.id.desc(),
    ).limit(
        max(
            1,
            min(
                int(limit),
                1000,
            ),
        )
    )

    rows = list(
        session.scalars(stmt).all()
    )

    return [
        _row_dict(row)
        for row in rows
    ]
