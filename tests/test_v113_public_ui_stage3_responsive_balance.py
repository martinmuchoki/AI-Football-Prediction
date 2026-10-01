from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

CSS = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "styles.css"
).read_text(encoding="utf-8")

JS = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "app.js"
).read_text(encoding="utf-8")


def test_final_responsive_balance_layer_exists():
    assert (
        "BEGIN MDRN SPORTSQ STAGE 3 "
        "CANONICAL MATCH LAYOUT"
        in CSS
    )

    assert (
        "END MDRN SPORTSQ STAGE 3 "
        "CANONICAL MATCH LAYOUT"
        in CSS
    )


def test_predict_and_scorecall_have_visual_context():
    assert 'content: "1X2 OUTLOOK"' in CSS
    assert 'content: "PROJECTED SCORE"' in CSS


def test_confidence_keeps_real_data_band():
    assert (
        ".stage3-headline-stat > small"
        in CSS
    )

    assert (
        ".stage3-headline-stat:nth-child(3)::after"
        in CSS
    )

    assert "content: none" in CSS


def test_match_centre_uses_responsive_editorial_padding():
    assert (
        "clamp(32px, 6vw, 72px)"
        in CSS
    )

    assert (
        "max-width: 1180px !important"
        in CSS
    )


def test_three_card_desktop_grid_is_preserved():
    assert (
        "repeat(3, minmax(0, 1fr))"
        in CSS
    )


def test_tablet_nav_containment_exists():
    assert "@media (max-width: 1100px)" in CSS
    assert ".site-header" in CSS
    assert ".main-nav" in CSS


def test_mobile_card_stack_exists():
    assert "@media (max-width: 720px)" in CSS
    assert "grid-template-columns: 1fr" in CSS


def test_stage3_deeper_intelligence_remains():
    assert "View deeper intelligence" in JS
    assert "SPORTSQ FORM INDEX" in JS
    assert "SPORTSQ NEWS IMPACT" in JS
