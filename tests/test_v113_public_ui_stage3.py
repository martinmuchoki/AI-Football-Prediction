from pathlib import Path

from fastapi.testclient import TestClient

from app.main import app


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


CLEANUP_MARKER = (
    "MDRN SPORTSQ STAGE 3 FINAL CLEANUP "
    "- DEEPER INTELLIGENCE ONLY"
)


def deeper_block():
    start = JS.index(CLEANUP_MARKER)

    end = JS.index(
        "</details>",
        start,
    )

    return JS[start:end]


def test_primary_match_summary_exists():
    assert "SportsQ Predict" in JS
    assert "ScoreCall" in JS
    assert "Confidence" in JS


def test_deeper_intelligence_control_exists():
    # The summary/control appears before CLEANUP_MARKER.
    assert "View deeper intelligence" in JS


def test_deeper_intelligence_has_no_primary_duplication():
    block = deeper_block()

    assert "SPORTSQ FORM INDEX" in block
    assert "SPORTSQ NEWS IMPACT" in block

    assert "SPORTSQ PREDICT" not in block
    assert "SPORTSQ SCORECALL" not in block
    assert "SPORTSQ CONFIDENCE" not in block


def test_stage3_styles_exist():
    assert ".stage3-headline-grid" in CSS
    assert ".stage3-deeper-detail" in CSS
    assert ".stage3-deeper-grid" in CSS
    assert ".stage3-form-grid" in CSS


def test_public_api_remains_sanitized():
    client = TestClient(app)

    response = client.get(
        "/api/v1/public/sportsq"
        "?competition=39&season=2026&limit=5"
    )

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "success"

    for item in payload.get("items", []):
        fixture = item.get("fixture") or {}

        assert "prediction_lock" not in item
        assert "provider_fixture_id" not in item

        assert "prediction_lock" not in fixture
        assert "provider_fixture_id" not in fixture


def test_all_five_intelligence_layers_remain_in_api():
    client = TestClient(app)

    response = client.get(
        "/api/v1/public/sportsq"
        "?competition=39&season=2026&limit=5"
    )

    assert response.status_code == 200

    payload = response.json()

    for item in payload.get("items", []):
        assert "sportsq_predict" in item
        assert "sportsq_score_call" in item
        assert "sportsq_confidence" in item
        assert "sportsq_form_index" in item
        assert "sportsq_news_impact" in item
