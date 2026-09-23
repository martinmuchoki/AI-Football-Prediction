from datetime import date, timedelta

from app.services.fixture_reconciliation import _statuses


def _row(
    fixture_date: date,
    *,
    finished: bool = False,
    score=None,
):
    return {
        "date": fixture_date.isoformat(),
        "finished": finished,
        "score": score,
    }


def test_v107_exact_date_stays_match():
    base = date.today() + timedelta(days=10)

    d, r, o = _statuses(
        _row(base),
        _row(base),
    )

    assert d == "MATCH"
    assert r == "PENDING"
    assert o == "MATCH"


def test_v107_one_day_future_variance_is_source_lag():
    base = date.today() + timedelta(days=10)

    d, r, o = _statuses(
        _row(base),
        _row(base + timedelta(days=1)),
    )

    assert d == "SOURCE_LAG"
    assert r == "PENDING"
    assert o == "SOURCE_LAG"


def test_v107_two_day_future_variance_is_source_lag():
    base = date.today() + timedelta(days=10)

    d, r, o = _statuses(
        _row(base),
        _row(base + timedelta(days=2)),
    )

    assert d == "SOURCE_LAG"
    assert r == "PENDING"
    assert o == "SOURCE_LAG"


def test_v107_three_day_variance_is_conflict():
    base = date.today() + timedelta(days=10)

    d, r, o = _statuses(
        _row(base),
        _row(base + timedelta(days=3)),
    )

    assert d == "CONFLICT"
    assert r == "PENDING"
    assert o == "CONFLICT"


def test_v107_past_variance_is_conflict():
    base = date.today() - timedelta(days=10)

    d, r, o = _statuses(
        _row(base),
        _row(base + timedelta(days=1)),
    )

    assert d == "CONFLICT"
    assert r == "PENDING"
    assert o == "CONFLICT"


def test_v107_finished_date_variance_is_conflict():
    base = date.today() + timedelta(days=10)

    d, r, o = _statuses(
        _row(base, finished=True, score=(2, 1)),
        _row(
            base + timedelta(days=1),
            finished=True,
            score=(2, 1),
        ),
    )

    assert d == "CONFLICT"
    assert r == "MATCH"
    assert o == "CONFLICT"


def test_v107_finished_score_mismatch_is_conflict():
    base = date.today() + timedelta(days=10)

    d, r, o = _statuses(
        _row(base, finished=True, score=(2, 1)),
        _row(base, finished=True, score=(1, 1)),
    )

    assert d == "MATCH"
    assert r == "CONFLICT"
    assert o == "CONFLICT"


def test_v107_unfinished_fixture_with_score_is_conflict():
    base = date.today() + timedelta(days=10)

    d, r, o = _statuses(
        _row(base, finished=False, score=(1, 0)),
        _row(
            base + timedelta(days=1),
            finished=False,
            score=None,
        ),
    )

    assert d == "CONFLICT"
    assert r == "PENDING"
    assert o == "CONFLICT"
