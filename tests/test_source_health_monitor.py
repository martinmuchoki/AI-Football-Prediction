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


@pytest.mark.asyncio
async def test_retired_livescore_persists_retired_metadata(
    session,
    monkeypatch,
):
    from datetime import datetime, timezone

    seed_default_sources(
        session
    )

    row = get_source(
        session,
        "live-score-api",
    )

    historical_success = datetime(
        2026,
        9,
        19,
        6,
        17,
        53,
        tzinfo=timezone.utc,
    )

    row.health_status = "OK"
    row.last_success_at = historical_success
    row.consecutive_failures = 2
    row.error_message = "historical diagnostic"

    session.commit()

    async def forbidden_poll(*args, **kwargs):
        raise AssertionError(
            "Retired LiveScore must not be polled."
        )

    monkeypatch.setattr(
        service,
        "_check_live_score",
        forbidden_poll,
    )

    settings = Settings(
        ls_api_key="configured",
        ls_api_secret="configured",
    )

    result = await service.run_source_health_checks(
        session,
        settings,
        only_slug="live-score-api",
        retired_sources={
            "live-score-api",
        },
    )

    session.refresh(
        row
    )

    assert (
        result["checks"][0]["status"]
        == "RETIRED"
    )

    assert (
        result["checks"][0]["detail"]
        == "retired_from_runtime"
    )

    assert row.health_status == "RETIRED"
    assert row.last_checked_at is not None

    def _retired_normalize_datetime(value):
        if value is None:
            return None
        if value.tzinfo is None:
            return value
        return (
            value
            .astimezone(timezone.utc)
            .replace(tzinfo=None)
        )

    assert (
        _retired_normalize_datetime(
            row.last_success_at
        if (
            row.last_success_at is None
            or row.last_success_at.tzinfo is None
        )
        else row.last_success_at.astimezone(
            __import__("datetime").timezone.utc
        ).replace(
            tzinfo=None
        )
        )
        ==
        _retired_normalize_datetime(
            historical_success
        if (
            historical_success is None
            or historical_success.tzinfo is None
        )
        else historical_success.astimezone(
            __import__("datetime").timezone.utc
        ).replace(
            tzinfo=None
        )
        )
    )

    assert row.consecutive_failures == 0
    assert row.error_message is None

    assert (
        result["summary"]["health_counts"]
        == {
            "RETIRED": 1,
            "STALE": 0,
        }
        or result["summary"]["health_counts"].get(
            "RETIRED"
        )
        == 1
    )
