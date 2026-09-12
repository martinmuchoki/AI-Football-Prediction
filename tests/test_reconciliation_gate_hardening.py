from app.services.market_refresh import (
    M6D_MIN_WARN_AGREEMENT,
    _reconciliation_prediction_lock_decision,
)


def decision(gate, agreement):
    return _reconciliation_prediction_lock_decision(
        gate,
        agreement,
    )


def test_m6d_threshold_constant():
    assert M6D_MIN_WARN_AGREEMENT == 0.99


def test_pass_allows():
    assert decision("PASS", None) == (
        False,
        None,
    )


def test_warn_current_production_agreement_allows():
    assert decision(
        "WARN",
        0.994737,
    ) == (
        False,
        None,
    )


def test_warn_exact_threshold_allows():
    assert decision(
        "WARN",
        0.99,
    ) == (
        False,
        None,
    )


def test_warn_below_threshold_blocks():
    blocked, reason = decision(
        "WARN",
        0.989999,
    )

    assert blocked is True
    assert reason == (
        "RECONCILIATION_WARN_LOW_AGREEMENT"
    )


def test_warn_historical_038_blocks():
    blocked, reason = decision(
        "WARN",
        0.38,
    )

    assert blocked is True
    assert reason == (
        "RECONCILIATION_WARN_LOW_AGREEMENT"
    )


def test_warn_missing_agreement_blocks():
    blocked, reason = decision(
        "WARN",
        None,
    )

    assert blocked is True
    assert reason == (
        "RECONCILIATION_WARN_INVALID_AGREEMENT"
    )


def test_warn_bad_text_blocks():
    blocked, reason = decision(
        "WARN",
        "not-a-number",
    )

    assert blocked is True
    assert reason == (
        "RECONCILIATION_WARN_INVALID_AGREEMENT"
    )


def test_warn_nan_blocks():
    blocked, reason = decision(
        "WARN",
        float("nan"),
    )

    assert blocked is True
    assert reason == (
        "RECONCILIATION_WARN_INVALID_AGREEMENT"
    )


def test_unknown_blocks():
    assert decision(
        "UNKNOWN",
        None,
    ) == (
        True,
        "RECONCILIATION_UNKNOWN",
    )


def test_stale_blocks():
    assert decision(
        "STALE",
        1.0,
    ) == (
        True,
        "RECONCILIATION_STALE",
    )


def test_fail_blocks():
    assert decision(
        "FAIL",
        1.0,
    ) == (
        True,
        "RECONCILIATION_FAIL",
    )


def test_unrecognized_gate_blocks():
    blocked, reason = decision(
        "SOMETHING_NEW",
        1.0,
    )

    assert blocked is True
    assert reason == (
        "RECONCILIATION_UNRECOGNIZED_SOMETHING_NEW"
    )


def test_market_safety_missing_prediction_permission_is_fail_closed():
    from pathlib import Path

    source = Path(
        "app/services/market_safety.py"
    ).read_text(
        encoding="utf-8"
    )

    expected = (
        '"prediction_lock_allowed",\n'
        '            False,'
    )

    assert expected in source
