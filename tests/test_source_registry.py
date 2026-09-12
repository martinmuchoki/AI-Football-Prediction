from app.services.source_registry import (
    record_observation,
    resolve_field,
    seed_default_sources,
    upsert_source,
)


def test_seed_default_sources_is_idempotent(session):
    first = seed_default_sources(session)
    second = seed_default_sources(session)
    assert first["inserted"] == 4
    assert first["total_defaults"] == 4
    assert second["inserted"] == 0
    assert second["total_defaults"] == 4


def test_source_priority_resolves_conflict(session):
    seed_default_sources(session)
    record_observation(
        session,
        source_slug="football-data-csv",
        entity_type="fixture",
        entity_key="123",
        field_name="kickoff_utc",
        value="2026-09-12T14:00:00Z",
    )
    record_observation(
        session,
        source_slug="live-score-api",
        entity_type="fixture",
        entity_key="123",
        field_name="kickoff_utc",
        value="2026-09-12T15:00:00Z",
    )
    session.commit()

    result = resolve_field(
        session,
        entity_type="fixture",
        entity_key="123",
        field_name="kickoff_utc",
    )
    assert result["status"] == "success"
    assert result["conflict"] is True
    assert result["chosen"]["source"] == "live-score-api"
    assert result["chosen"]["priority"] == 10


def test_add_web_source(session):
    result = upsert_source(
        session,
        slug="official-example",
        name="Official Example",
        source_type="official-web",
        priority=5,
        base_url="https://example.com",
        parser_version="example-v1",
    )
    assert result["created"] is True
    assert result["source_type"] == "official-web"
