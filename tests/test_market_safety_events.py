from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

import app.market_scheduler as scheduler

from app.main import app

from app.services.market_safety_events import (
    list_market_safety_events,
    record_market_safety_event,
)


BASE_TIME = datetime(
    2026,
    9,
    10,
    0,
    0,
    tzinfo=timezone.utc,
)


def snapshot(status: str) -> dict:

    if status == "READY":
        allowed = True
        action = "ALLOW"
        reconciliation_status = "HEALTHY"
        gate = "PASS"
        reasons = [
            "ALL_REQUIRED_GATES_READY"
        ]

    elif status == "DEGRADED":
        allowed = True
        action = "ALLOW_DEGRADED"
        reconciliation_status = "DEGRADED"
        gate = "WARN"
        reasons = [
            "RECONCILIATION_DEGRADED"
        ]

    else:
        allowed = False
        action = "BLOCK_NEW_PREDICTIONS"
        reconciliation_status = "BLOCKED"
        gate = "FAIL"
        reasons = [
            "RECONCILIATION_BLOCKED"
        ]

    return {
        "status": "success",
        "competition_id": 2,
        "season": 2026,

        "overall_status": status,
        "reasons": reasons,

        "prediction_lock_allowed": allowed,
        "prediction_gate_action": action,

        "primary_source": {
            "effective_health_status": "OK",
        },

        "reconciliation": {
            "operational_status": (
                reconciliation_status
            ),

            "quality_gate": gate,
            "freshness": "FRESH",

            "fixture_agreement_rate": (
                1.0
                if gate == "PASS"
                else 0.994737
            ),

            "issue_count": (
                0
                if gate == "PASS"
                else 2
            ),

            "conflict_count": (
                1
                if gate == "FAIL"
                else 0
            ),

            "warning_count": (
                2
                if gate == "WARN"
                else 0
            ),
        },

        "odds_market": {
            "snapshots": 18,
        },

        "final_holdout_touched": False,
    }


def test_market_safety_transitions_and_alerts(
    session,
):
    first = record_market_safety_event(
        session,
        snapshot=snapshot("READY"),
        observed_at=BASE_TIME,
    )

    assert first["previous_status"] is None
    assert first["transition"] is None
    assert first["alert_type"] is None

    degraded = record_market_safety_event(
        session,
        snapshot=snapshot("DEGRADED"),
        observed_at=(
            BASE_TIME
            + timedelta(minutes=1)
        ),
    )

    assert (
        degraded["transition"]
        == "READY->DEGRADED"
    )

    assert degraded[
        "alert_type"
    ] == "DEGRADED"

    blocked = record_market_safety_event(
        session,
        snapshot=snapshot("BLOCKED"),
        observed_at=(
            BASE_TIME
            + timedelta(minutes=2)
        ),
    )

    assert (
        blocked["transition"]
        == "DEGRADED->BLOCKED"
    )

    assert blocked[
        "alert_type"
    ] == "BLOCKED"

    recovered = record_market_safety_event(
        session,
        snapshot=snapshot("READY"),
        observed_at=(
            BASE_TIME
            + timedelta(minutes=3)
        ),
    )

    assert (
        recovered["transition"]
        == "BLOCKED->READY"
    )

    assert recovered[
        "alert_type"
    ] == "RECOVERY"

    unchanged = record_market_safety_event(
        session,
        snapshot=snapshot("READY"),
        observed_at=(
            BASE_TIME
            + timedelta(minutes=4)
        ),
    )

    assert unchanged["transition"] is None
    assert unchanged["alert_type"] is None

    history = list_market_safety_events(
        session,
        competition_id=2,
        season=2026,
        limit=20,
    )

    assert len(history) == 5

    transitions = list_market_safety_events(
        session,
        competition_id=2,
        season=2026,
        limit=20,
        transitions_only=True,
    )

    assert len(transitions) == 3

    assert {
        row["alert_type"]
        for row in transitions
    } == {
        "DEGRADED",
        "BLOCKED",
        "RECOVERY",
    }


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


class _Settings:
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


async def test_scheduler_records_safety_after_refresh(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        scheduler,
        "settings",
        _Settings(),
    )

    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: _SessionContext(),
    )

    async def fake_reconciliation():
        events.append(
            "reconciliation"
        )

        return {
            "status": "success",
            "results": [],
            "final_holdout_touched": False,
        }

    async def fake_refresh(
        session,
        settings,
        *,
        competition_id,
        season,
    ):
        events.append(
            "market"
        )

        return {
            "status": "success",
            "final_holdout_touched": False,
        }

    def fake_safety(
        session,
        **kwargs,
    ):
        events.append(
            "safety"
        )

        return snapshot(
            "READY"
        )

    def fake_record(
        session,
        *,
        snapshot,
    ):
        events.append(
            "record"
        )

        return {
            "overall_status": "READY",
            "transition": None,
            "alert_type": None,
        }

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )

    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_refresh,
    )

    monkeypatch.setattr(
        scheduler,
        "market_safety_status",
        fake_safety,
    )

    monkeypatch.setattr(
        scheduler,
        "record_market_safety_event",
        fake_record,
    )

    await scheduler.live_market_job()

    assert events == [
        "reconciliation",
        "market",
        "safety",
        "record",
    ]


async def test_observability_failure_is_isolated(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(
        scheduler,
        "settings",
        _Settings(),
    )

    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: _SessionContext(),
    )

    async def fake_reconciliation():
        events.append(
            "reconciliation"
        )

        return {
            "status": "success",
            "results": [],
            "final_holdout_touched": False,
        }

    async def fake_refresh(
        session,
        settings,
        *,
        competition_id,
        season,
    ):
        events.append(
            "market"
        )

        return {
            "status": "success",
            "final_holdout_touched": False,
        }

    def fail_safety(
        session,
        **kwargs,
    ):
        events.append(
            "safety"
        )

        raise RuntimeError(
            "simulated observability failure"
        )

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )

    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_refresh,
    )

    monkeypatch.setattr(
        scheduler,
        "market_safety_status",
        fail_safety,
    )

    await scheduler.live_market_job()

    assert events == [
        "reconciliation",
        "market",
        "safety",
    ]


def test_safety_history_api_routes_exist():
    with TestClient(app) as client:

        response = client.get(
            "/api/v1/market/safety/history"
            "?competition=2"
            "&season=2026"
            "&limit=10"
        )

        assert response.status_code == 200
        assert isinstance(
            response.json(),
            list,
        )

        response = client.get(
            "/api/v1/market/safety/transitions"
            "?competition=2"
            "&season=2026"
            "&limit=10"
        )

        assert response.status_code == 200
        assert isinstance(
            response.json(),
            list,
        )
