from datetime import datetime, timezone

from app.services.source_registry import (
    get_source,
    mark_source_success,
    mark_source_transient_failure,
    seed_default_sources,
)


def test_transient_failures_degrade_before_hard_failure(session):
    seed_default_sources(session)
    source = get_source(session, "football-data-csv")
    now = datetime.now(timezone.utc)
    assert mark_source_transient_failure(session, source, "HTTP 503", checked_at=now, failure_threshold=3) == "DEGRADED"
    assert source.consecutive_failures == 1
    assert mark_source_transient_failure(session, source, "HTTP 503", checked_at=now, failure_threshold=3) == "DEGRADED"
    assert source.consecutive_failures == 2
    assert mark_source_transient_failure(session, source, "HTTP 503", checked_at=now, failure_threshold=3) == "FAILED"
    assert source.consecutive_failures == 3
    mark_source_success(session, source, checked_at=now)
    assert source.health_status == "OK"
    assert source.consecutive_failures == 0
