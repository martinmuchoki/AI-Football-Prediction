from __future__ import annotations

from app.config import Settings
from app.services.market_refresh import refresh_live_market


async def test_market_refresh_requires_live_score_credentials(session):
    settings = Settings(ls_api_key="", ls_api_secret="")
    result = await refresh_live_market(
        session,
        settings,
        competition_id=2,
        season=2026,
    )
    assert result["status"] == "config_required"
    assert result["final_holdout_touched"] is False
    assert "credential" in result["detail"].lower()

class _FakeLiveScoreClient:
    def __init__(self, settings):
        self.settings = settings

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False


async def test_reconciliation_gate_controls_new_prediction_locks(session, monkeypatch):
    import app.services.market_refresh as service

    settings = Settings(
        _env_file=None,
        ls_api_key="test-key",
        ls_api_secret="test-secret",
    )

    monkeypatch.setattr(service, "LiveScoreApiClient", _FakeLiveScoreClient)
    monkeypatch.setattr(service, "seed_default_sources", lambda session: None)
    monkeypatch.setattr(service, "get_source", lambda session, slug: object())
    monkeypatch.setattr(service, "mark_source_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "mark_source_failure", lambda *args, **kwargs: None)

    async def fake_sync(*args, **kwargs):
        return {
            "status": "success",
            "received": 1,
            "inserted": 0,
            "updated": 1,
            "rejected": 0,
            "pages_processed": 1,
            "failed_page": None,
        }

    monkeypatch.setattr(service, "sync_live_score_fixtures", fake_sync)

    odds_calls = {"count": 0}

    def fake_odds(*args, **kwargs):
        odds_calls["count"] += 1
        return {
            "captured_new": 1,
            "unchanged": 0,
            "missing_or_invalid_odds": 0,
        }

    monkeypatch.setattr(service, "capture_pre_match_odds", fake_odds)

    for gate, expected_status, expected_action, should_generate in (
        ("PASS", "success", "ALLOW", True),
        ("WARN", "partial", "ALLOW_DEGRADED", True),
        ("UNKNOWN", "blocked", "BLOCK_NEW_PREDICTIONS", False),
        ("FAIL", "blocked", "BLOCK_NEW_PREDICTIONS", False),
    ):
        prediction_calls = {"count": 0}

        monkeypatch.setattr(
            service,
            "reconciliation_summary",
            lambda *args, _gate=gate, **kwargs: {
                "status": "success" if _gate != "UNKNOWN" else "not_initialized",
                "quality_gate": _gate,
                "fixture_agreement_rate": (
                    1.0
                    if _gate == "PASS"
                    else 0.994737
                    if _gate == "WARN"
                    else None
                ),
                "overall_counts": {},
                "last_checked_at": (
                    service.datetime.now(service.timezone.utc).isoformat()
                    if _gate != "UNKNOWN"
                    else None
                ),
                "final_holdout_touched": False,
            },
        )

        def fake_predictions(*args, **kwargs):
            prediction_calls["count"] += 1
            return {
                "locked_new": 1,
                "already_locked": 2,
                "high_confidence": 1,
                "pass": 0,
            }

        monkeypatch.setattr(service, "generate_live_predictions", fake_predictions)

        result = await service.refresh_live_market(
            session,
            settings,
            competition_id=2,
            season=2026,
        )

        assert result["status"] == expected_status
        assert result["reconciliation_quality"]["quality_gate"] == gate
        assert result["reconciliation_quality"]["action"] == expected_action
        assert result["prediction_lock"]["blocked"] is (
            gate in {"FAIL", "UNKNOWN"}
        )
        assert prediction_calls["count"] == (1 if should_generate else 0)

        if gate in {"FAIL", "UNKNOWN"}:
            assert result["prediction_lock"]["locked_new"] == 0
            assert result["prediction_lock"]["block_reason"] == (
                f"RECONCILIATION_{gate}"
            )
        else:
            assert result["prediction_lock"]["locked_new"] == 1
            assert result["prediction_lock"]["block_reason"] is None

    assert odds_calls["count"] == 4
    assert result["final_holdout_touched"] is False

async def test_stale_reconciliation_blocks_prediction_locking(session, monkeypatch):
    import app.services.market_refresh as service

    settings = Settings(
        _env_file=None,
        ls_api_key="test-key",
        ls_api_secret="test-secret",
        reconciliation_stale_after_minutes=180,
    )

    monkeypatch.setattr(service, "LiveScoreApiClient", _FakeLiveScoreClient)
    monkeypatch.setattr(service, "seed_default_sources", lambda session: None)
    monkeypatch.setattr(service, "get_source", lambda session, slug: object())
    monkeypatch.setattr(service, "mark_source_success", lambda *args, **kwargs: None)
    monkeypatch.setattr(service, "mark_source_failure", lambda *args, **kwargs: None)

    async def fake_sync(*args, **kwargs):
        return {
            "status": "success",
            "received": 1,
            "inserted": 0,
            "updated": 1,
            "rejected": 0,
            "pages_processed": 1,
            "failed_page": None,
        }

    monkeypatch.setattr(service, "sync_live_score_fixtures", fake_sync)
    monkeypatch.setattr(
        service,
        "capture_pre_match_odds",
        lambda *args, **kwargs: {
            "captured_new": 0,
            "unchanged": 1,
            "missing_or_invalid_odds": 0,
        },
    )

    for reported_gate in ("PASS", "FAIL"):
        monkeypatch.setattr(
            service,
            "reconciliation_summary",
            lambda *args, _gate=reported_gate, **kwargs: {
                "status": "success",
                "quality_gate": _gate,
                "fixture_agreement_rate": 1.0 if _gate == "PASS" else 0.9,
                "overall_counts": {},
                "last_checked_at": "2020-01-01T00:00:00+00:00",
                "final_holdout_touched": False,
            },
        )

        prediction_calls = {"count": 0}

        def fake_predictions(*args, **kwargs):
            prediction_calls["count"] += 1
            return {
                "locked_new": 1,
                "already_locked": 0,
                "high_confidence": 1,
                "pass": 0,
            }

        monkeypatch.setattr(service, "generate_live_predictions", fake_predictions)

        result = await service.refresh_live_market(
            session,
            settings,
            competition_id=2,
            season=2026,
        )

        quality = result["reconciliation_quality"]
        assert result["status"] == "blocked"
        assert quality["reported_quality_gate"] == reported_gate
        assert quality["quality_gate"] == "STALE"
        assert quality["freshness"] == "STALE"
        assert quality["action"] == "BLOCK_NEW_PREDICTIONS"
        assert quality["age_minutes"] > quality["stale_after_minutes"]
        assert result["prediction_lock"]["blocked"] is True
        assert result["prediction_lock"]["block_reason"] == "RECONCILIATION_STALE"
        assert prediction_calls["count"] == 0
        assert result["final_holdout_touched"] is False

