from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import MarketSafetyAlertDelivery


DELIVERABLE_ALERT_TYPES = {
    "BLOCKED",
    "DEGRADED",
    "RECOVERY",
    "STATE_CHANGE",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _delivery_dict(
    row: MarketSafetyAlertDelivery,
) -> dict[str, Any]:

    return {
        "id": int(row.id),
        "event_id": int(row.event_id),

        "competition_id": int(
            row.competition_id
        ),

        "season": int(row.season),

        "alert_type": row.alert_type,
        "transition": row.transition,
        "channel": row.channel,

        "delivery_status": (
            row.delivery_status
        ),

        "attempts": int(
            row.attempts or 0
        ),

        "http_status": row.http_status,
        "error_type": row.error_type,

        "delivered_at": (
            row.delivered_at.isoformat()
            if row.delivered_at
            else None
        ),

        "created_at": (
            row.created_at.isoformat()
        ),

        "final_holdout_touched": False,
    }


def _existing_delivery(
    session: Session,
    *,
    event_id: int,
    channel: str = "webhook",
) -> MarketSafetyAlertDelivery | None:

    return session.scalar(
        select(MarketSafetyAlertDelivery)
        .where(
            MarketSafetyAlertDelivery.event_id
            == int(event_id),

            MarketSafetyAlertDelivery.channel
            == str(channel),
        )
        .limit(1)
    )


def _store_delivery(
    session: Session,
    *,
    event: dict[str, Any],
    channel: str,
    status: str,
    attempts: int,
    http_status: int | None = None,
    error_type: str | None = None,
    delivered_at: datetime | None = None,
) -> dict[str, Any]:

    existing = _existing_delivery(
        session,
        event_id=int(event["id"]),
        channel=channel,
    )

    if existing is not None:
        return _delivery_dict(existing)

    row = MarketSafetyAlertDelivery(
        event_id=int(event["id"]),

        competition_id=int(
            event["competition_id"]
        ),

        season=int(
            event["season"]
        ),

        alert_type=str(
            event["alert_type"]
        ),

        transition=event.get(
            "transition"
        ),

        channel=str(channel),

        delivery_status=status,
        attempts=int(attempts),

        http_status=http_status,

        error_type=(
            str(error_type)[:120]
            if error_type
            else None
        ),

        delivered_at=delivered_at,
    )

    session.add(row)
    session.commit()
    session.refresh(row)

    return _delivery_dict(row)


def build_safety_alert_payload(
    event: dict[str, Any],
) -> dict[str, Any]:

    return {
        "brand": "MDRN SportsQ",
        "type": "market_safety_transition",

        "alert_type": event.get(
            "alert_type"
        ),

        "transition": event.get(
            "transition"
        ),

        "previous_status": event.get(
            "previous_status"
        ),

        "overall_status": event.get(
            "overall_status"
        ),

        "competition_id": event.get(
            "competition_id"
        ),

        "season": event.get(
            "season"
        ),

        "prediction_lock_allowed": event.get(
            "prediction_lock_allowed"
        ),

        "prediction_gate_action": event.get(
            "prediction_gate_action"
        ),

        "reasons": list(
            event.get(
                "reasons",
                [],
            )
            or []
        ),

        "primary_source_status": event.get(
            "primary_source_status"
        ),

        "reconciliation_status": event.get(
            "reconciliation_status"
        ),

        "quality_gate": event.get(
            "quality_gate"
        ),

        "reconciliation_freshness": event.get(
            "reconciliation_freshness"
        ),

        "observed_at": event.get(
            "observed_at"
        ),

        "final_holdout_touched": False,
    }


def build_telegram_message(
    event: dict[str, Any],
) -> str:

    alert_type = str(
        event.get(
            "alert_type",
            "STATE_CHANGE",
        )
    ).upper()

    icons = {
        "BLOCKED": "🚨",
        "DEGRADED": "⚠️",
        "RECOVERY": "✅",
        "STATE_CHANGE": "ℹ️",
    }

    icon = icons.get(
        alert_type,
        "ℹ️",
    )

    reasons = list(
        event.get(
            "reasons",
            [],
        )
        or []
    )

    reason_text = (
        ", ".join(
            str(reason)
            for reason in reasons
        )
        if reasons
        else "None"
    )

    prediction_allowed = bool(
        event.get(
            "prediction_lock_allowed",
            True,
        )
    )

    incident_state = dict(
        event.get(
            "incident",
            {},
        )
        or {}
    )

    incident = dict(
        incident_state.get(
            "incident",
            {},
        )
        or {}
    )

    incident_text = ""

    if incident:
        incident_text = (
            f"Incident ID: {incident.get('id')}\n"
            f"Incident Action: "
            f"{incident_state.get('action') or 'UPDATED'}\n"
            f"Incident Status: "
            f"{incident.get('status') or 'UNKNOWN'}\n"
            f"Highest Severity: "
            f"{incident.get('highest_severity') or 'UNKNOWN'}\n"
            f"Escalations: "
            f"{incident.get('escalation_count', 0)}\n"
            f"\n"
        )

    message = (
        f"{icon} MDRN SportsQ Safety Alert\n"
        f"\n"
        f"Alert: {alert_type}\n"
        f"Transition: {event.get('transition') or 'N/A'}\n"
        f"Market Status: {event.get('overall_status') or 'UNKNOWN'}\n"
        f"Competition: {event.get('competition_id')}\n"
        f"Season: {event.get('season')}\n"
        f"\n"
        f"Prediction Locking: "
        f"{'ALLOWED' if prediction_allowed else 'BLOCKED'}\n"
        f"Gate Action: "
        f"{event.get('prediction_gate_action') or 'UNKNOWN'}\n"
        f"\n"
        f"Primary Source: "
        f"{event.get('primary_source_status') or 'UNKNOWN'}\n"
        f"Reconciliation: "
        f"{event.get('reconciliation_status') or 'UNKNOWN'}\n"
        f"Quality Gate: "
        f"{event.get('quality_gate') or 'UNKNOWN'}\n"
        f"Freshness: "
        f"{event.get('reconciliation_freshness') or 'UNKNOWN'}\n"
        f"\n"
        f"Reason: {reason_text}\n"
        f"\n"
        f"{incident_text}"
        f"Observed: {event.get('observed_at') or 'UNKNOWN'}"
    )

    # Telegram sendMessage limit is comfortably above
    # this operational alert. Keep an additional safety cap.
    return message[:3900]


async def _deliver_webhook(
    session: Session,
    settings: Any,
    *,
    event: dict[str, Any],
) -> dict[str, Any]:

    existing = _existing_delivery(
        session,
        event_id=int(event["id"]),
        channel="webhook",
    )

    if existing is not None:
        result = _delivery_dict(existing)
        result["status"] = "existing"
        return result

    enabled = bool(
        getattr(
            settings,
            "safety_alert_enabled",
            False,
        )
    )

    webhook_url = str(
        getattr(
            settings,
            "safety_alert_webhook_url",
            "",
        )
        or ""
    ).strip()

    if not enabled:
        return _store_delivery(
            session,
            event=event,
            channel="webhook",
            status="DISABLED",
            attempts=0,
        )

    if not webhook_url:
        return _store_delivery(
            session,
            event=event,
            channel="webhook",
            status="CONFIG_REQUIRED",
            attempts=0,
        )

    if not webhook_url.lower().startswith(
        "https://"
    ):
        return _store_delivery(
            session,
            event=event,
            channel="webhook",
            status="CONFIG_REJECTED",
            attempts=0,
            error_type="HTTPS_REQUIRED",
        )

    token = str(
        getattr(
            settings,
            "safety_alert_webhook_bearer_token",
            "",
        )
        or ""
    ).strip()

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

    headers = {
        "Content-Type": "application/json",
        "User-Agent": "MDRN-SportsQ-Safety/0.10",
    }

    if token:
        headers[
            "Authorization"
        ] = f"Bearer {token}"

    payload = build_safety_alert_payload(
        event
    )

    last_error_type = None
    last_http_status = None
    attempts_used = 0

    for attempt in range(
        1,
        max_attempts + 1,
    ):
        attempts_used = attempt

        try:
            async with httpx.AsyncClient(
                timeout=timeout_seconds,
                follow_redirects=False,
            ) as client:

                response = await client.post(
                    webhook_url,
                    json=payload,
                    headers=headers,
                )

            last_http_status = int(
                response.status_code
            )

            if (
                200
                <= response.status_code
                < 300
            ):
                return _store_delivery(
                    session,
                    event=event,
                    channel="webhook",
                    status="DELIVERED",
                    attempts=attempt,
                    http_status=response.status_code,
                    delivered_at=_utcnow(),
                )

            last_error_type = (
                "HTTP_STATUS"
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
            last_error_type = (
                type(exc).__name__
            )

        except httpx.HTTPError as exc:
            last_error_type = (
                type(exc).__name__
            )

        if attempt < max_attempts:
            await asyncio.sleep(
                min(
                    0.5
                    * (2 ** (attempt - 1)),
                    2.0,
                )
            )

    return _store_delivery(
        session,
        event=event,
        channel="webhook",
        status="FAILED",
        attempts=attempts_used,
        http_status=last_http_status,
        error_type=(
            last_error_type
            or "DELIVERY_FAILED"
        ),
    )


async def _deliver_telegram(
    session: Session,
    settings: Any,
    *,
    event: dict[str, Any],
) -> dict[str, Any]:

    existing = _existing_delivery(
        session,
        event_id=int(event["id"]),
        channel="telegram",
    )

    if existing is not None:
        result = _delivery_dict(existing)
        result["status"] = "existing"
        return result

    enabled = bool(
        getattr(
            settings,
            "safety_alert_telegram_enabled",
            False,
        )
    )

    if not enabled:
        return {
            "status": "skipped",
            "channel": "telegram",
            "reason": "TELEGRAM_DISABLED",
            "final_holdout_touched": False,
        }

    bot_token = str(
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
        not bot_token
        or not chat_id
    ):
        return _store_delivery(
            session,
            event=event,
            channel="telegram",
            status="CONFIG_REQUIRED",
            attempts=0,
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

    # Token is intentionally used only here and is
    # never copied into logs, payloads or audit rows.
    url = (
        "https://api.telegram.org/"
        f"bot{bot_token}/sendMessage"
    )

    payload = {
        "chat_id": chat_id,
        "text": build_telegram_message(
            event
        ),
    }

    last_error_type = None
    last_http_status = None
    attempts_used = 0

    for attempt in range(
        1,
        max_attempts + 1,
    ):
        attempts_used = attempt

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

            telegram_ok = False

            if (
                200
                <= response.status_code
                < 300
            ):
                try:
                    telegram_body = (
                        response.json()
                    )

                    telegram_ok = bool(
                        telegram_body.get(
                            "ok",
                            False,
                        )
                    )

                except (
                    TypeError,
                    ValueError,
                ):
                    telegram_ok = False

            if telegram_ok:
                return _store_delivery(
                    session,
                    event=event,
                    channel="telegram",
                    status="DELIVERED",
                    attempts=attempt,
                    http_status=response.status_code,
                    delivered_at=_utcnow(),
                )

            last_error_type = (
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

            if (
                200
                <= response.status_code
                < 300
                and not telegram_ok
            ):
                retryable = False

            if not retryable:
                break

        except (
            httpx.TimeoutException,
            httpx.NetworkError,
        ) as exc:
            last_error_type = (
                type(exc).__name__
            )

        except httpx.HTTPError as exc:
            last_error_type = (
                type(exc).__name__
            )

        if attempt < max_attempts:
            await asyncio.sleep(
                min(
                    0.5
                    * (2 ** (attempt - 1)),
                    2.0,
                )
            )

    return _store_delivery(
        session,
        event=event,
        channel="telegram",
        status="FAILED",
        attempts=attempts_used,
        http_status=last_http_status,
        error_type=(
            last_error_type
            or "DELIVERY_FAILED"
        ),
    )


async def deliver_market_safety_alert(
    session: Session,
    settings: Any,
    *,
    event: dict[str, Any],
) -> dict[str, Any]:
    """Deliver one transition through configured channels.

    Backwards-compatible top-level result remains the webhook result.
    Telegram outcome is attached under the 'telegram' key when enabled.

    All network failures remain isolated and audit-safe.
    """

    alert_type = event.get(
        "alert_type"
    )

    if not alert_type:
        return {
            "status": "skipped",
            "reason": "NO_TRANSITION",
            "audit_id": None,
            "final_holdout_touched": False,
        }

    alert_type = str(
        alert_type
    ).upper()

    if alert_type not in DELIVERABLE_ALERT_TYPES:
        return {
            "status": "skipped",
            "reason": "UNSUPPORTED_ALERT_TYPE",
            "audit_id": None,
            "final_holdout_touched": False,
        }

    webhook_result = await _deliver_webhook(
        session,
        settings,
        event=event,
    )

    telegram_enabled = bool(
        getattr(
            settings,
            "safety_alert_telegram_enabled",
            False,
        )
    )

    if telegram_enabled:
        telegram_result = await _deliver_telegram(
            session,
            settings,
            event=event,
        )

        webhook_result[
            "telegram"
        ] = telegram_result

    return webhook_result


def list_market_safety_deliveries(
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int = 100,
) -> list[dict[str, Any]]:

    stmt = (
        select(
            MarketSafetyAlertDelivery
        )
        .where(
            MarketSafetyAlertDelivery.competition_id
            == int(competition_id),

            MarketSafetyAlertDelivery.season
            == int(season),
        )
        .order_by(
            MarketSafetyAlertDelivery.created_at.desc(),
            MarketSafetyAlertDelivery.id.desc(),
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
        _delivery_dict(row)
        for row
        in session.scalars(stmt).all()
    ]
