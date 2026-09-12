import pytest

from app.services.source_policy import (
    get_source_policy,
    require_collection_approved,
    seed_known_source_policies,
    upsert_source_policy,
)
from app.services.source_registry import get_source, seed_default_sources, upsert_source


def test_openfootball_policy_is_seeded_approved_cc0(session):
    result = seed_known_source_policies(session)
    assert result["policies_seeded"] == 1
    policy = get_source_policy(session, "openfootball-json")
    assert policy["review_status"] == "APPROVED"
    assert policy["license_id"] == "CC0-1.0"
    assert policy["collection_allowed"] is True
    assert policy["redistribution_allowed"] is True
    assert policy["commercial_use_allowed"] is True


def test_unreviewed_web_source_is_blocked_by_policy(session):
    upsert_source(
        session,
        slug="unknown-web",
        name="Unknown Web",
        source_type="web",
        priority=80,
        base_url="https://example.com",
    )
    with pytest.raises(PermissionError, match="does not approve"):
        require_collection_approved(session, "unknown-web")

    upsert_source_policy(
        session,
        source_slug="unknown-web",
        review_status="APPROVED",
        collection_allowed=True,
    )
    policy = require_collection_approved(session, "unknown-web")
    assert policy["review_status"] == "APPROVED"
