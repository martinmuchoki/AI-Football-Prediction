from __future__ import annotations

from datetime import datetime, timedelta, timezone

import app.services.market_reliability as reliability

from app.main import app

from app.services.market_reliability import (
    database_integrity_report,
    deliver_market_safety_sla_alert,
    evaluate_market_safety_sla,
    list_market_safety_sla_alerts,
    recover_incident_state_from_history,
    reliability_summary,
)

from app.services.market_safety_events import (
    record_market_safety_event,
)

from app.services.market_safety_incidents import (
    process_market_safety_incident,
)


BASE = datetime(
    2026,
    9,
    10,
    0,
    0,
    tzinfo=timezone.utc,
)


class Settings:
    incident_degraded_warn_minutes = 60
    incident_degraded_critical_minutes = 180

    incident_blocked_warn_minutes = 15
    incident_blocked_critical_minutes = 60

    operations_heartbeat_stale_minutes = 120

    safety_alert_telegram_enabled = True
    safety_alert_telegram_bot_token = (
        "123456:TEST-SECRET"
    )
    safety_alert_telegram_chat_id = (
        "987654321"
    )

    safety_alert_timeout_seconds = 3.0
    safety_alert_max_retries = 3


def incident_event(
    event_id: int,
    status: str,
    *,
    minutes: int = 0,
):
    return {
        "id": event_id,
        "competition_id": 2,
        "season": 2026,

        "overall_status": status,
        "previous_status": None,
        "transition": None,
        "alert_type": None,

        "prediction_lock_allowed": (
            status != "BLOCKED"
        ),

        "prediction_gate_action": (
            "BLOCK_NEW_PREDICTIONS"
            if status == "BLOCKED"
            else "ALLOW_DEGRADED"
            if status == "DEGRADED"
            else "ALLOW"
        ),

        "reasons": [
            status
        ],

        "primary_source_status": "OK",

        "reconciliation_status": (
            "BLOCKED"
            if status == "BLOCKED"
            else "DEGRADED"
            if status == "DEGRADED"
            else "HEALTHY"
        ),

        "quality_gate": (
            "FAIL"
            if status == "BLOCKED"
            else "WARN"
            if status == "DEGRADED"
            else "PASS"
        ),

        "reconciliation_freshness": "FRESH",

        "observed_at": (
            BASE
            + timedelta(
                minutes=minutes
            )
        ).isoformat(),

        "final_holdout_touched": False,
    }


def safety_snapshot(
    status: str,
):
    return {
        "competition_id": 2,
        "season": 2026,

        "overall_status": status,

        "prediction_lock_allowed": (
            status != "BLOCKED"
        ),

        "prediction_gate_action": (
            "BLOCK_NEW_PREDICTIONS"
            if status == "BLOCKED"
            else "ALLOW_DEGRADED"
            if status == "DEGRADED"
            else "ALLOW"
        ),

        "reasons": [
            status
        ],

        "primary_source": {
            "effective_health_status": "OK",
        },

        "reconciliation": {
            "operational_status": (
                "BLOCKED"
                if status == "BLOCKED"
                else "DEGRADED"
                if status == "DEGRADED"
                else "HEALTHY"
            ),

            "quality_gate": (
                "FAIL"
                if status == "BLOCKED"
                else "WARN"
                if status == "DEGRADED"
                else "PASS"
            ),

            "freshness": "FRESH",

            "fixture_agreement_rate": 1.0,

            "issue_count": 0,
            "conflict_count": 0,
            "warning_count": 0,
        },

        "odds_market": {
            "snapshots": 10,
        },
    }


