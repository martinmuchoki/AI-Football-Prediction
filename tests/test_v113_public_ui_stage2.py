"""Public UI Stage 2 Gate 3 regression tests."""

from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)

ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "app" / "static" / "public"


def test_public_endpoint_defaults_to_api_football_premier_league():
    with patch(
        "app.main.list_sportsq_intelligence",
        return_value={
            "status": "success",
            "items": [],
        },
    ) as mocked:
        response = client.get("/api/v1/public/sportsq")

    assert response.status_code == 200

    payload = response.json()

    assert payload["competition_id"] == 39
    assert payload["season"] == 2026

    kwargs = mocked.call_args.kwargs

    assert kwargs["competition_id"] == 39
    assert kwargs["season"] == 2026


def test_explicit_public_competition_parameter_still_supported():
    with patch(
        "app.main.list_sportsq_intelligence",
        return_value={
            "status": "success",
            "items": [],
        },
    ) as mocked:
        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=2&season=2025"
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["competition_id"] == 2
    assert payload["season"] == 2025

    kwargs = mocked.call_args.kwargs

    assert kwargs["competition_id"] == 2
    assert kwargs["season"] == 2025


def test_public_js_uses_api_football_default_and_public_endpoint_only():
    js = (PUBLIC / "app.js").read_text(encoding="utf-8-sig")

    assert "DEFAULT_COMPETITION = 39" in js
    assert "/api/v1/public/sportsq" in js

    assert "/api/v1/sportsq/intelligence" not in js
    assert "X-API-Key" not in js
    assert "x-api-key" not in js.lower()


def test_public_ui_identifies_premier_league():
    html = (PUBLIC / "index.html").read_text(encoding="utf-8-sig")

    assert 'value="39"' in html
    assert "Premier League" in html


def test_public_ui_has_safe_empty_state_copy():
    js = (PUBLIC / "app.js").read_text(encoding="utf-8-sig")

    assert "No upcoming intelligence published yet" in js
    assert "publication safety gate" in js
    assert "No unverified prediction data will" in js


def test_public_ui_formats_kickoff_for_browser_locale():
    js = (PUBLIC / "app.js").read_text(encoding="utf-8-sig")

    assert "Intl.DateTimeFormat" in js
    assert "timeZoneName" in js


def test_public_ui_keeps_public_response_sanitized_client_side():
    js = (PUBLIC / "app.js").read_text(encoding="utf-8-sig")

    forbidden = [
        "provider_fixture_id",
        "model_version",
        "model_name",
        "analysis_cutoff_at",
        "lookahead_protection",
    ]

    for field in forbidden:
        assert field not in js
