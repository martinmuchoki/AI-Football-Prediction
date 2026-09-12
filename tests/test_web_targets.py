import pytest

from app.config import Settings
from app.services.source_registry import upsert_source
from app.services.web_targets import add_web_target, collect_due_web_targets, list_web_targets


def test_add_and_list_web_target(session):
    upsert_source(
        session,
        slug="official-example",
        name="Official Example",
        source_type="official-web",
        priority=40,
        base_url="https://example.com/",
        parser_version="example-v1",
    )
    created = add_web_target(
        session,
        source_slug="official-example",
        url="https://example.com/fixtures",
        interval_minutes=30,
    )
    assert created["created"] is True
    rows = list_web_targets(session)
    assert len(rows) == 1
    assert rows[0]["source"] == "official-example"
    assert rows[0]["interval_minutes"] == 30


@pytest.mark.asyncio
async def test_collection_stays_config_required_without_allowlist(session):
    upsert_source(
        session,
        slug="official-example",
        name="Official Example",
        source_type="official-web",
        priority=40,
        base_url="https://example.com/",
        parser_version="example-v1",
    )
    add_web_target(
        session,
        source_slug="official-example",
        url="https://example.com/fixtures",
    )
    settings = Settings(public_web_allowed_domains="")
    result = await collect_due_web_targets(session, settings)
    assert result["status"] == "config_required"
    assert result["config_required"] == 1
    assert result["failed"] == 0
