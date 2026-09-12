from __future__ import annotations

from datetime import datetime, timezone

import app.services.market_safety_alerts as alerts

from app.services.market_safety_alerts import (
    build_telegram_message,
    deliver_market_safety_alert,
    list_market_safety_deliveries,
)


def event(
    event_id: int = 701,
    *,
    alert_type: str = "BLOCKED",
) -> dict:

    return {
        "id": event_id,
        "competition_id": 2,
        "season": 2026,

        "overall_status": (
            "BLOCKED"
            if alert_type == "BLOCKED"
            else "DEGRADED"
            if alert_type == "DEGRADED"
            else "READY"
        ),

        "previous_status": (
            "DEGRADED"
            if alert_type == "BLOCKED"
            else "READY"
            if alert_type == "DEGRADED"
            else "BLOCKED"
        ),

        "transition": (
            "DEGRADED->BLOCKED"
            if alert_type == "BLOCKED"
            else "READY->DEGRADED"
            if alert_type == "DEGRADED"
            else "BLOCKED->READY"
        ),

        "alert_type": alert_type,

        "prediction_lock_allowed": (
            alert_type != "BLOCKED"
        ),

        "prediction_gate_action": (
            "BLOCK_NEW_PREDICTIONS"
            if alert_type == "BLOCKED"
            else "ALLOW_DEGRADED"
            if alert_type == "DEGRADED"
            else "ALLOW"
        ),

        "reasons": [
            "RECONCILIATION_BLOCKED"
            if alert_type == "BLOCKED"
            else "RECONCILIATION_DEGRADED"
            if alert_type == "DEGRADED"
            else "ALL_REQUIRED_GATES_READY"
        ],

        "primary_source_status": "OK",

        "reconciliation_status": (
            "BLOCKED"
            if alert_type == "BLOCKED"
            else "DEGRADED"
            if alert_type == "DEGRADED"
            else "HEALTHY"
        ),

        "quality_gate": (
            "FAIL"
            if alert_type == "BLOCKED"
            else "WARN"
            if alert_type == "DEGRADED"
            else "PASS"
        ),

        "reconciliation_freshness": "FRESH",

        "observed_at": datetime(
            2026,
            9,
            10,
            1,
            30,
            tzinfo=timezone.utc,
        ).isoformat(),

        "final_holdout_touched": False,
    }


class TelegramSettings:
    safety_alert_enabled = False
    safety_alert_webhook_url = ""
    safety_alert_webhook_bearer_token = ""

    safety_alert_telegram_enabled = True
    safety_alert_telegram_bot_token = (
        "123456:TEST-SECRET-TOKEN"
    )

    safety_alert_telegram_chat_id = (
        "987654321"
    )

    safety_alert_timeout_seconds = 3.0
    safety_alert_max_retries = 3


def test_telegram_message_contains_operational_state():

    message = build_telegram_message(
        event()
    )

    assert "MDRN SportsQ" in message
    assert "BLOCKED" in message
    assert "DEGRADED->BLOCKED" in message
    assert "Prediction Locking: BLOCKED" in message
    assert "Quality Gate: FAIL" in message

    assert (
        "123456:TEST-SECRET-TOKEN"
        not in message
    )


async def test_native_telegram_delivery_success(
    session,
    monkeypatch,
):
    captured = {}

    class Response:
        status_code = 200

        def json(self):
            return {
                "ok": True,
                "result": {
                    "message_id": 123,
                },
            }

    class Client:
        def __init__(
            self,
            *args,
            **kwargs,
        ):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            tb,
        ):
            return False

        async def post(
            self,
            url,
            *,
            json,
            **kwargs,
        ):
            captured["url"] = url
            captured["json"] = json

            return Response()

    monkeypatch.setattr(
        alerts.httpx,
        "AsyncClient",
        Client,
    )

    result = await deliver_market_safety_alert(
        session,
        TelegramSettings(),
        event=event(
            event_id=702,
        ),
    )

    assert (
        result["delivery_status"]
        == "DISABLED"
    )

    telegram = result["telegram"]

    assert (
        telegram["delivery_status"]
        == "DELIVERED"
    )

    assert telegram["channel"] == "telegram"
    assert telegram["attempts"] == 1
    assert telegram["http_status"] == 200

    assert (
        captured["json"]["chat_id"]
        == "987654321"
    )

    assert (
        "MDRN SportsQ"
        in captured["json"]["text"]
    )

    assert (
        "123456:TEST-SECRET-TOKEN"
        not in str(captured["json"])
    )

    assert captured["url"].startswith(
        "https://api.telegram.org/bot"
    )

    rows = list_market_safety_deliveries(
        session,
        competition_id=2,
        season=2026,
        limit=20,
    )

    channels = {
        row["channel"]
        for row in rows
    }

    assert "webhook" in channels
    assert "telegram" in channels


async def test_telegram_missing_credentials_is_audited(
    session,
):
    class Missing(TelegramSettings):
        safety_alert_telegram_bot_token = ""
        safety_alert_telegram_chat_id = ""

    result = await deliver_market_safety_alert(
        session,
        Missing(),
        event=event(
            event_id=703,
            alert_type="DEGRADED",
        ),
    )

    assert (
        result["telegram"]["delivery_status"]
        == "CONFIG_REQUIRED"
    )

    assert (
        result["telegram"]["attempts"]
        == 0
    )


async def test_telegram_api_rejection_is_failure_isolated(
    session,
    monkeypatch,
):
    class Response:
        status_code = 400

        def json(self):
            return {
                "ok": False,
                "description": (
                    "simulated rejection"
                ),
            }

    class Client:
        def __init__(
            self,
            *args,
            **kwargs,
        ):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            tb,
        ):
            return False

        async def post(
            self,
            url,
            *,
            json,
            **kwargs,
        ):
            return Response()

    monkeypatch.setattr(
        alerts.httpx,
        "AsyncClient",
        Client,
    )

    result = await deliver_market_safety_alert(
        session,
        TelegramSettings(),
        event=event(
            event_id=704,
        ),
    )

    telegram = result["telegram"]

    assert (
        telegram["delivery_status"]
        == "FAILED"
    )

    assert telegram["attempts"] == 1
    assert telegram["http_status"] == 400
    assert telegram["error_type"] == "HTTP_STATUS"


async def test_telegram_delivery_is_idempotent(
    session,
    monkeypatch,
):
    calls = {
        "count": 0,
    }

    class Response:
        status_code = 200

        def json(self):
            return {
                "ok": True,
            }

    class Client:
        def __init__(
            self,
            *args,
            **kwargs,
        ):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(
            self,
            exc_type,
            exc,
            tb,
        ):
            return False

        async def post(
            self,
            url,
            *,
            json,
            **kwargs,
        ):
            calls["count"] += 1
            return Response()

    monkeypatch.setattr(
        alerts.httpx,
        "AsyncClient",
        Client,
    )

    first = await deliver_market_safety_alert(
        session,
        TelegramSettings(),
        event=event(
            event_id=705,
            alert_type="RECOVERY",
        ),
    )

    second = await deliver_market_safety_alert(
        session,
        TelegramSettings(),
        event=event(
            event_id=705,
            alert_type="RECOVERY",
        ),
    )

    assert (
        first["telegram"]["delivery_status"]
        == "DELIVERED"
    )

    assert (
        second["telegram"]["status"]
        == "existing"
    )

    assert calls["count"] == 1
