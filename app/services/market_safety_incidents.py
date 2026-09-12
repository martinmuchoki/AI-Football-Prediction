from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketSafetyIncident


SEVERITY = {
    "READY": 0,
    "DEGRADED": 1,
    "BLOCKED": 2,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(
    value: datetime,
) -> datetime:

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _parse_datetime(
    value: Any,
) -> datetime:

    if isinstance(
        value,
        datetime,
    ):
        return _aware(
            value
        )

    if isinstance(
        value,
        str,
    ):
        raw = value.strip()

        if raw.endswith("Z"):
            raw = (
                raw[:-1]
                + "+00:00"
            )

        parsed = datetime.fromisoformat(
            raw
        )

        return _aware(
            parsed
        )

    return _utcnow()


def _seconds_between(
    start: datetime,
    end: datetime,
) -> float:

    return max(
        0.0,
        (
            _aware(end)
            - _aware(start)
        ).total_seconds(),
    )


def _incident_dict(
    row: MarketSafetyIncident,
) -> dict[str, Any]:

    now = _utcnow()

    effective_end = (
        row.closed_at
        if row.closed_at
        else now
    )

    current_duration = (
        _seconds_between(
            row.opened_at,
            effective_end,
        )
    )

    return {
        "id": int(row.id),

        "competition_id": int(
            row.competition_id
        ),

        "season": int(
            row.season
        ),

        "status": row.status,

        "opened_event_id": int(
            row.opened_event_id
        ),

        "last_event_id": int(
            row.last_event_id
        ),

        "closed_event_id": (
            int(row.closed_event_id)
            if row.closed_event_id
            is not None
            else None
        ),

        "opened_status": (
            row.opened_status
        ),

        "current_status": (
            row.current_status
        ),

        "highest_severity": (
            row.highest_severity
        ),

        "escalation_count": int(
            row.escalation_count
            or 0
        ),

        "opened_at": (
            row.opened_at.isoformat()
        ),

        "last_observed_at": (
            row.last_observed_at.isoformat()
        ),

        "escalated_at": (
            row.escalated_at.isoformat()
            if row.escalated_at
            else None
        ),

        "closed_at": (
            row.closed_at.isoformat()
            if row.closed_at
            else None
        ),

        "duration_seconds": (
            float(row.duration_seconds)
            if row.duration_seconds
            is not None
            else None
        ),

        "current_duration_seconds": (
            current_duration
        ),

        "current_duration_minutes": (
            current_duration
            / 60.0
        ),

        "final_holdout_touched": False,
    }


def _active_row(
    session: Session,
    *,
    competition_id: int,
    season: int,
) -> MarketSafetyIncident | None:

    return session.scalar(
        select(
            MarketSafetyIncident
        )
        .where(
            MarketSafetyIncident.competition_id
            == int(competition_id),

            MarketSafetyIncident.season
            == int(season),

            MarketSafetyIncident.status
            == "OPEN",
        )
        .order_by(
            MarketSafetyIncident.opened_at.desc(),
            MarketSafetyIncident.id.desc(),
        )
        .limit(1)
    )


def _closed_by_event(
    session: Session,
    *,
    event_id: int,
) -> MarketSafetyIncident | None:

    return session.scalar(
        select(
            MarketSafetyIncident
        )
        .where(
            MarketSafetyIncident.closed_event_id
            == int(event_id)
        )
        .limit(1)
    )


def process_market_safety_incident(
    session: Session,
    *,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Apply one persisted MarketSafetyEvent to incident lifecycle.

    DEGRADED/BLOCKED:
        open or update active incident.

    Higher severity:
        escalate the same incident.

    READY:
        close active incident.

    The same event can safely be processed again.
    """

    event_id = int(
        event["id"]
    )

    competition_id = int(
        event["competition_id"]
    )

    season = int(
        event["season"]
    )

    market_status = str(
        event.get(
            "overall_status",
            "DEGRADED",
        )
    ).upper()

    observed_at = _parse_datetime(
        event.get(
            "observed_at"
        )
    )

    active = _active_row(
        session,
        competition_id=competition_id,
        season=season,
    )

    if active is not None:
        if int(
            active.last_event_id
        ) == event_id:

            return {
                "action": "DUPLICATE",
                "incident": _incident_dict(
                    active
                ),
                "final_holdout_touched": False,
            }

    if market_status in {
        "DEGRADED",
        "BLOCKED",
    }:

        if active is None:

            row = MarketSafetyIncident(
                competition_id=competition_id,
                season=season,

                status="OPEN",

                opened_event_id=event_id,
                last_event_id=event_id,

                opened_status=market_status,
                current_status=market_status,
                highest_severity=market_status,

                escalation_count=0,

                opened_at=observed_at,
                last_observed_at=observed_at,

                updated_at=_utcnow(),
            )

            session.add(row)
            session.commit()
            session.refresh(row)

            return {
                "action": "OPENED",
                "incident": _incident_dict(
                    row
                ),
                "final_holdout_touched": False,
            }

        previous_highest = str(
            active.highest_severity
        ).upper()

        active.last_event_id = event_id
        active.current_status = (
            market_status
        )

        active.last_observed_at = (
            observed_at
        )

        action = "UPDATED"

        if (
            SEVERITY.get(
                market_status,
                0,
            )
            >
            SEVERITY.get(
                previous_highest,
                0,
            )
        ):
            active.highest_severity = (
                market_status
            )

            active.escalation_count = int(
                active.escalation_count
                or 0
            ) + 1

            active.escalated_at = (
                observed_at
            )

            action = "ESCALATED"

        active.updated_at = (
            _utcnow()
        )

        session.commit()
        session.refresh(active)

        return {
            "action": action,
            "incident": _incident_dict(
                active
            ),
            "final_holdout_touched": False,
        }

    if market_status == "READY":

        duplicate_closed = (
            _closed_by_event(
                session,
                event_id=event_id,
            )
        )

        if duplicate_closed is not None:

            return {
                "action": "DUPLICATE",
                "incident": _incident_dict(
                    duplicate_closed
                ),
                "final_holdout_touched": False,
            }

        if active is None:

            return {
                "action": "NONE",
                "incident": None,
                "final_holdout_touched": False,
            }

        active.status = "CLOSED"

        active.current_status = (
            "READY"
        )

        active.last_event_id = (
            event_id
        )

        active.closed_event_id = (
            event_id
        )

        active.last_observed_at = (
            observed_at
        )

        active.closed_at = (
            observed_at
        )

        active.duration_seconds = (
            _seconds_between(
                active.opened_at,
                observed_at,
            )
        )

        active.updated_at = (
            _utcnow()
        )

        session.commit()
        session.refresh(active)

        return {
            "action": "CLOSED",
            "incident": _incident_dict(
                active
            ),
            "final_holdout_touched": False,
        }

    return {
        "action": "IGNORED",
        "incident": (
            _incident_dict(active)
            if active
            else None
        ),
        "final_holdout_touched": False,
    }


def active_market_safety_incident(
    session: Session,
    *,
    competition_id: int,
    season: int,
) -> dict[str, Any]:

    row = _active_row(
        session,
        competition_id=competition_id,
        season=season,
    )

    return {
        "competition_id": int(
            competition_id
        ),

        "season": int(
            season
        ),

        "active": (
            row is not None
        ),

        "incident": (
            _incident_dict(row)
            if row
            else None
        ),

        "final_holdout_touched": False,
    }


def list_market_safety_incidents(
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int = 100,
) -> list[dict[str, Any]]:

    stmt = (
        select(
            MarketSafetyIncident
        )
        .where(
            MarketSafetyIncident.competition_id
            == int(competition_id),

            MarketSafetyIncident.season
            == int(season),
        )
        .order_by(
            MarketSafetyIncident.opened_at.desc(),
            MarketSafetyIncident.id.desc(),
        )
        .limit(
            max(
                1,
                min(
                    int(limit),
                    1000,
                ),
            )
        )
    )

    return [
        _incident_dict(row)
        for row
        in session.scalars(
            stmt
        ).all()
    ]


def market_safety_incident_metrics(
    session: Session,
    *,
    competition_id: int,
    season: int,
) -> dict[str, Any]:

    rows = list(
        session.scalars(
            select(
                MarketSafetyIncident
            )
            .where(
                MarketSafetyIncident.competition_id
                == int(competition_id),

                MarketSafetyIncident.season
                == int(season),
            )
        ).all()
    )

    closed = [
        row
        for row in rows
        if row.status == "CLOSED"
        and row.duration_seconds
        is not None
    ]

    durations = [
        float(
            row.duration_seconds
        )
        for row in closed
    ]

    mttr = (
        sum(durations)
        / len(durations)
        if durations
        else None
    )

    longest = (
        max(durations)
        if durations
        else None
    )

    active_count = sum(
        1
        for row in rows
        if row.status == "OPEN"
    )

    escalated_count = sum(
        1
        for row in rows
        if int(
            row.escalation_count
            or 0
        ) > 0
    )

    return {
        "competition_id": int(
            competition_id
        ),

        "season": int(
            season
        ),

        "incident_count": len(rows),

        "active_incidents": (
            active_count
        ),

        "closed_incidents": (
            len(closed)
        ),

        "escalated_incidents": (
            escalated_count
        ),

        "mttr_seconds": mttr,

        "mttr_minutes": (
            mttr / 60.0
            if mttr is not None
            else None
        ),

        "longest_recovery_seconds": (
            longest
        ),

        "longest_recovery_minutes": (
            longest / 60.0
            if longest is not None
            else None
        ),

        "final_holdout_touched": False,
    }
