from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.config import Settings
from app.services import source_health as service
from app.services.source_registry import get_source, mark_source_success, seed_default_sources, source_health_summary


@pytest.mark.asyncio
async def test_missing_optional_api_football_key_is_config_required_not_failed(session, monkeypatch):
    seed_default_sources(session)
    settings = Settings(af_api_key="", ls_api_key="", ls_api_secret="")
    result = await service.run_source_health_checks(session, settings, only_slug="api-football")
    assert result["status"] == "success"
    assert result["checks"][0]["status"] == "CONFIG_REQUIRED"
    row = get_source(session, "api-football")
    assert row.health_status == "CONFIG_REQUIRED"
    assert row.consecutive_failures == 0


def test_health_summary_marks_old_success_stale(session):
    seed_default_sources(session)
    source = get_source(session, "football-data-csv")
    now = datetime.now(timezone.utc)
    mark_source_success(session, source, checked_at=now - timedelta(hours=4))
    session.commit()
    summary = source_health_summary(session, stale_after_minutes=180, now=now)
    item = next(x for x in summary["items"] if x["slug"] == "football-data-csv")
    assert item["health_status"] == "OK"
    assert item["effective_health_status"] == "STALE"
