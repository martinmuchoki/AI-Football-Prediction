from __future__ import annotations

from datetime import datetime, timezone

import httpx
from fastapi.testclient import TestClient

import app.market_scheduler as scheduler
import app.services.market_safety_alerts as alerts

from app.main import app
from app.services.market_safety_alerts import (
    deliver_market_safety_alert,
    list_market_safety_deliveries,
)


def event(
    *,
    event_id: int = 501,
    alert_type: str | None = "BLOCKED",
):
    return {
        "id": event_id,
        "competition_id": 2,
        "season": 2026,

        "overall_status": (
            "BLOCKED"
            if alert_type == "BLOCKED"
            else "DEGRADED"
        ),

        "previous_status": "READY",
        "transition": (
            "READY->BLOCKED"
            if alert_type == "BLOCKED"
            else "READY->DEGRADED"
        ),

        "alert_type": alert_type,

        "prediction_lock_allowed": (
            alert_type != "BLOCKED"
        ),

        "prediction_gate_action": (
            "BLOCK_NEW_PREDICTIONS"
            if alert_type == "BLOCKED"
            else "ALLOW_DEGRADED"
        ),

        "reasons": [
            "RECONCILIATION_BLOCKED"
            if alert_type == "BLOCKED"
            else "RECONCILIATION_DEGRADED"
        ],

        "primary_source_status": "OK",

        "reconciliation_status": (
            "BLOCKED"
            if alert_type == "BLOCKED"
            else "DEGRADED"
        ),

        "quality_gate": (
            "FAIL"
            if alert_type == "BLOCKED"
            else "WARN"
        ),

        "reconciliation_freshness": "FRESH",

        "observed_at": (
            datetime(
                2026,
                9,
                10,
                1,
                0,
                tzinfo=timezone.utc,
            ).isoformat()
        ),

        "final_holdout_touched": False,
    }


class Settings:
    safety_alert_enabled = True
    safety_alert_webhook_url = (
        "https://alerts.example.test/sportsq"
    )
    safety_alert_webhook_bearer_token = (
        "secret-test-token"
    )
    safety_alert_timeout_seconds = 3.0
    safety_alert_max_retries = 3


async def test_no_transition_means_no_external_delivery(
    session,
):
    result = await deliver_market_safety_alert(
        session,
        Settings(),
        event=event(
            alert_type=None,
        ),
    )

    assert result["status"] == "skipped"
    assert result["reason"] == "NO_TRANSITION"

    rows = list_market_safety_deliveries(
        session,
        competition_id=2,
        season=2026,
    )

    assert rows == []


async def test_disabled_delivery_is_audited(
    session,
):
    class Disabled(Settings):
        safety_alert_enabled = False

    result = await deliver_market_safety_alert(
        session,
        Disabled(),
        event=event(
            event_id=502,
            alert_type="DEGRADED",
        ),
    )

    assert (
        result["delivery_status"]
        == "DISABLED"
    )

    assert result["attempts"] == 0


async def test_successful_webhook_delivery(
    session,
    monkeypatch,
):
    captured = {}

    class Response:
        status_code = 204

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
            headers,
        ):
            captured["url"] = url
            captured["json"] = json
            captured["headers"] = headers

            return Response()

    monkeypatch.setattr(
        alerts.httpx,
        "AsyncClient",
        Client,
    )

    result = await deliver_market_safety_alert(
        session,
        Settings(),
        event=event(
            event_id=503,
        ),
    )

    assert (
        result["delivery_status"]
        == "DELIVERED"
    )

    assert result["attempts"] == 1
    assert result["http_status"] == 204

    assert (
        captured["url"]
        == Settings.safety_alert_webhook_url
    )

    assert (
        captured["json"]["brand"]
        == "MDRN SportsQ"
    )

    assert (
        captured["json"]["alert_type"]
        == "BLOCKED"
    )

    # Secret is sent only as a header,
    # never included in alert payload.
    assert (
        "secret-test-token"
        not in str(captured["json"])
    )

    assert (
        captured[
            "headers"
        ][
            "Authorization"
        ]
        == "Bearer secret-test-token"
    )


