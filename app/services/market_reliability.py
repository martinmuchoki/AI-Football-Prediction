from __future__ import annotations

import asyncio
from collections import Counter
from datetime import datetime, timezone
from typing import Any

import httpx

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.models import (
    MarketSafetyEvent,
    MarketSafetyIncident,
    MarketSafetySlaAlert,
)

from app.services.market_safety_events import (
    list_market_safety_events,
)

from app.services.market_safety_incidents import (
    active_market_safety_incident,
    list_market_safety_incidents,
    market_safety_incident_metrics,
    process_market_safety_incident,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _aware(value: datetime) -> datetime:

    if value.tzinfo is None:
        return value.replace(
            tzinfo=timezone.utc
        )

    return value.astimezone(
        timezone.utc
    )


def _parse_datetime(value: Any) -> datetime:

    if isinstance(
        value,
        datetime,
    ):
        return _aware(value)

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

        return _aware(
            datetime.fromisoformat(
                raw
            )
        )

    return _utcnow()


def _age_minutes(
    opened_at: Any,
    *,
    now: datetime | None = None,
) -> float:

    current = _aware(
        now or _utcnow()
    )

    opened = _parse_datetime(
        opened_at
    )

    return max(
        0.0,
        (
            current - opened
        ).total_seconds()
        / 60.0,
    )


def _sla_alert_dict(
    row: MarketSafetySlaAlert,
) -> dict[str, Any]:

    return {
        "id": int(row.id),

        "incident_id": int(
            row.incident_id
        ),

        "competition_id": int(
            row.competition_id
        ),

        "season": int(
            row.season
        ),

        "stage": row.stage,
        "severity": row.severity,

        "market_status": (
            row.market_status
        ),

        "threshold_minutes": float(
            row.threshold_minutes
        ),

        "incident_age_minutes": float(
            row.incident_age_minutes
        ),

        "telegram_delivery_status": (
            row.telegram_delivery_status
        ),

        "telegram_delivery_attempts": int(
            row.telegram_delivery_attempts
            or 0
        ),

        "telegram_http_status": (
            row.telegram_http_status
        ),

        "telegram_last_error_type": (
            row.telegram_last_error_type
        ),

        "telegram_delivered_at": (
            row.telegram_delivered_at.isoformat()
            if row.telegram_delivered_at
            else None
        ),

        "created_at": (
            row.created_at.isoformat()
        ),

        "final_holdout_touched": False,
    }


def _due_sla_stage(
    incident: dict[str, Any],
    settings: Any,
    *,
    now: datetime | None = None,
) -> dict[str, Any] | None:

    if not incident:
        return None

    if str(
        incident.get(
            "status",
            "",
        )
    ).upper() != "OPEN":
        return None

    status = str(
        incident.get(
            "current_status",
            "DEGRADED",
        )
    ).upper()

    age = _age_minutes(
        incident["opened_at"],
        now=now,
    )

    if status == "BLOCKED":

        critical = float(
            getattr(
                settings,
                "incident_blocked_critical_minutes",
                60,
            )
        )

        warning = float(
            getattr(
                settings,
                "incident_blocked_warn_minutes",
                15,
            )
        )

        if age >= critical:
            return {
                "stage": "BLOCKED_CRITICAL",
                "severity": "CRITICAL",
                "threshold_minutes": critical,
                "incident_age_minutes": age,
            }

        if age >= warning:
            return {
                "stage": "BLOCKED_WARNING",
                "severity": "WARNING",
                "threshold_minutes": warning,
                "incident_age_minutes": age,
            }

        return None

    if status == "DEGRADED":

        critical = float(
            getattr(
                settings,
                "incident_degraded_critical_minutes",
                180,
            )
        )

        warning = float(
            getattr(
                settings,
                "incident_degraded_warn_minutes",
                60,
            )
        )

        if age >= critical:
            return {
                "stage": "DEGRADED_CRITICAL",
                "severity": "CRITICAL",
                "threshold_minutes": critical,
                "incident_age_minutes": age,
            }

        if age >= warning:
            return {
                "stage": "DEGRADED_WARNING",
                "severity": "WARNING",
                "threshold_minutes": warning,
                "incident_age_minutes": age,
            }

    return None


def evaluate_market_safety_sla(
    session: Session,
    settings: Any,
    *,
    incident: dict[str, Any] | None,
    now: datetime | None = None,
) -> dict[str, Any]:

    if not incident:
        return {
            "status": "NO_ACTIVE_INCIDENT",
            "alert": None,
            "new_alert": False,
            "final_holdout_touched": False,
        }

    due = _due_sla_stage(
        incident,
        settings,
        now=now,
    )

    if due is None:
        return {
            "status": "WITHIN_SLA",
            "alert": None,
            "new_alert": False,
            "final_holdout_touched": False,
        }

    existing = session.scalar(
        select(
            MarketSafetySlaAlert
        )
        .where(
            MarketSafetySlaAlert.incident_id
            == int(incident["id"]),

            MarketSafetySlaAlert.stage
            == due["stage"],
        )
        .limit(1)
    )

    if existing is not None:

        return {
            "status": "EXISTING_STAGE",
            "alert": _sla_alert_dict(
                existing
            ),
            "new_alert": False,
            "final_holdout_touched": False,
        }

    row = MarketSafetySlaAlert(
        incident_id=int(
            incident["id"]
        ),

        competition_id=int(
            incident["competition_id"]
        ),

        season=int(
            incident["season"]
        ),

        stage=due["stage"],
        severity=due["severity"],

        market_status=str(
            incident.get(
                "current_status",
                "UNKNOWN",
            )
        ).upper(),

        threshold_minutes=float(
            due["threshold_minutes"]
        ),

        incident_age_minutes=float(
            due["incident_age_minutes"]
        ),

        telegram_delivery_status="PENDING",
        telegram_delivery_attempts=0,

        updated_at=_utcnow(),
    )

    session.add(row)
    session.commit()
    session.refresh(row)

    return {
        "status": "SLA_STAGE_CREATED",
        "alert": _sla_alert_dict(
            row
        ),
        "new_alert": True,
        "final_holdout_touched": False,
    }


def _sla_telegram_message(
    alert: dict[str, Any],
    incident: dict[str, Any],
) -> str:

    severity = str(
        alert["severity"]
    ).upper()

    icon = (
        "🚨"
        if severity == "CRITICAL"
        else "⚠️"
    )

    message = (
        f"{icon} MDRN SportsQ SLA Escalation\n"
        f"\n"
        f"Stage: {alert['stage']}\n"
        f"Severity: {severity}\n"
        f"Incident ID: {incident['id']}\n"
        f"Market Status: {incident['current_status']}\n"
        f"Highest Severity: {incident['highest_severity']}\n"
        f"\n"
        f"Incident Age: "
        f"{alert['incident_age_minutes']:.1f} minutes\n"
        f"SLA Threshold: "
        f"{alert['threshold_minutes']:.1f} minutes\n"
        f"\n"
        f"Competition: {incident['competition_id']}\n"
        f"Season: {incident['season']}\n"
        f"Escalations: {incident.get('escalation_count', 0)}\n"
        f"\n"
        f"Operational attention required."
    )

    return message[:3900]


async def deliver_market_safety_sla_alert(
    session: Session,
    settings: Any,
    *,
    alert: dict[str, Any],
    incident: dict[str, Any],
) -> dict[str, Any]:

    row = session.get(
        MarketSafetySlaAlert,
        int(alert["id"]),
    )

    if row is None:
        return {
            "status": "MISSING_ALERT",
            "final_holdout_touched": False,
        }

    if (
        row.telegram_delivery_status
        == "DELIVERED"
    ):
        result = _sla_alert_dict(
            row
        )

        result["status"] = "existing"
        return result

    enabled = bool(
        getattr(
            settings,
            "safety_alert_telegram_enabled",
            False,
        )
    )

    token = str(
        getattr(
            settings,
            "safety_alert_telegram_bot_token",
            "",
        )
        or ""
    ).strip()

    chat_id = str(
        getattr(
            settings,
            "safety_alert_telegram_chat_id",
            "",
        )
        or ""
    ).strip()

    if (
        not enabled
        or not token
        or not chat_id
    ):
        row.telegram_delivery_status = (
            "CONFIG_REQUIRED"
        )

        row.updated_at = _utcnow()

        session.commit()
        session.refresh(row)

        return _sla_alert_dict(
            row
        )

    timeout_seconds = max(
        1.0,
        float(
            getattr(
                settings,
                "safety_alert_timeout_seconds",
                10.0,
            )
        ),
    )

    max_attempts = max(
        1,
        min(
            int(
                getattr(
                    settings,
                    "safety_alert_max_retries",
                    3,
                )
            ),
            5,
        ),
    )

    url = (
        "https://api.telegram.org/"
        f"bot{token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": _sla_telegram_message(
            alert,
            incident,
        ),
    }

    last_error = None
    last_http_status = None

    for attempt in range(
        1,
        max_attempts + 1,
    ):

        row.telegram_delivery_attempts = (
            int(
                row.telegram_delivery_attempts
                or 0
            )
            + 1
        )

        try:

            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=False,
            ) as client:

                response = await client.post(
                    url,
                    json=payload,
                )

            last_http_status = int(
                response.status_code
            )

            ok = False

            if (
                200
                <= response.status_code
                < 300
            ):
                try:
                    body = response.json()

                    ok = bool(
                        body.get(
                            "ok",
                            False,
                        )
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    ok = False

            if ok:

                row.telegram_delivery_status = (
                    "DELIVERED"
                )

                row.telegram_http_status = (
                    response.status_code
                )

                row.telegram_last_error_type = (
                    None
                )

                row.telegram_delivered_at = (
                    _utcnow()
                )

                row.updated_at = _utcnow()

                session.commit()
                session.refresh(row)

                return _sla_alert_dict(
                    row
                )

            last_error = (
                "TELEGRAM_API_REJECTED"
                if (
                    200
                    <= response.status_code
                    < 300
                )
                else "HTTP_STATUS"
            )

            retryable = (
                response.status_code
                in {
                    408,
                    429,
                }
                or response.status_code
                >= 500
            )

            if not retryable:
                break

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:

            last_error = (
                type(exc).__name__
            )

        except httpx.HTTPError as exc:

            last_error = (
                type(exc).__name__
            )

        if attempt < max_attempts:

            await asyncio.sleep(
                min(
                    0.5
                    * (
                        2
                        ** (
                            attempt - 1
                        )
                    ),
                    2.0,
                )
            )

    row.telegram_delivery_status = (
        "FAILED"
    )

    row.telegram_http_status = (
        last_http_status
    )

    row.telegram_last_error_type = (
        str(
            last_error
            or "DELIVERY_FAILED"
        )[:120]
    )

    row.updated_at = _utcnow()

    session.commit()
    session.refresh(row)

    return _sla_alert_dict(
        row
    )


async def evaluate_and_dispatch_market_safety_sla(
    session: Session,
    settings: Any,
    *,
    incident: dict[str, Any] | None,
) -> dict[str, Any]:

    evaluation = evaluate_market_safety_sla(
        session,
        settings,
        incident=incident,
    )

    alert = evaluation.get(
        "alert"
    )

    if not alert:
        return evaluation

    delivery = await deliver_market_safety_sla_alert(
        session,
        settings,
        alert=alert,
        incident=incident,
    )

    return {
        **evaluation,
        "delivery": delivery,
        "final_holdout_touched": False,
    }


def recover_incident_state_from_history(
    session: Session,
    *,
    competition_id: int,
    season: int,
) -> dict[str, Any]:
    """Repair a crash gap between safety-event persistence and incident tracking."""

    history = list_market_safety_events(
        session,
        competition_id=int(
            competition_id
        ),
        season=int(
            season
        ),
        limit=1000,
        transitions_only=False,
    )

    if not history:
        return {
            "status": "NO_SAFETY_HISTORY",
            "processed": 0,
            "actions": [],
            "final_holdout_touched": False,
        }

    latest = history[0]

    active = active_market_safety_incident(
        session,
        competition_id=int(
            competition_id
        ),
        season=int(
            season
        ),
    )

    incidents = list_market_safety_incidents(
        session,
        competition_id=int(
            competition_id
        ),
        season=int(
            season
        ),
        limit=1000,
    )

    last_processed_id = 0

    if incidents:
        last_processed_id = max(
            int(
                row.get(
                    "last_event_id",
                    0,
                )
                or 0
            )
            for row in incidents
        )

    latest_status = str(
        latest["overall_status"]
    ).upper()

    needs_repair = False

    if active["active"]:

        active_last = int(
            active["incident"][
                "last_event_id"
            ]
        )

        needs_repair = (
            int(latest["id"])
            > active_last
        )

        last_processed_id = max(
            last_processed_id,
            active_last,
        )

    elif latest_status in {
        "DEGRADED",
        "BLOCKED",
    }:

        needs_repair = True

    if not needs_repair:

        return {
            "status": "CONSISTENT",
            "processed": 0,
            "actions": [],
            "latest_safety_event_id": int(
                latest["id"]
            ),
            "final_holdout_touched": False,
        }

    pending = [
        event
        for event in reversed(
            history
        )
        if int(
            event["id"]
        ) > last_processed_id
    ]

    actions = []

    for event in pending:

        result = process_market_safety_incident(
            session,
            event=event,
        )

        actions.append(
            {
                "event_id": int(
                    event["id"]
                ),
                "status": (
                    event[
                        "overall_status"
                    ]
                ),
                "action": (
                    result["action"]
                ),
            }
        )

    return {
        "status": "REPAIRED",
        "processed": len(
            pending
        ),
        "actions": actions,
        "latest_safety_event_id": int(
            latest["id"]
        ),
        "final_holdout_touched": False,
    }


def list_market_safety_sla_alerts(
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int = 100,
) -> list[dict[str, Any]]:

    stmt = (
        select(
            MarketSafetySlaAlert
        )
        .where(
            MarketSafetySlaAlert.competition_id
            == int(
                competition_id
            ),

            MarketSafetySlaAlert.season
            == int(
                season
            ),
        )
        .order_by(
            MarketSafetySlaAlert.created_at.desc(),
            MarketSafetySlaAlert.id.desc(),
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
        _sla_alert_dict(row)
        for row in session.scalars(
            stmt
        ).all()
    ]


def database_integrity_report(
    session: Session,
) -> dict[str, Any]:

    checks: list[dict[str, Any]] = []
    errors: list[str] = []

    try:

        session.execute(
            text(
                "SELECT 1"
            )
        )

        checks.append(
            {
                "check": "DATABASE_CONNECTIVITY",
                "status": "PASS",
            }
        )

    except Exception as exc:

        errors.append(
            "DATABASE_CONNECTIVITY"
        )

        checks.append(
            {
                "check": "DATABASE_CONNECTIVITY",
                "status": "FAIL",
                "error_type": (
                    type(exc).__name__
                ),
            }
        )

    dialect = (
        session.get_bind()
        .dialect.name
    )

    if dialect == "sqlite":

        try:

            result = session.execute(
                text(
                    "PRAGMA quick_check"
                )
            ).scalar()

            sqlite_ok = (
                str(result).lower()
                == "ok"
            )

            checks.append(
                {
                    "check": "SQLITE_QUICK_CHECK",
                    "status": (
                        "PASS"
                        if sqlite_ok
                        else "FAIL"
                    ),
                }
            )

            if not sqlite_ok:
                errors.append(
                    "SQLITE_QUICK_CHECK"
                )

        except Exception as exc:

            errors.append(
                "SQLITE_QUICK_CHECK"
            )

            checks.append(
                {
                    "check": "SQLITE_QUICK_CHECK",
                    "status": "FAIL",
                    "error_type": (
                        type(exc).__name__
                    ),
                }
            )

    open_incidents = list(
        session.scalars(
            select(
                MarketSafetyIncident
            )
            .where(
                MarketSafetyIncident.status
                == "OPEN"
            )
        ).all()
    )

    open_keys = Counter(
        (
            int(row.competition_id),
            int(row.season),
        )
        for row in open_incidents
    )

    duplicate_open = [
        {
            "competition_id": key[0],
            "season": key[1],
            "open_incidents": count,
        }
        for key, count
        in open_keys.items()
        if count > 1
    ]

    if duplicate_open:

        errors.append(
            "MULTIPLE_OPEN_INCIDENTS"
        )

        checks.append(
            {
                "check": "SINGLE_ACTIVE_INCIDENT",
                "status": "FAIL",
                "details": duplicate_open,
            }
        )

    else:

        checks.append(
            {
                "check": "SINGLE_ACTIVE_INCIDENT",
                "status": "PASS",
            }
        )

    incident_ids = set(
        int(value)
        for value in session.scalars(
            select(
                MarketSafetyIncident.id
            )
        ).all()
    )

    sla_rows = list(
        session.scalars(
            select(
                MarketSafetySlaAlert
            )
        ).all()
    )

    orphan_sla = [
        int(row.id)
        for row in sla_rows
        if int(
            row.incident_id
        )
        not in incident_ids
    ]

    if orphan_sla:

        errors.append(
            "ORPHAN_SLA_ALERTS"
        )

        checks.append(
            {
                "check": "SLA_INCIDENT_REFERENCES",
                "status": "FAIL",
                "orphan_ids": orphan_sla,
            }
        )

    else:

        checks.append(
            {
                "check": "SLA_INCIDENT_REFERENCES",
                "status": "PASS",
            }
        )

    return {
        "status": (
            "PASS"
            if not errors
            else "FAIL"
        ),

        "dialect": dialect,

        "checks": checks,
        "errors": errors,

        "prediction_tables_modified": False,
        "final_holdout_touched": False,
    }


def reliability_summary(
    session: Session,
    settings: Any,
    *,
    competition_id: int,
    season: int,
    now: datetime | None = None,
) -> dict[str, Any]:

    current_time = _aware(
        now or _utcnow()
    )

    latest_event = session.scalar(
        select(
            MarketSafetyEvent
        )
        .where(
            MarketSafetyEvent.competition_id
            == int(
                competition_id
            ),

            MarketSafetyEvent.season
            == int(
                season
            ),
        )
        .order_by(
            MarketSafetyEvent.observed_at.desc(),
            MarketSafetyEvent.id.desc(),
        )
        .limit(1)
    )

    if latest_event is None:

        heartbeat = {
            "status": "UNINITIALIZED",
            "age_minutes": None,
            "latest_event_id": None,
        }

        safety_status = (
            "UNKNOWN"
        )

        primary_source_status = (
            "UNKNOWN"
        )

    else:

        observed = _aware(
            latest_event.observed_at
        )

        age = max(
            0.0,
            (
                current_time - observed
            ).total_seconds()
            / 60.0,
        )

        stale_after = float(
            getattr(
                settings,
                "operations_heartbeat_stale_minutes",
                120,
            )
        )

        heartbeat = {
            "status": (
                "STALE"
                if age
                > stale_after
                else "FRESH"
            ),

            "age_minutes": age,

            "stale_after_minutes": (
                stale_after
            ),

            "latest_event_id": int(
                latest_event.id
            ),

            "observed_at": (
                latest_event.observed_at.isoformat()
            ),
        }

        safety_status = str(
            latest_event.overall_status
        ).upper()

        primary_source_status = str(
            latest_event.primary_source_status
            or "UNKNOWN"
        ).upper()

    active = active_market_safety_incident(
        session,
        competition_id=int(
            competition_id
        ),
        season=int(
            season
        ),
    )

    metrics = market_safety_incident_metrics(
        session,
        competition_id=int(
            competition_id
        ),
        season=int(
            season
        ),
    )

    sla_alerts = (
        list_market_safety_sla_alerts(
            session,
            competition_id=int(
                competition_id
            ),
            season=int(
                season
            ),
            limit=1000,
        )
    )

    due_stage = None

    if active["active"]:

        due_stage = _due_sla_stage(
            active["incident"],
            settings,
            now=current_time,
        )

    integrity = database_integrity_report(
        session
    )

    if integrity["status"] == "FAIL":

        overall = "BLOCKED"

        reason = (
            "DATABASE_INTEGRITY_FAILURE"
        )

    elif safety_status == "BLOCKED":

        overall = "BLOCKED"

        reason = (
            "MARKET_SAFETY_BLOCKED"
        )

    elif (
        heartbeat["status"]
        == "STALE"
    ):

        overall = "DEGRADED"

        reason = (
            "OPERATIONS_HEARTBEAT_STALE"
        )

    elif (
        safety_status
        == "DEGRADED"
        or active["active"]
        or primary_source_status
        != "OK"
    ):

        overall = "DEGRADED"

        reason = (
            "OPERATIONAL_DEGRADATION"
        )

    else:

        overall = "READY"

        reason = (
            "ALL_RELIABILITY_GATES_READY"
        )

    return {
        "competition_id": int(
            competition_id
        ),

        "season": int(
            season
        ),

        "overall_reliability_status": (
            overall
        ),

        "reason": reason,

        "market_safety_status": (
            safety_status
        ),

        "primary_source_status": (
            primary_source_status
        ),

        "scheduler_heartbeat": (
            heartbeat
        ),

        "active_incident": (
            active
        ),

        "incident_metrics": (
            metrics
        ),

        "sla": {
            "current_due_stage": due_stage,

            "alert_count": len(
                sla_alerts
            ),

            "latest_alert": (
                sla_alerts[0]
                if sla_alerts
                else None
            ),
        },

        "database_integrity": (
            integrity
        ),

        "prediction_lock_policy_changed": False,
        "existing_prediction_locks_immutable": True,
        "final_holdout_touched": False,
    }
