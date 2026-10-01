from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

INDEX = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "index.html"
).read_text(encoding="utf-8")

CSS = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "styles.css"
).read_text(encoding="utf-8")


def test_matches_inner_exists_once():
    assert (
        INDEX.count(
            'class="matches-inner"'
        )
        == 1
    )


def test_match_grid_follows_matches_inner():
    inner = INDEX.index(
        'class="matches-inner"'
    )

    grid = INDEX.index(
        'id="match-grid"',
        inner,
    )

    assert grid > inner


def test_background_is_separate_from_inner_rail():
    assert ".matches-section {" in CSS

    assert (
        ".matches-section > .matches-inner"
        in CSS
    )


def test_inner_rail_is_1180_max():
    assert (
        "max-width: 1180px !important"
        in CSS
    )


def test_narrow_rail_has_18px_side_gutters():
    assert (
        "calc(100% - 36px)"
        in CSS
    )


def test_match_grid_cannot_escape_inner():
    assert (
        ".matches-inner .match-grid"
        in CSS
    )


def test_mobile_prediction_stack_remains():
    assert "@media (max-width: 720px)" in CSS

    assert (
        "grid-template-columns: 1fr"
        in CSS
    )
