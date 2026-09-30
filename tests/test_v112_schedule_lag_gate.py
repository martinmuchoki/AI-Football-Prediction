"""MDRN SportsQ v1.0.12 schedule-lag prediction-gate regression tests."""

from types import SimpleNamespace

from app.services.fixture_reconciliation import (
    _prediction_gate_agreement_rate,
)
from app.services.market_refresh import (
    M6D_MIN_WARN_AGREEMENT,
    _reconciliation_prediction_lock_decision,
)


def _row(overall, date_status, result_status):
    return SimpleNamespace(
        overall_status=overall,
        date_status=date_status,
        result_status=result_status,
    )


def test_v112_threshold_remains_099():
    assert M6D_MIN_WARN_AGREEMENT == 0.99


def test_v112_schedule_only_source_lag_is_gate_acceptable():
    rows = [
        *[
            _row("MATCH", "MATCH", "PENDING")
            for _ in range(364)
        ],
        *[
            _row("SOURCE_LAG", "SOURCE_LAG", "PENDING")
            for _ in range(16)
        ],
    ]

    rate = _prediction_gate_agreement_rate(rows)

    assert rate == 1.0

    blocked, reason = _reconciliation_prediction_lock_decision(
        "WARN",
        rate,
    )

    assert blocked is False
    assert reason is None


def test_v112_result_source_lag_remains_nonagreement():
    rows = [
        *[
            _row("MATCH", "MATCH", "MATCH")
            for _ in range(99)
        ],
        _row("SOURCE_LAG", "MATCH", "SOURCE_LAG"),
    ]

    rate = _prediction_gate_agreement_rate(rows)

    assert rate == 0.99


def test_v112_result_source_lag_below_threshold_still_blocks():
    rows = [
        *[
            _row("MATCH", "MATCH", "MATCH")
            for _ in range(98)
        ],
        _row("SOURCE_LAG", "MATCH", "SOURCE_LAG"),
        _row("SOURCE_LAG", "MATCH", "SOURCE_LAG"),
    ]

    rate = _prediction_gate_agreement_rate(rows)

    assert rate == 0.98

    blocked, reason = _reconciliation_prediction_lock_decision(
        "WARN",
        rate,
    )

    assert blocked is True
    assert reason == "RECONCILIATION_WARN_LOW_AGREEMENT"


def test_v112_conflict_is_never_gate_acceptable():
    rows = [
        _row("MATCH", "MATCH", "MATCH"),
        _row("CONFLICT", "CONFLICT", "CONFLICT"),
    ]

    assert _prediction_gate_agreement_rate(rows) == 0.5


def test_v112_missing_source_is_never_gate_acceptable():
    rows = [
        _row("MATCH", "MATCH", "MATCH"),
        _row(
            "MISSING_SECONDARY",
            "MISSING_SECONDARY",
            "NOT_COMPARABLE",
        ),
    ]

    assert _prediction_gate_agreement_rate(rows) == 0.5


def test_v112_empty_rows_return_none():
    assert _prediction_gate_agreement_rate([]) is None
