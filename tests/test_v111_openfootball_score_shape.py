from app.services.fixture_reconciliation import _openfootball_score


def test_direct_zero_zero_score_is_preserved():
    assert (
        _openfootball_score(
            {"score": [0, 0]}
        )
        == "0-0"
    )


def test_direct_nonzero_score_is_preserved():
    assert (
        _openfootball_score(
            {"score": [2, 1]}
        )
        == "2-1"
    )


def test_direct_tuple_score_is_preserved():
    assert (
        _openfootball_score(
            {"score": (3, 0)}
        )
        == "3-0"
    )


def test_legacy_ft_mapping_remains_supported():
    assert (
        _openfootball_score(
            {
                "score": {
                    "ft": [0, 0]
                }
            }
        )
        == "0-0"
    )


def test_missing_or_invalid_score_remains_missing():
    assert _openfootball_score({}) is None
    assert _openfootball_score({"score": None}) is None
    assert _openfootball_score({"score": []}) is None
    assert _openfootball_score({"score": [1]}) is None
