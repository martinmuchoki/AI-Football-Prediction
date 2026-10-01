from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

JS = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "app.js"
).read_text(encoding="utf-8")

CSS = (
    ROOT
    / "app"
    / "static"
    / "public"
    / "styles.css"
).read_text(encoding="utf-8")


MARKER = (
    "MDRN SPORTSQ STAGE 3 FINAL CLEANUP "
    "- DEEPER INTELLIGENCE ONLY"
)


def deeper():
    start = JS.index(MARKER)

    end = JS.index(
        "</details>",
        start,
    )

    return JS[start:end]


def test_deeper_control_exists():
    assert "View deeper intelligence" in JS


def test_duplicate_primary_metrics_removed():
    block = deeper()

    assert "SPORTSQ PREDICT" not in block
    assert "SPORTSQ SCORECALL" not in block
    assert "SPORTSQ CONFIDENCE" not in block


def test_form_and_news_remain():
    block = deeper()

    assert "SPORTSQ FORM INDEX" in block
    assert "SPORTSQ NEWS IMPACT" in block


def test_editorial_alignment():
    assert ".matches-section > .matches-inner" in CSS
    assert "max-width: 1180px !important" in CSS


def test_three_column_primary_row():
    assert (
        "repeat(3, minmax(0, 1fr))"
        in CSS
    )


def test_mobile_single_column():
    assert "@media (max-width: 720px)" in CSS
    assert "grid-template-columns: 1fr" in CSS