async def test_network_failure_retries_and_is_audited(
    session,
    monkeypatch,
):
    calls = {
        "count": 0,
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
            headers,
        ):
            calls["count"] += 1

            request = httpx.Request(
                "POST",
                url,
            )

            raise httpx.ConnectError(
                "simulated",
                request=request,
            )

    async def no_sleep(
        delay,
    ):
        return None

    monkeypatch.setattr(
        alerts.httpx,
        "AsyncClient",
        Client,
    )

    monkeypatch.setattr(
        alerts.asyncio,
        "sleep",
        no_sleep,
    )

    result = await deliver_market_safety_alert(
        session,
        Settings(),
        event=event(
            event_id=504,
        ),
    )

    assert (
        result["delivery_status"]
        == "FAILED"
    )

    assert result["attempts"] == 3
    assert calls["count"] == 3

    assert (
        result["error_type"]
        == "ConnectError"
    )


async def test_same_event_delivery_is_idempotent(
    session,
):
    class Disabled(Settings):
        safety_alert_enabled = False

    first = await deliver_market_safety_alert(
        session,
        Disabled(),
        event=event(
            event_id=505,
            alert_type="DEGRADED",
        ),
    )

    second = await deliver_market_safety_alert(
        session,
        Disabled(),
        event=event(
            event_id=505,
            alert_type="DEGRADED",
        ),
    )

    assert first["id"] == second["id"]
    assert second["status"] == "existing"


def test_delivery_audit_api_exists():
    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/safety/deliveries"
            "?competition=2"
            "&season=2026"
            "&limit=10"
        )

        assert response.status_code == 200
        assert isinstance(
            response.json(),
            list,
        )


class _SessionContext:
    def __init__(self):
        self.session = object()

    def __enter__(self):
        return self.session

    def __exit__(
        self,
        exc_type,
        exc,
        tb,
    ):
        return False


class _SchedulerSettings:
    source_health_stale_after_minutes = 180
    reconciliation_stale_after_minutes = 180

    def sportsq_competitions(self):
        return [
            (39, 2026),
        ]

    def live_score_competitions(self):
        return [
            (2, 2026),
        ]


async def test_scheduler_delivers_only_transition_alert(
    monkeypatch,
):
    actions = []

    monkeypatch.setattr(
        scheduler,
        "settings",
        _SchedulerSettings(),
    )

    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: _SessionContext(),
    )

    async def reconcile():
        actions.append("reconcile")

        return {
            "status": "success",
        }

    async def refresh(
        session,
        settings,
        *,
        competition_id,
        season,
    ):
        actions.append("refresh")

        return {
            "status": "success",
        }

    def safety(
        session,
        **kwargs,
    ):
        actions.append("safety")

        return {
            "competition_id": 2,
            "season": 2026,
        }

    def record(
        session,
        *,
        snapshot,
    ):
        actions.append("record")

        return event(
            event_id=506,
            alert_type="BLOCKED",
        )

    async def deliver(
        session,
        settings,
        *,
        event,
    ):
        actions.append("deliver")

        return {
            "delivery_status": "DELIVERED",
            "attempts": 1,
        }

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        reconcile,
    )

    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        refresh,
    )

    monkeypatch.setattr(
        scheduler,
        "market_safety_status",
        safety,
    )

    monkeypatch.setattr(
        scheduler,
        "record_market_safety_event",
        record,
    )

    monkeypatch.setattr(
        scheduler,
        "deliver_market_safety_alert",
        deliver,
    )

    await scheduler.live_market_job()

    assert actions == [
        "reconcile",
        "refresh",
        "safety",
        "record",
        "deliver",
    ]
