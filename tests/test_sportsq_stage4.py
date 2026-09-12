from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.main import app
from app.services.sportsq_source_rights import (
    MediaReuseBlockedError,
    STAGE4_TABLE_NAMES,
    assert_media_reusable,
    evaluate_media_reuse,
    list_sources,
    stage4_status,
)

ROOT = Path(__file__).resolve().parents[1]


def _asset():
    return {
        "asset_id": "demo",
        "source_id": "media-demo",
        "source_url": "https://example.invalid/media/demo",
        "attribution_text": "Example attribution",
        "verification_status": "VERIFIED",
        "reuse_verified": True,
    }


def _rights():
    return {
        "licence_code": "CC-BY-4.0",
        "commercial_use_allowed": True,
        "modification_allowed": True,
        "attribution_required": True,
        "share_alike_required": False,
        "trademark_check": "CLEAR",
        "personality_rights_check": "CLEAR",
        "evidence_url": "https://example.invalid/licence/demo",
        "verified_at": "2026-09-10T00:00:00+00:00",
        "reuse_verified": True,
    }


def test_stage4_schema_and_policies_are_ready():
    status = stage4_status()
    assert status["schema_ready"] is True
    assert set(status["tables"]) == STAGE4_TABLE_NAMES
    assert status["reuse_gate"] == "REUSE_VERIFIED"
    assert status["reuse_policy"] == "FAIL_CLOSED"
    assert status["network_media_downloads_enabled"] is False
    assert status["external_media_auto_import_enabled"] is False


def test_stage4_seed_counts_match_resources():
    official = json.loads(
        (
            ROOT
            / "resources"
            / "stage4"
            / "seed"
            / "official_football_sources.json"
        ).read_text(encoding="utf-8-sig")
    )
    media = json.loads(
        (
            ROOT
            / "resources"
            / "stage4"
            / "seed"
            / "reusable_media_sources.json"
        ).read_text(encoding="utf-8-sig")
    )

    rows = list_sources(status=None)

    assert len(rows) == len(official) + len(media)
    assert sum(
        row["source_kind"] == "OFFICIAL_FOOTBALL"
        for row in rows
    ) == len(official)
    assert sum(
        row["source_kind"] == "REUSABLE_MEDIA_PROVIDER"
        for row in rows
    ) == len(media)


def test_stage4_routes_are_read_only():
    wanted = {
        "/api/v1/sportsq/stage4/status",
        "/api/v1/sportsq/stage4/sources",
        "/api/v1/sportsq/stage4/media",
        "/api/v1/sportsq/stage4/media/{asset_id}",
        "/api/v1/sportsq/stage4/rights/{asset_id}",
    }

    route_map = {
        route.path: set(
            getattr(route, "methods", set()) or set()
        )
        for route in app.routes
    }

    for path in wanted:
        assert path in route_map
        assert "GET" in route_map[path]
        assert not (
            {"POST", "PUT", "PATCH", "DELETE"}
            & route_map[path]
        )


def test_verified_media_can_pass():
    decision = evaluate_media_reuse(
        _asset(),
        _rights(),
    )
    assert decision["allowed"] is True
    assert decision["reuse_verified"] is True


@pytest.mark.parametrize(
    "target,key,value,reason",
    [
        ("rights", "reuse_verified", False, "REUSE_NOT_VERIFIED"),
        (
            "rights",
            "commercial_use_allowed",
            False,
            "COMMERCIAL_USE_NOT_ALLOWED",
        ),
        (
            "rights",
            "modification_allowed",
            False,
            "MODIFICATION_NOT_ALLOWED",
        ),
        (
            "rights",
            "evidence_url",
            None,
            "RIGHTS_EVIDENCE_MISSING",
        ),
        (
            "rights",
            "licence_code",
            None,
            "LICENCE_UNKNOWN",
        ),
        (
            "rights",
            "trademark_check",
            "REVIEW",
            "TRADEMARK_CHECK_UNRESOLVED",
        ),
        (
            "rights",
            "personality_rights_check",
            "REVIEW",
            "PERSONALITY_RIGHTS_CHECK_UNRESOLVED",
        ),
        (
            "asset",
            "reuse_verified",
            False,
            "ASSET_REUSE_FLAG_FALSE",
        ),
        (
            "asset",
            "verification_status",
            "PENDING",
            "ASSET_NOT_VERIFIED",
        ),
        (
            "asset",
            "attribution_text",
            "",
            "ATTRIBUTION_TEXT_MISSING",
        ),
    ],
)
def test_reuse_gate_fails_closed(
    target,
    key,
    value,
    reason,
):
    asset = _asset()
    rights = _rights()

    (asset if target == "asset" else rights)[key] = value

    decision = evaluate_media_reuse(asset, rights)

    assert decision["allowed"] is False
    assert reason in decision["reasons"]


def test_missing_rights_fails_closed():
    decision = evaluate_media_reuse(_asset(), None)
    assert decision["allowed"] is False
    assert "RIGHTS_VERIFICATION_MISSING" in decision["reasons"]


def test_unknown_asset_is_blocked():
    with pytest.raises(MediaReuseBlockedError):
        assert_media_reusable(
            "stage4-definitely-missing-asset"
        )


def test_official_sources_are_not_media_licences():
    assert (
        stage4_status()[
            "official_sources_are_media_licences"
        ]
        is False
    )


def test_prediction_safety_flags_remain_read_only():
    assert stage4_status()["prediction_safety"] == {
        "prediction_engine_rewritten": False,
        "live_prediction_rows_modified": False,
        "existing_prediction_locks_modified": False,
        "historical_holdout_touched": False,
    }
