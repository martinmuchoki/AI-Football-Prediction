from __future__ import annotations

import inspect

import app
import app.market_scheduler as scheduler

import app.services.result_verification as verification
import app.services.source_health as source_health
import app.services.sportsq_result_intelligence as result_intelligence


def _summary():

    return {
        "status": "success",
        "competition_id": 39,
        "season": 2026,
        "primary_source": "api-football",
        "secondary_source": "openfootball-json",
        "total": 380,
        "overall_counts": {
            "MATCH": 380,
        },
        "fixture_agreement_rate": 1.0,
        "quality_gate": "PASS",
        "last_checked_at":
            "2026-09-25T00:00:00+00:00",
        "final_holdout_touched": False,
    }


def test_v108_version():

    assert app.__version__ == "1.0.9"


def test_legacy_verifier_default_preserved():

    sig = inspect.signature(
        verification
        .reconcile_stored_provider_pair
    )

    assert (
        sig.parameters[
            "secondary_source"
        ].default
        == "live-score-api"
    )


def test_explicit_production_mode_exists():

    sig = inspect.signature(
        verification
        .reconcile_stored_provider_pair
    )

    assert (
        "verification_mode"
        in sig.parameters
    )


def test_production_mode_never_reads_livescore(
    monkeypatch,
):

    calls = {
        "summary": 0,
        "live": 0,
    }

    def fake_summary(
        session,
        *,
        competition_id,
        season,
        primary_source,
    ):

        calls["summary"] += 1

        assert competition_id == 39
        assert season == 2026
        assert primary_source == "api-football"

        return _summary()

    def forbidden_live(
        *args,
        **kwargs,
    ):

        calls["live"] += 1

        raise AssertionError(
            "Production mode read LiveScore."
        )

    monkeypatch.setattr(
        verification,
        "reconciliation_summary",
        fake_summary,
    )

    monkeypatch.setattr(
        verification,
        "_live_rows",
        forbidden_live,
    )

    result = (
        verification
        .reconcile_stored_provider_pair(
            object(),
            competition_id=39,
            season=2026,
            verification_mode=(
                "api-football-openfootball-only"
            ),
        )
    )

    assert calls == {
        "summary": 1,
        "live": 0,
    }

    assert (
        result["secondary_source"]
        == "openfootball-json"
    )


def test_composite_production_mode(
    monkeypatch,
):

    def fake_summary(
        session,
        *,
        competition_id,
        season,
        primary_source,
    ):
        return _summary()

    monkeypatch.setattr(
        verification,
        "reconciliation_summary",
        fake_summary,
    )

    result = (
        verification
        .composite_reconciliation_summary(
            object(),
            competition_id=39,
            season=2026,
        )
    )

    assert (
        result["verification_mode"]
        == "api-football-openfootball-only"
    )

    assert (
        result[
            "result_verifier_source"
        ]
        is None
    )


def test_result_intelligence_uses_openfootball():

    source = inspect.getsource(
        result_intelligence
    )

    assert "OPENFOOTBALL_SOURCE" in source
    assert "LIVE_SCORE_SOURCE" not in source


def test_source_health_supports_retirement():

    sig = inspect.signature(
        source_health
        .run_source_health_checks
    )

    assert (
        "retired_sources"
        in sig.parameters
    )


def test_scheduler_requests_production_mode():

    source = inspect.getsource(
        scheduler.live_market_job
    )

    assert (
        "verification_mode"
        in source
    )

    assert (
        "api-football-openfootball-only"
        in source
    )

    assert (
        'provider="api-football"'
        in source
    )
