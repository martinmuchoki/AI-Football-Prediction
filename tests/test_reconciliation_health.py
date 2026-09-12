from datetime import datetime, timezone

import pytest

import app.services.reconciliation_health as health_service


NOW = datetime(
    2026,
    9,
    10,
    0,
    0,
    tzinfo=timezone.utc,
)


@pytest.mark.parametrize(
    (
        "gate",
        "checked_at",
        "expected_status",
        "expected_gate",
        "allowed",
        "action",
    ),
    [
        (
            "PASS",
            "2026-09-09T23:30:00+00:00",
            "HEALTHY",
            "PASS",
            True,
            "ALLOW",
        ),
        (
            "WARN",
            "2026-09-09T23:30:00+00:00",
            "DEGRADED",
            "WARN",
            True,
            "ALLOW_DEGRADED",
        ),
        (
            "FAIL",
            "2026-09-09T23:30:00+00:00",
            "BLOCKED",
            "FAIL",
            False,
            "BLOCK_NEW_PREDICTIONS",
        ),
        (
            "PASS",
            "2026-09-09T18:00:00+00:00",
            "STALE",
            "STALE",
            True,
            "ALLOW_UNVERIFIED",
        ),
        (
            "FAIL",
            "2026-09-09T18:00:00+00:00",
            "STALE",
            "STALE",
            True,
            "ALLOW_UNVERIFIED",
        ),
    ],
)
def test_operational_reconciliation_states(
    session,
    monkeypatch,
    gate,
    checked_at,
    expected_status,
    expected_gate,
    allowed,
    action,
):
    monkeypatch.setattr(
        health_service,
        "reconciliation_summary",
        lambda *args, **kwargs: {
            "status": "success",
            "competition_id": 2,
            "season": 2026,
            "primary_source": "live-score-api",
            "secondary_source": "openfootball-json",
            "total": 380,
            "overall_counts": (
                {"MATCH": 380}
                if gate == "PASS"
                else {"MATCH": 378, "SOURCE_LAG": 2}
                if gate == "WARN"
                else {"MATCH": 379, "CONFLICT": 1}
            ),
            "fixture_agreement_rate": (
                1.0
                if gate == "PASS"
                else 0.994737
            ),
            "quality_gate": gate,
            "last_checked_at": checked_at,
            "final_holdout_touched": False,
        },
    )

    result = health_service.reconciliation_health(
        session,
        competition_id=2,
        season=2026,
        stale_after_minutes=180,
        now=NOW,
    )

    assert result["status"] == "success"
    assert result["operational_status"] == expected_status
    assert result["quality_gate"] == expected_gate
    assert result["reported_quality_gate"] == gate
    assert result["prediction_lock_allowed"] is allowed
    assert result["action"] == action
    assert result["final_holdout_touched"] is False

    if expected_status == "STALE":
        assert result["freshness"] == "STALE"
        assert result["age_minutes"] > 180
    else:
        assert result["freshness"] == "FRESH"
        assert result["age_minutes"] <= 180


def test_uninitialized_reconciliation_is_visible_and_nonblocking(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        health_service,
        "reconciliation_summary",
        lambda *args, **kwargs: {
            "status": "not_initialized",
            "competition_id": 2,
            "season": 2026,
            "total": 0,
            "quality_gate": "UNKNOWN",
            "final_holdout_touched": False,
        },
    )

    result = health_service.reconciliation_health(
        session,
        competition_id=2,
        season=2026,
        stale_after_minutes=180,
        now=NOW,
    )

    assert result["operational_status"] == "UNINITIALIZED"
    assert result["quality_gate"] == "UNKNOWN"
    assert result["reported_quality_gate"] == "UNKNOWN"
    assert result["freshness"] == "NOT_INITIALIZED"
    assert result["age_minutes"] is None
    assert result["prediction_lock_allowed"] is True
    assert result["action"] == "ALLOW_UNVERIFIED"
    assert result["issue_count"] == 0
    assert result["final_holdout_touched"] is False


def test_missing_timestamp_becomes_stale(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        health_service,
        "reconciliation_summary",
        lambda *args, **kwargs: {
            "status": "success",
            "competition_id": 2,
            "season": 2026,
            "total": 380,
            "overall_counts": {"MATCH": 380},
            "quality_gate": "PASS",
            "last_checked_at": None,
            "final_holdout_touched": False,
        },
    )

    result = health_service.reconciliation_health(
        session,
        competition_id=2,
        season=2026,
        stale_after_minutes=180,
        now=NOW,
    )

    assert result["operational_status"] == "STALE"
    assert result["quality_gate"] == "STALE"
    assert result["reported_quality_gate"] == "PASS"
    assert result["freshness"] == "MISSING_TIMESTAMP"
    assert result["prediction_lock_allowed"] is True
    assert result["action"] == "ALLOW_UNVERIFIED"
    assert result["final_holdout_touched"] is False
