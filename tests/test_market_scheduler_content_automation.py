from __future__ import annotations

import pytest

import app.market_scheduler as scheduler


class DummySession:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


def _disable_observability(monkeypatch):
    monkeypatch.setattr(
        scheduler,
        "recover_incident_state_from_history",
        lambda *args, **kwargs: {"status": "ok", "processed": 0},
    )

    monkeypatch.setattr(
        scheduler,
        "market_safety_status",
        lambda *args, **kwargs: {
            "overall_status": "PASS",
        },
    )

    monkeypatch.setattr(
        scheduler,
        "record_market_safety_event",
        lambda *args, **kwargs: {
            "overall_status": "PASS",
            "transition": False,
            "alert_type": None,
        },
    )

    monkeypatch.setattr(
        scheduler,
        "process_market_safety_incident",
        lambda *args, **kwargs: {
            "action": "NONE",
            "incident": {},
        },
    )


@pytest.mark.asyncio
async def test_scheduler_runs_content_after_successful_stage7(monkeypatch):
    calls = []

    async def fake_reconciliation():
        return {"status": "PASS"}

    async def fake_refresh(*args, **kwargs):
        calls.append("refresh")
        return {"status": "success"}

    async def fake_stage7(*args, **kwargs):
        calls.append("stage7")
        return {
            "stage": 7,
            "status": "success",
            "content_handoff_ready": 1,
        }

    def fake_content(*args, **kwargs):
        calls.append("content")
        return {
            "status": "success",
            "ready_count": 1,
            "generated_count": 1,
            "reused_count": 0,
            "draft_queue_count": 1,
            "failed_count": 0,
            "safety": {
                "approval_performed": False,
                "queue_processing_performed": False,
                "social_auto_posting_performed": False,
                "network_posting_performed": False,
            },
        }

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_refresh,
    )
    monkeypatch.setattr(
        scheduler,
        "run_fixture_lifecycle_post_refresh",
        fake_stage7,
    )
    monkeypatch.setattr(
        scheduler,
        "automate_ready_fixture_content",
        fake_content,
    )
    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: DummySession(),
    )
    monkeypatch.setattr(
        type(scheduler.settings),
        "sportsq_competitions",
        lambda self: [(39, 2026)],
    )

    _disable_observability(monkeypatch)

    await scheduler.live_market_job()

    assert calls == ["refresh", "stage7", "content"]


@pytest.mark.asyncio
async def test_scheduler_skips_content_when_stage7_fails(monkeypatch):
    calls = []

    async def fake_reconciliation():
        return {"status": "PASS"}

    async def fake_refresh(*args, **kwargs):
        calls.append("refresh")
        return {"status": "success"}

    async def fake_stage7(*args, **kwargs):
        calls.append("stage7")
        raise RuntimeError("stage7 failed")

    def forbidden_content(*args, **kwargs):
        calls.append("content")
        raise AssertionError(
            "content automation must not run after Stage 7 failure"
        )

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_refresh,
    )
    monkeypatch.setattr(
        scheduler,
        "run_fixture_lifecycle_post_refresh",
        fake_stage7,
    )
    monkeypatch.setattr(
        scheduler,
        "automate_ready_fixture_content",
        forbidden_content,
    )
    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: DummySession(),
    )
    monkeypatch.setattr(
        type(scheduler.settings),
        "sportsq_competitions",
        lambda self: [(39, 2026)],
    )

    _disable_observability(monkeypatch)

    await scheduler.live_market_job()

    assert calls == ["refresh", "stage7"]


@pytest.mark.asyncio
async def test_scheduler_isolates_content_failure(monkeypatch):
    calls = []

    async def fake_reconciliation():
        return {"status": "PASS"}

    async def fake_refresh(*args, **kwargs):
        calls.append("refresh")
        return {"status": "success"}

    async def fake_stage7(*args, **kwargs):
        calls.append("stage7")
        return {
            "stage": 7,
            "status": "success",
            "content_handoff_ready": 1,
        }

    def failing_content(*args, **kwargs):
        calls.append("content")
        raise RuntimeError("content failed")

    safety_calls = []

    def fake_safety(*args, **kwargs):
        safety_calls.append("safety")
        return {"overall_status": "PASS"}

    monkeypatch.setattr(
        scheduler,
        "openfootball_reconciliation_job",
        fake_reconciliation,
    )
    monkeypatch.setattr(
        scheduler,
        "refresh_live_market",
        fake_refresh,
    )
    monkeypatch.setattr(
        scheduler,
        "run_fixture_lifecycle_post_refresh",
        fake_stage7,
    )
    monkeypatch.setattr(
        scheduler,
        "automate_ready_fixture_content",
        failing_content,
    )
    monkeypatch.setattr(
        scheduler,
        "SessionLocal",
        lambda: DummySession(),
    )
    monkeypatch.setattr(
        type(scheduler.settings),
        "sportsq_competitions",
        lambda self: [(39, 2026)],
    )

    _disable_observability(monkeypatch)
    monkeypatch.setattr(
        scheduler,
        "market_safety_status",
        fake_safety,
    )

    await scheduler.live_market_job()

    assert calls == ["refresh", "stage7", "content"]
    assert safety_calls == ["safety"]
