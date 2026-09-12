from __future__ import annotations

import hashlib
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path

from app import __version__
from app.main import app
from app.services.sportsq_fixture_lifecycle import (
    _classify_fixture_state,
    history_signature_status,
)

ROOT = Path(__file__).resolve().parents[1]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def test_stage7_version():
    assert __version__ == "1.0.5-stage7-fixture-lifecycle"


def test_stage7_history_adapter_signature_is_supported():
    status = history_signature_status()
    assert status["supported_by_stage7_adapter"] is True
    assert status["unknown_required_parameters"] == []


def test_fixture_lifecycle_state_machine():
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    future = now + timedelta(hours=5)
    past = now - timedelta(hours=5)

    cases = [
        (False, future, False, False, False, "PREMATCH_PENDING_LOCK"),
        (False, future, True, False, False, "PREMATCH_LOCKED"),
        (False, future, True, False, True, "PREMATCH_LOCKED_SCORECALL"),
        (False, past, True, False, True, "STARTED_UNFINISHED"),
        (True, past, True, False, True, "COMPLETED_PENDING_GRADE"),
        (True, past, True, True, True, "COMPLETED_GRADED"),
    ]
    for finished, kickoff, pred, graded, score, expected in cases:
        assert _classify_fixture_state(
            is_finished=finished,
            kickoff_utc=kickoff,
            now=now,
            has_prediction=pred,
            prediction_graded=graded,
            has_scorecall=score,
        ) == expected


def test_stage7_routes_are_read_only():
    route_methods = {
        route.path: set(getattr(route, "methods", set()) or set())
        for route in app.routes
    }
    for path in (
        "/api/v1/sportsq/stage7/status",
        "/api/v1/sportsq/stage7/fixtures",
    ):
        assert path in route_methods
        assert "GET" in route_methods[path]
        assert not ({"POST", "PUT", "PATCH", "DELETE"} & route_methods[path])


def test_live_market_scheduler_keeps_refresh_and_adds_stage7():
    import app.market_scheduler as scheduler
    source = inspect.getsource(scheduler.live_market_job)
    assert "refresh_live_market" in source
    assert "run_fixture_lifecycle_post_refresh" in source
    assert "stage7_lifecycle" in source


def test_stage6_protected_core_services_are_unchanged():
    expected = {
        "app/services/live_prediction_engine.py":
            "7190cb7978d0043005198a7628ab2775b17d68471cce642a64131a9289e4cde2",
        "app/services/prediction_grading.py":
            "593300da77b912a9efa593f139f0652c2d263477ea164317969344ddbaa4f7e9",
        "app/services/social_content.py":
            "2678e8a9c000b077ecf495d727e509387985b774f27888787440c02d174f310c",
        "app/services/sportsq_intelligence.py":
            "3487355e2198bb4b0a7f4eff4591720789cdf8c343c03f145e32da7d8464daef",
        "app/services/sportsq_scorecall.py":
            "9f8af0581272a8aa1fc24630723b5230442a48f02ae38b9c8ce5f5f571ef51ab",
        "app/services/sportsq_news_impact.py":
            "b1b24541fa8c9eb79e1853445d99fbf27adbd71b7500e6682f90496fb09078eb",
    }
    for rel, digest in expected.items():
        assert _sha256(ROOT / rel) == digest
