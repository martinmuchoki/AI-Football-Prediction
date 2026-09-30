from pathlib import Path

from app.main import _sportsq_public_item, app


ROOT = Path(__file__).resolve().parents[1]
PUBLIC = ROOT / "app" / "static" / "public"


def _route(path: str):
    return next(route for route in app.routes if route.path == path)


def test_public_routes_and_operator_console_coexist():
    paths = [route.path for route in app.routes]

    assert "/" in paths
    assert "/api/v1/public/sportsq" in paths
    assert "/sportsq" in paths


def test_public_endpoint_is_not_bound_to_private_guard():
    route = _route("/api/v1/public/sportsq")
    dependencies = getattr(route, "dependencies", []) or []

    assert dependencies == []


def test_private_intelligence_remains_guarded():
    route = _route("/api/v1/sportsq/intelligence")
    dependencies = getattr(route, "dependencies", []) or []

    assert len(dependencies) >= 1


def test_public_whitelist():
    source = {
        "fixture": {
            "fixture_id": 10,
            "provider_fixture_id": 999,
            "home_team": "Home FC",
            "away_team": "Away FC",
            "kickoff_utc": "2026-10-01T18:00:00+00:00",
        },
        "sportsq_predict": {
            "name": "SportsQ Predict",
            "prediction": "HOME",
            "status": "ready",
            "source": "private",
            "probabilities": {"home": 0.6},
        },
        "sportsq_score_call": {
            "name": "SportsQ ScoreCall",
            "score": "2-1",
            "status": "ready",
            "source": "private",
        },
        "sportsq_confidence": {
            "name": "SportsQ Confidence",
            "percent": 71.2,
            "band": "HIGH",
            "status": "ready",
            "source": "private",
        },
        "sportsq_form_index": {
            "name": "SportsQ Form Index",
            "window": 5,
            "method": "private",
            "home": {
                "team": "Home FC",
                "index": 80.0,
                "band": "strong",
                "wins": 4,
            },
            "away": {
                "team": "Away FC",
                "index": 55.0,
                "band": "mixed",
                "wins": 2,
            },
            "lookahead_protection": True,
        },
        "sportsq_news_impact": {
            "name": "SportsQ News Impact",
            "status": "ready",
            "impact_score": 1.5,
            "direction": "home-positive",
            "verified": True,
            "note": "Verified team news.",
            "analysis_cutoff_at": "private",
            "model_name": "private",
            "model_version": "private",
            "fabricated": False,
        },
        "prediction_lock": {
            "policy_version": "private",
            "locked_at": "private",
            "publish": True,
            "immutable_source": True,
        },
        "safety": {
            "historical_holdout_touched": False,
        },
    }

    public = _sportsq_public_item(source)

    assert public["fixture"] == {
        "home_team": "Home FC",
        "away_team": "Away FC",
        "kickoff_utc": "2026-10-01T18:00:00+00:00",
    }

    assert public["sportsq_predict"]["prediction"] == "HOME"
    assert public["sportsq_score_call"]["score"] == "2-1"
    assert public["sportsq_confidence"]["percent"] == 71.2

    serialized = repr(public)

    forbidden = (
        "provider_fixture_id",
        "fixture_id",
        "prediction_lock",
        "safety",
        "probabilities",
        "source",
        "model_name",
        "model_version",
        "analysis_cutoff_at",
        "impact_score",
        "lookahead_protection",
        "method",
        "wins",
    )

    for field in forbidden:
        assert field not in serialized


def test_public_static_files():
    index = (PUBLIC / "index.html").read_text(encoding="utf-8")
    css = (PUBLIC / "styles.css").read_text(encoding="utf-8")
    js = (PUBLIC / "app.js").read_text(encoding="utf-8")

    assert "MDRN SportsQ" in index
    assert "SportsQ Predict" in index
    assert "SportsQ ScoreCall" in index
    assert "SportsQ Confidence" in index
    assert "SportsQ Form Index" in index
    assert "SportsQ News Impact" in index

    assert "/api/v1/public/sportsq" in js

    combined = (index + css + js).lower()

    assert "x-api-key" not in combined
    assert "own_api_key" not in combined
    assert "sportsq_x_api_key" not in combined


def test_public_page_does_not_link_internal_console():
    index = (PUBLIC / "index.html").read_text(encoding="utf-8")

    assert 'href="/sportsq"' not in index


def test_existing_operator_assets_remain():
    operator = ROOT / "app" / "static" / "sportsq"

    assert (operator / "index.html").is_file()
    assert (operator / "styles.css").is_file()
    assert (operator / "app.js").is_file()
