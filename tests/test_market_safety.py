from __future__ import annotations

import app.services.market_safety as safety_service


def _source_summary(status: str):
    return {
        "status": "success",
        "sources": 1,
        "health_counts": {
            status: 1,
        },
        "stale_after_minutes": 180,
        "items": [
            {
                "slug": "live-score-api",
                "health_status": status,
                "effective_health_status": status,
                "last_checked_at": (
                    "2026-09-10T00:00:00+00:00"
                ),
                "last_success_at": (
                    "2026-09-10T00:00:00+00:00"
                ),
                "consecutive_failures": 0,
            }
        ],
    }


def _reconciliation(
    operational_status: str,
    *,
    allowed: bool = True,
):
    if operational_status == "HEALTHY":
        gate = "PASS"
        action = "ALLOW"

    elif operational_status == "BLOCKED":
        gate = "FAIL"
        action = "BLOCK_NEW_PREDICTIONS"

    elif operational_status == "DEGRADED":
        gate = "WARN"
        action = "ALLOW_DEGRADED"

    else:
        gate = "STALE"
        action = "ALLOW_UNVERIFIED"

    return {
        "status": "success",
        "operational_status": operational_status,
        "prediction_lock_allowed": allowed,
        "action": action,
        "quality_gate": gate,
        "reported_quality_gate": gate,
        "freshness": (
            "FRESH"
            if operational_status
            in {
                "HEALTHY",
                "DEGRADED",
                "BLOCKED",
            }
            else "STALE"
        ),
        "age_minutes": 10.0,
        "stale_after_minutes": 180,
        "fixture_agreement_rate": 1.0,
        "issue_count": 0,
        "conflict_count": (
            1
            if operational_status == "BLOCKED"
            else 0
        ),
        "warning_count": (
            1
            if operational_status == "DEGRADED"
            else 0
        ),
        "last_checked_at": (
            "2026-09-10T00:00:00+00:00"
        ),
        "final_holdout_touched": False,
    }


def test_market_safety_ready_when_required_layers_are_healthy(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        safety_service,
        "source_health_summary",
        lambda *args, **kwargs: _source_summary(
            "OK"
        ),
    )

    monkeypatch.setattr(
        safety_service,
        "reconciliation_health",
        lambda *args, **kwargs: _reconciliation(
            "HEALTHY",
            allowed=True,
        ),
    )

    result = safety_service.market_safety_status(
        session,
        competition_id=2,
        season=2026,
    )

    assert result["overall_status"] == "READY"

    assert result["reasons"] == [
        "ALL_REQUIRED_GATES_READY"
    ]

    assert result[
        "prediction_lock_allowed"
    ] is True

    assert result[
        "primary_source"
    ][
        "effective_health_status"
    ] == "OK"

    assert result[
        "reconciliation"
    ][
        "operational_status"
    ] == "HEALTHY"

    assert result[
        "odds_market"
    ][
        "freshness_policy"
    ] == "INFORMATIONAL_DEDUPLICATED_NOT_HARD_GATE"

    assert result[
        "final_holdout_touched"
    ] is False


def test_market_safety_degraded_for_stale_primary_source(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        safety_service,
        "source_health_summary",
        lambda *args, **kwargs: _source_summary(
            "STALE"
        ),
    )

    monkeypatch.setattr(
        safety_service,
        "reconciliation_health",
        lambda *args, **kwargs: _reconciliation(
            "HEALTHY",
            allowed=True,
        ),
    )

    result = safety_service.market_safety_status(
        session,
        competition_id=2,
        season=2026,
    )

    assert result["overall_status"] == "DEGRADED"

    assert (
        "PRIMARY_SOURCE_STALE"
        in result["reasons"]
    )

    # Source observability does not invent a new
    # prediction-lock policy.
    assert result[
        "prediction_lock_allowed"
    ] is True

    assert result[
        "final_holdout_touched"
    ] is False


def test_market_safety_degraded_for_stale_reconciliation(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        safety_service,
        "source_health_summary",
        lambda *args, **kwargs: _source_summary(
            "OK"
        ),
    )

    monkeypatch.setattr(
        safety_service,
        "reconciliation_health",
        lambda *args, **kwargs: _reconciliation(
            "STALE",
            allowed=True,
        ),
    )

    result = safety_service.market_safety_status(
        session,
        competition_id=2,
        season=2026,
    )

    assert result["overall_status"] == "DEGRADED"

    assert (
        "RECONCILIATION_STALE"
        in result["reasons"]
    )

    assert result[
        "prediction_lock_allowed"
    ] is True

    assert result[
        "prediction_gate_action"
    ] == "ALLOW_UNVERIFIED"

    assert result[
        "final_holdout_touched"
    ] is False


def test_market_safety_blocked_only_by_actual_prediction_gate(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        safety_service,
        "source_health_summary",
        lambda *args, **kwargs: _source_summary(
            "OK"
        ),
    )

    monkeypatch.setattr(
        safety_service,
        "reconciliation_health",
        lambda *args, **kwargs: _reconciliation(
            "BLOCKED",
            allowed=False,
        ),
    )

    result = safety_service.market_safety_status(
        session,
        competition_id=2,
        season=2026,
    )

    assert result["overall_status"] == "BLOCKED"

    assert result["reasons"] == [
        "RECONCILIATION_BLOCKED"
    ]

    assert result[
        "prediction_lock_allowed"
    ] is False

    assert result[
        "prediction_gate_action"
    ] == "BLOCK_NEW_PREDICTIONS"

    assert result[
        "final_holdout_touched"
    ] is False


def test_market_safety_api_route_exists():
    from fastapi.testclient import TestClient
    from app.main import app

    with TestClient(app) as client:
        response = client.get(
            "/api/v1/market/safety"
            "?competition=2&season=2026"
        )

        assert response.status_code == 200

        payload = response.json()

        assert payload["overall_status"] in {
            "READY",
            "DEGRADED",
            "BLOCKED",
        }

        assert isinstance(
            payload["prediction_lock_allowed"],
            bool,
        )

        assert (
            payload[
                "odds_market"
            ][
                "freshness_policy"
            ]
            ==
            "INFORMATIONAL_DEDUPLICATED_NOT_HARD_GATE"
        )

        assert payload[
            "final_holdout_touched"
        ] is False
