from __future__ import annotations

from pathlib import Path

from app.main import app

ROOT = Path(__file__).resolve().parents[1]

def test_gui_polish_route_exists():
    paths = {route.path for route in app.routes}
    assert "/api/v1/sportsq/stage3/generate/{fixture_id}" in paths

def test_dashboard_has_accuracy_cards_and_modal():
    html = (ROOT / "app" / "static" / "sportsq" / "index.html").read_text(
        encoding="utf-8"
    )
    assert 'id="accuracyLocked"' in html
    assert 'id="accuracyGraded"' in html
    assert 'id="accuracyPending"' in html
    assert 'id="accuracyPercent"' in html
    assert 'id="detailModal"' in html

def test_dashboard_js_has_fixture_generation_and_scorecall_no_fabrication():
    js = (ROOT / "app" / "static" / "sportsq" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "generateFixtureContent" in js
    assert "/api/v1/sportsq/stage3/generate/${fixtureId}" in js
    assert 'hasScore ? "ACTIVE" : "AWAITING DATA"' in js
    assert "Not exposed by lock" in js

def test_dashboard_js_has_confidence_bands_and_kickoff():
    js = (ROOT / "app" / "static" / "sportsq" / "app.js").read_text(
        encoding="utf-8"
    )
    assert "confidenceBand" in js
    assert "formatKickoff" in js
    assert "HIGH" in js
    assert "MEDIUM" in js
    assert "LOW" in js

def test_gui_keeps_approved_brand_colours():
    css = (ROOT / "app" / "static" / "sportsq" / "styles.css").read_text(
        encoding="utf-8"
    ).lower()
    assert "--navy: #07111f;" in css
    assert "--lime: #c7ff2e;" in css
    assert "--white: #ffffff;" in css
