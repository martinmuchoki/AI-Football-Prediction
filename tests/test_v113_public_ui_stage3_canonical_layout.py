from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

INDEX = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "index.html"
).read_text(
    encoding="utf-8-sig"
)

CSS = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "styles.css"
).read_text(
    encoding="utf-8-sig"
)


def test_single_matches_inner():
    assert (
        INDEX.count(
            'class="matches-inner"'
        )
        == 1
    )


def test_single_canonical_layer():
    assert (
        CSS.count(
            "BEGIN MDRN SPORTSQ STAGE 3 "
            "CANONICAL MATCH LAYOUT"
        )
        == 1
    )


def test_background_only_is_full_width():
    assert ".matches-section {" in CSS
    assert "max-width: none !important" in CSS


def test_editorial_rail():
    assert (
        ".matches-section > .matches-inner"
        in CSS
    )

    assert (
        "max-width: 1180px !important"
        in CSS
    )


def test_grid_and_card_containment():
    assert (
        ".matches-inner .match-grid"
        in CSS
    )

    assert (
        ".matches-inner .stage3-match-card"
        in CSS
    )


def test_three_primary_cards():
    assert (
        "repeat(3, minmax(0, 1fr))"
        in CSS
    )


def test_real_confidence_band_preserved():
    assert (
        ".stage3-headline-stat > small"
        in CSS
    )


def test_editorial_vertical_spacing_preserved():
    assert (
        "clamp(32px, 6vw, 72px)"
        in CSS
    )


def test_deeper_intelligence_contained():
    assert (
        ".matches-inner .stage3-deeper-grid"
        in CSS
    )

    assert (
        ".matches-inner .stage3-form-grid"
        in CSS
    )


def test_mobile_stack():
    assert "@media (max-width: 720px)" in CSS

    assert (
        "grid-template-columns: 1fr"
        in CSS
    )
