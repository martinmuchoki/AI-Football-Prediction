from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

CSS = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "styles.css"
).read_text(encoding="utf-8")


def test_visual_polish_layer_exists():
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


def test_match_section_alignment():
    assert "width: min(1180px, calc(100% - 36px)) !important" in CSS
    assert "scroll-margin-top: 92px" in CSS


def test_header_clipping_fix():
    assert ".matches-inner .stage3-match-card .match-card__topline" in CSS
    assert "position: static !important" in CSS
    assert "padding-top: 24px" in CSS


def test_three_column_desktop_intelligence():
    assert "repeat(3, minmax(0, 1fr))" in CSS
    assert ".stage3-form-panel" in CSS
    assert ".stage3-news-panel" in CSS


def test_mobile_breakpoint_preserved():
    assert "@media (max-width: 720px)" in CSS
