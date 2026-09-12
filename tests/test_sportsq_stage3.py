from __future__ import annotations

import json
from pathlib import Path

import pytest
from PIL import Image

from app.main import app
from app.services.sportsq_stage3 import (
    BRAND,
    THEME,
    approve_queue_item,
    cancel_queue_item,
    enqueue_package,
    list_queue,
    platform_readiness,
    process_queue_item,
    render_news_card,
    render_prediction_card,
)

def sample_item():
    return {
        "fixture": {
            "fixture_id": 10,
            "home_team": "Chelsea",
            "away_team": "Arsenal",
            "kickoff_utc": "2026-09-12T16:00:00+00:00",
        },
        "sportsq_predict": {
            "prediction": "HOME",
            "status": "AVAILABLE",
        },
        "sportsq_score_call": {
            "score": None,
            "status": "NOT_EXPOSED_BY_EXISTING_LOCK",
        },
        "sportsq_confidence": {
            "percent": 67.4,
            "band": "HIGH",
        },
        "sportsq_form_index": {
            "home": {"index": 72.0},
            "away": {"index": 58.0},
        },
        "sportsq_news_impact": {
            "status": "NO_STRUCTURED_TEAM_NEWS_INPUT",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
        },
    }

def test_brand_and_approved_theme():
    assert BRAND == "MDRN SportsQ"
    assert THEME["navy"] == "#07111F"
    assert THEME["lime"] == "#C7FF2E"
    assert THEME["white"] == "#FFFFFF"

def test_prediction_square_render(tmp_path: Path):
    path = render_prediction_card(sample_item(), tmp_path / "square.png")
    assert path.exists()
    with Image.open(path) as image:
        assert image.size == (1080, 1080)

def test_prediction_vertical_render(tmp_path: Path):
    path = render_prediction_card(
        sample_item(),
        tmp_path / "vertical.png",
        vertical=True,
    )
    assert path.exists()
    with Image.open(path) as image:
        assert image.size == (1080, 1920)

def test_news_card_is_not_fabricated(tmp_path: Path):
    result = render_news_card(
        sample_item(),
        tmp_path / "news.png",
    )
    assert result["created"] is False
    assert result["reason"] == "NO_VERIFIED_STRUCTURED_TEAM_NEWS"
    assert not (tmp_path / "news.png").exists()

def test_platform_readiness_never_exposes_secret_values(monkeypatch):
    monkeypatch.setenv("SPORTSQ_META_ACCESS_TOKEN", "TEST-SECRET")
    monkeypatch.setenv("SPORTSQ_META_INSTAGRAM_ACCOUNT_ID", "123")
    result = platform_readiness()
    assert result["instagram"]["config_present"] is True
    assert result["instagram"]["live_posting_verified"] is False
    assert result["instagram"]["secret_values_exposed"] is False
    assert "TEST-SECRET" not in json.dumps(result)

def create_manifest(root: Path, package_id: str = "pkg-1"):
    package = root / "packages" / package_id
    package.mkdir(parents=True)
    manifest = {
        "package_id": package_id,
        "rights": {
            "third_party_clips_used": False,
            "attribution_is_not_license": True,
        },
    }
    (package / "manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )

def test_queue_approval_and_manual_export(tmp_path: Path):
    create_manifest(tmp_path)
    item = enqueue_package(
        "pkg-1",
        platforms=["manual_export"],
        content_root=tmp_path,
    )
    assert item["status"] == "DRAFT"

    approved = approve_queue_item(
        item["queue_id"],
        content_root=tmp_path,
    )
    assert approved["status"] == "APPROVED"

    processed = process_queue_item(
        item["queue_id"],
        content_root=tmp_path,
    )
    assert processed["status"] == "EXPORTED"
    assert processed["platform_results"]["manual_export"]["network_request_sent"] is False

def test_external_platform_is_not_falsely_published(tmp_path: Path):
    create_manifest(tmp_path)
    item = enqueue_package(
        "pkg-1",
        platforms=["instagram"],
        content_root=tmp_path,
    )
    approve_queue_item(
        item["queue_id"],
        content_root=tmp_path,
    )

    processed = process_queue_item(
        item["queue_id"],
        content_root=tmp_path,
    )
    assert processed["status"] == "APPROVED"
    assert processed["platform_results"]["instagram"]["status"] in {
        "NOT_CONFIGURED",
        "CONNECTOR_REQUIRES_LIVE_VERIFICATION",
    }
    assert processed["platform_results"]["instagram"]["network_request_sent"] is False

def test_unapproved_item_cannot_process(tmp_path: Path):
    create_manifest(tmp_path)
    item = enqueue_package(
        "pkg-1",
        platforms=["manual_export"],
        content_root=tmp_path,
    )
    with pytest.raises(ValueError):
        process_queue_item(
            item["queue_id"],
            content_root=tmp_path,
        )

def test_invalid_platform_rejected(tmp_path: Path):
    create_manifest(tmp_path)
    with pytest.raises(ValueError):
        enqueue_package(
            "pkg-1",
            platforms=["not-a-platform"],
            content_root=tmp_path,
        )

def test_cancel_queue_item(tmp_path: Path):
    create_manifest(tmp_path)
    item = enqueue_package(
        "pkg-1",
        platforms=["manual_export"],
        content_root=tmp_path,
    )
    cancelled = cancel_queue_item(
        item["queue_id"],
        content_root=tmp_path,
    )
    assert cancelled["status"] == "CANCELLED"

def test_queue_persists(tmp_path: Path):
    create_manifest(tmp_path)
    enqueue_package(
        "pkg-1",
        platforms=["manual_export"],
        content_root=tmp_path,
    )
    assert len(list_queue(content_root=tmp_path)) == 1

def test_stage3_routes_and_gui_exist():
    paths = {route.path for route in app.routes}
    required = {
        "/sportsq",
        "/api/v1/sportsq/stage3/status",
        "/api/v1/sportsq/stage3/preview",
        "/api/v1/sportsq/stage3/generate",
        "/api/v1/sportsq/stage3/queue",
        "/api/v1/sportsq/stage3/queue/{queue_id}/approve",
        "/api/v1/sportsq/stage3/queue/{queue_id}/cancel",
        "/api/v1/sportsq/stage3/queue/{queue_id}/process",
    }
    assert required.issubset(paths)
