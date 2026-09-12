from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from app.main import app

from app.services.market_safety_incidents import (
    active_market_safety_incident,
    list_market_safety_incidents,
    market_safety_incident_metrics,
    process_market_safety_incident,
)

from app.services.market_safety_alerts import (
    build_telegram_message,
)


BASE = datetime(
    2026,
    9,
    10,
    0,
    0,
    tzinfo=timezone.utc,
)


def event(
    event_id: int,
    status: str,
    *,
    minutes: int,
    previous_status: str | None = None,
):
    alert_type = None
    transition = None

    if previous_status and previous_status != status:
        transition = (
            f"{previous_status}->{status}"
        )

        if status == "BLOCKED":
            alert_type = "BLOCKED"

        elif status == "DEGRADED":
            alert_type = "DEGRADED"

        elif status == "READY":
            alert_type = "RECOVERY"

    return {
        "id": event_id,
        "competition_id": 2,
        "season": 2026,

        "overall_status": status,

        "previous_status": previous_status,
        "transition": transition,
        "alert_type": alert_type,

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


def test_bootstrap_degraded_opens_incident(
    session,
):
    result = process_market_safety_incident(
        session,
        event=event(
            1001,
            "DEGRADED",
            minutes=0,
        ),
    )

    assert result["action"] == "OPENED"

    incident = result["incident"]

    assert incident["status"] == "OPEN"

    assert (
        incident["opened_status"]
        == "DEGRADED"
    )

    assert (
        incident["highest_severity"]
        == "DEGRADED"
    )

    active = active_market_safety_incident(
        session,
        competition_id=2,
        season=2026,
    )

    assert active["active"] is True


def test_repeated_degraded_updates_same_incident(
    session,
):
    first = process_market_safety_incident(
        session,
        event=event(
            1011,
            "DEGRADED",
            minutes=0,
        ),
    )

    second = process_market_safety_incident(
        session,
        event=event(
            1012,
            "DEGRADED",
            minutes=5,
            previous_status="DEGRADED",
        ),
    )

    assert first["incident"]["id"] == (
        second["incident"]["id"]
    )

    assert second["action"] == "UPDATED"

    assert (
        second["incident"][
            "escalation_count"
        ]
        == 0
    )


def test_degraded_to_blocked_escalates(
    session,
):
    opened = process_market_safety_incident(
        session,
        event=event(
            1021,
            "DEGRADED",
            minutes=0,
        ),
    )

    escalated = process_market_safety_incident(
        session,
        event=event(
            1022,
            "BLOCKED",
            minutes=10,
            previous_status="DEGRADED",
        ),
    )

    assert opened["incident"]["id"] == (
        escalated["incident"]["id"]
    )

    assert escalated["action"] == "ESCALATED"

    assert (
        escalated["incident"][
            "highest_severity"
        ]
        == "BLOCKED"
    )

    assert (
        escalated["incident"][
            "escalation_count"
        ]
        == 1
    )


def test_ready_closes_incident_and_calculates_mttr(
    session,
):
    process_market_safety_incident(
        session,
        event=event(
            1031,
            "DEGRADED",
            minutes=0,
        ),
    )

    process_market_safety_incident(
        session,
        event=event(
            1032,
            "BLOCKED",
            minutes=10,
            previous_status="DEGRADED",
        ),
    )

    closed = process_market_safety_incident(
        session,
        event=event(
            1033,
            "READY",
            minutes=30,
            previous_status="BLOCKED",
        ),
    )

    assert closed["action"] == "CLOSED"

    incident = closed["incident"]

    assert incident["status"] == "CLOSED"
    assert incident["current_status"] == "READY"

    assert (
        incident["duration_seconds"]
        == 1800.0
    )

    metrics = market_safety_incident_metrics(
        session,
        competition_id=2,
        season=2026,
    )

    assert metrics["incident_count"] == 1
    assert metrics["active_incidents"] == 0
    assert metrics["closed_incidents"] == 1

    assert (
        metrics["mttr_seconds"]
        == 1800.0
    )

    assert (
        metrics["mttr_minutes"]
        == 30.0
    )


def test_same_event_is_idempotent(
    session,
):
    first = process_market_safety_incident(
        session,
        event=event(
            1041,
            "DEGRADED",
            minutes=0,
        ),
    )

    duplicate = process_market_safety_incident(
        session,
        event=event(
            1041,
            "DEGRADED",
            minutes=0,
        ),
    )

    assert duplicate["action"] == "DUPLICATE"

    rows = list_market_safety_incidents(
        session,
        competition_id=2,
        season=2026,
        limit=20,
    )

    assert len(rows) == 1

    assert (
        first["incident"]["id"]
        == duplicate["incident"]["id"]
    )


def test_ready_without_open_incident_is_noop(
    session,
):
    result = process_market_safety_incident(
        session,
        event=event(
            1051,
            "READY",
            minutes=0,
        ),
    )

    assert result["action"] == "NONE"
    assert result["incident"] is None


def test_telegram_message_contains_incident_context():
    safety_event = event(
        1061,
        "BLOCKED",
        minutes=10,
        previous_status="DEGRADED",
    )

    safety_event["incident"] = {
        "action": "ESCALATED",

        "incident": {
            "id": 77,
            "status": "OPEN",
            "highest_severity": "BLOCKED",
            "escalation_count": 1,
        },
    }

    message = build_telegram_message(
        safety_event
    )

    assert "Incident ID: 77" in message

    assert (
        "Incident Action: ESCALATED"
        in message
    )

    assert (
        "Highest Severity: BLOCKED"
        in message
    )


def test_incident_api_routes_exist():
    with TestClient(app) as client:

        paths = (
            "/api/v1/market/safety/incidents"
            "?competition=2&season=2026&limit=10",

            "/api/v1/market/safety/incidents/active"
            "?competition=2&season=2026",

            "/api/v1/market/safety/incidents/metrics"
            "?competition=2&season=2026",
        )

        for path in paths:
            response = client.get(
                path
            )

            assert (
                response.status_code
                == 200
            )
