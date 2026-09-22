from __future__ import annotations

from types import SimpleNamespace

import app.market_scheduler as scheduler


class _SessionContext:
    def __init__(self):
        self.session = object()

    def __enter__(self):
        return self.session

    def __exit__(self, exc_type, exc, tb):
        return False


class _Settings:
    public_web_timeout_seconds = 10

    def sportsq_competitions(self):
        return [(39, 2026)]

    def live_score_competitions(self):
        return [(2, 2026)]


class _FakeOpenFootballClient:
    def __init__(self, timeout_seconds):
        assert timeout_seconds == 10

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    async def fetch_league(self, *, season_label, league_file):
        assert season_label == "2026-27"
        assert league_file == "en.1.json"
        return SimpleNamespace(
            payload={
                "name": "Premier League 2026/27",
                "matches": [],
            }
        )


async def test_automatic_openfootball_reconciliation_fetches_stores_and_reconciles(monkeypatch):
    calls = {
        "seed": 0,
        "store": 0,
        "reconcile": 0,
    }

    monkeypatch.setattr(scheduler, "settings", _Settings())
    monkeypatch.setattr(
        scheduler,
        "OpenFootballJsonClient",
        _FakeOpenFootballClient,
    )
    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: _SessionContext(),
    )

    def fake_seed(session):
        calls["seed"] += 1

    def fake_store(session, *, document, season_label):
        calls["store"] += 1
        assert season_label == "2026-27"
        assert document.payload["name"] == "Premier League 2026/27"

    def fake_reconcile(
        session,
        *,
        payload,
        competition_id,
        season,
        primary_source,
    ):
        calls["reconcile"] += 1
        assert competition_id == 39
        assert season == 2026
        assert primary_source == "api-football"
        assert payload["name"] == "Premier League 2026/27"

        return {
            "status": "success",
            "quality_gate": "PASS",
            "fixture_agreement_rate": 1.0,
            "overall_counts": {"MATCH": 380},
            "final_holdout_touched": False,
        }

    monkeypatch.setattr(
        scheduler,
        "seed_known_source_policies",
        fake_seed,
    )
    monkeypatch.setattr(
        scheduler,
        "store_openfootball_document",
        fake_store,
    )
    monkeypatch.setattr(
        scheduler,
        "reconcile_openfootball_payload",
        fake_reconcile,
    )

    result = await scheduler.openfootball_reconciliation_job()

    assert result["status"] == "success"
    assert result["final_holdout_touched"] is False
    assert len(result["results"]) == 1

    row = result["results"][0]

    assert row["status"] == "success"
    assert row["competition_id"] == 39
    assert row["season"] == 2026
    assert row["season_label"] == "2026-27"
    assert row["quality_gate"] == "PASS"

    assert calls == {
        "seed": 1,
        "store": 1,
        "reconcile": 1,
    }


async def test_live_market_job_reconciles_before_prediction_refresh(monkeypatch):
    events = []

    monkeypatch.setattr(scheduler, "settings", _Settings())
    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: _SessionContext(),
    )

    async def fake_reconciliation():
        events.append("reconciliation")
        return {
            "status": "success",
            "results": [],
            "final_holdout_touched": False,
        }

    async def fake_market_refresh(
        session,
        settings,
        *,
        competition_id,
        season,
    ):
        events.append("market")
        assert competition_id == 39
        assert season == 2026

        return {
            "status": "success",
            "final_holdout_touched": False,
        }

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_market_refresh,
    )

    await scheduler.live_market_job()

    assert events == [
        "reconciliation",
        "market",
    ]



async def test_live_market_job_runs_result_verifier_after_successful_lifecycle(
    monkeypatch,
):
    events = []

    monkeypatch.setattr(scheduler, "settings", _Settings())
    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: _SessionContext(),
    )

    async def fake_reconciliation():
        events.append("reconciliation")
        return {
            "status": "success",
            "results": [],
            "final_holdout_touched": False,
        }

    async def fake_market_refresh(
        session,
        settings,
        *,
        competition_id,
        season,
    ):
        events.append("market")
        return {
            "status": "success",
            "final_holdout_touched": False,
        }

    async def fake_lifecycle(
        session,
        settings,
        *,
        competition_id,
        season,
        market_result,
        provider,
    ):
        events.append("lifecycle")
        return {
            "status": "success",
            "result_refresh": {
                "status": "success",
            },
            "final_holdout_touched": False,
        }

    def fake_verifier(
        session,
        *,
        competition_id,
        season,
        **kwargs,
    ):
        events.append("verifier")
        assert competition_id == 39
        assert season == 2026
        return {
            "status": "success",
            "quality_gate": "PASS",
            "final_holdout_touched": False,
        }

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_market_refresh,
    )
    monkeypatch.setattr(
        scheduler,
        "run_fixture_lifecycle_post_refresh",
        fake_lifecycle,
    )
    monkeypatch.setattr(
        scheduler,
        "reconcile_stored_provider_pair",
        fake_verifier,
    )

    await scheduler.live_market_job()

    assert events.index("reconciliation") < events.index("market")
    assert events.index("market") < events.index("lifecycle")
    assert events.index("lifecycle") < events.index("verifier")


async def test_live_market_job_skips_result_verifier_when_refresh_not_successful(
    monkeypatch,
):
    verifier_called = False

    monkeypatch.setattr(scheduler, "settings", _Settings())
    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: _SessionContext(),
    )

    async def fake_reconciliation():
        return {
            "status": "success",
            "results": [],
            "final_holdout_touched": False,
        }

    async def fake_market_refresh(
        session,
        settings,
        *,
        competition_id,
        season,
    ):
        return {
            "status": "success",
            "final_holdout_touched": False,
        }

    async def fake_lifecycle(
        session,
        settings,
        *,
        competition_id,
        season,
        market_result,
        provider,
    ):
        return {
            "status": "partial",
            "result_refresh": {
                "status": "config_required",
            },
            "final_holdout_touched": False,
        }

    def fake_verifier(*args, **kwargs):
        nonlocal verifier_called
        verifier_called = True

        return {
            "status": "success",
            "final_holdout_touched": False,
        }

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_market_refresh,
    )
    monkeypatch.setattr(
        scheduler,
        "run_fixture_lifecycle_post_refresh",
        fake_lifecycle,
    )
    monkeypatch.setattr(
        scheduler,
        "reconcile_stored_provider_pair",
        fake_verifier,
    )

    await scheduler.live_market_job()

    assert verifier_called is False