def test_degraded_warning_and_critical_are_persistent(
    session,
):
    opened = process_market_safety_incident(
        session,
        event=incident_event(
            2001,
            "DEGRADED",
        ),
    )["incident"]

    warning = evaluate_market_safety_sla(
        session,
        Settings(),
        incident=opened,
        now=BASE + timedelta(
            minutes=70
        ),
    )

    assert (
        warning["alert"]["stage"]
        == "DEGRADED_WARNING"
    )

    assert warning["new_alert"] is True

    critical = evaluate_market_safety_sla(
        session,
        Settings(),
        incident=opened,
        now=BASE + timedelta(
            minutes=200
        ),
    )

    assert (
        critical["alert"]["stage"]
        == "DEGRADED_CRITICAL"
    )

    assert critical["new_alert"] is True

    duplicate = evaluate_market_safety_sla(
        session,
        Settings(),
        incident=opened,
        now=BASE + timedelta(
            minutes=220
        ),
    )

    assert (
        duplicate["alert"]["stage"]
        == "DEGRADED_CRITICAL"
    )

    assert (
        duplicate["new_alert"]
        is False
    )

    rows = list_market_safety_sla_alerts(
        session,
        competition_id=2,
        season=2026,
        limit=20,
    )

    assert len(rows) == 2


def test_blocked_warning_stage(
    session,
):
    opened = process_market_safety_incident(
        session,
        event=incident_event(
            2011,
            "BLOCKED",
        ),
    )["incident"]

    result = evaluate_market_safety_sla(
        session,
        Settings(),
        incident=opened,
        now=BASE + timedelta(
            minutes=20
        ),
    )

    assert (
        result["alert"]["stage"]
        == "BLOCKED_WARNING"
    )

    assert (
        result["alert"]["severity"]
        == "WARNING"
    )


async def test_sla_telegram_delivery_success(
    session,
    monkeypatch,
):
    opened = process_market_safety_incident(
        session,
        event=incident_event(
            2021,
            "BLOCKED",
        ),
    )["incident"]

    evaluation = evaluate_market_safety_sla(
        session,
        Settings(),
        incident=opened,
        now=BASE + timedelta(
            minutes=70
        ),
    )

    captured = {}

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
        ):
            captured["url"] = url
            captured["json"] = json

            return Response()

    monkeypatch.setattr(
        reliability.httpx,
        "AsyncClient",
        Client,
    )

    delivered = await deliver_market_safety_sla_alert(
        session,
        Settings(),
        alert=evaluation["alert"],
        incident=opened,
    )

    assert (
        delivered[
            "telegram_delivery_status"
        ]
        == "DELIVERED"
    )

    assert (
        delivered[
            "telegram_delivery_attempts"
        ]
        == 1
    )

    assert (
        "MDRN SportsQ SLA Escalation"
        in captured["json"]["text"]
    )

    assert (
        "123456:TEST-SECRET"
        not in str(
            captured["json"]
        )
    )


def test_crash_gap_recovery_opens_missing_incident(
    session,
):
    record_market_safety_event(
        session,
        snapshot=safety_snapshot(
            "DEGRADED"
        ),
        observed_at=BASE,
    )

    recovery = (
        recover_incident_state_from_history(
            session,
            competition_id=2,
            season=2026,
        )
    )

    assert (
        recovery["status"]
        == "REPAIRED"
    )

    assert recovery["processed"] == 1

    second = (
        recover_incident_state_from_history(
            session,
            competition_id=2,
            season=2026,
        )
    )

    assert (
        second["status"]
        == "CONSISTENT"
    )


def test_integrity_report_passes(
    session,
):
    report = database_integrity_report(
        session
    )

    assert report["status"] == "PASS"

    assert (
        report["final_holdout_touched"]
        is False
    )


def test_reliability_heartbeat_detects_stale_scheduler(
    session,
):
    record_market_safety_event(
        session,
        snapshot=safety_snapshot(
            "READY"
        ),
        observed_at=BASE,
    )

    summary = reliability_summary(
        session,
        Settings(),
        competition_id=2,
        season=2026,
        now=BASE + timedelta(
            minutes=180
        ),
    )

    assert (
        summary[
            "scheduler_heartbeat"
        ][
            "status"
        ]
        == "STALE"
    )

    assert (
        summary[
            "overall_reliability_status"
        ]
        == "DEGRADED"
    )


def test_reliability_api_routes_exist():
    paths = {
        route.path
        for route in app.routes
    }

    required = {
        "/api/v1/market/reliability",
        "/api/v1/market/reliability/integrity",
        "/api/v1/market/reliability/sla",
    }

    assert required.issubset(
        paths
    )
