from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSource, SourcePolicy
from app.services.source_registry import get_source

ALLOWED_REVIEW_STATUSES = {
    "UNREVIEWED",
    "APPROVED",
    "BLOCKED",
    "WRITTEN_PERMISSION_REQUIRED",
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def upsert_source_policy(
    session: Session,
    *,
    source_slug: str,
    review_status: str,
    license_id: str | None = None,
    license_url: str | None = None,
    terms_url: str | None = None,
    collection_allowed: bool = False,
    redistribution_allowed: bool = False,
    commercial_use_allowed: bool = False,
    review_note: str | None = None,
) -> dict[str, Any]:
    status = review_status.strip().upper()
    if status not in ALLOWED_REVIEW_STATUSES:
        raise ValueError(
            "review_status must be UNREVIEWED, APPROVED, BLOCKED, or WRITTEN_PERMISSION_REQUIRED"
        )

    source = get_source(session, source_slug)
    row = session.scalar(select(SourcePolicy).where(SourcePolicy.source_id == source.id))
    created = row is None
    if row is None:
        row = SourcePolicy(source_id=source.id)
        session.add(row)

    row.review_status = status
    row.license_id = license_id
    row.license_url = license_url
    row.terms_url = terms_url
    row.collection_allowed = bool(collection_allowed)
    row.redistribution_allowed = bool(redistribution_allowed)
    row.commercial_use_allowed = bool(commercial_use_allowed)
    row.reviewed_at = _utcnow()
    row.review_note = review_note
    session.commit()

    return {
        "status": "success",
        "created": created,
        "source": source.slug,
        "review_status": row.review_status,
        "license_id": row.license_id,
        "collection_allowed": row.collection_allowed,
        "redistribution_allowed": row.redistribution_allowed,
        "commercial_use_allowed": row.commercial_use_allowed,
    }


def get_source_policy(session: Session, source_slug: str) -> dict[str, Any]:
    source = get_source(session, source_slug)
    row = session.scalar(select(SourcePolicy).where(SourcePolicy.source_id == source.id))
    if row is None:
        return {
            "source": source.slug,
            "review_status": "UNREVIEWED",
            "license_id": None,
            "license_url": None,
            "terms_url": None,
            "collection_allowed": False,
            "redistribution_allowed": False,
            "commercial_use_allowed": False,
            "reviewed_at": None,
            "review_note": None,
        }
    return {
        "source": source.slug,
        "review_status": row.review_status,
        "license_id": row.license_id,
        "license_url": row.license_url,
        "terms_url": row.terms_url,
        "collection_allowed": row.collection_allowed,
        "redistribution_allowed": row.redistribution_allowed,
        "commercial_use_allowed": row.commercial_use_allowed,
        "reviewed_at": row.reviewed_at.isoformat() if row.reviewed_at else None,
        "review_note": row.review_note,
    }


def list_source_policies(session: Session) -> list[dict[str, Any]]:
    rows = list(session.scalars(select(DataSource).order_by(DataSource.priority, DataSource.slug)).all())
    return [get_source_policy(session, row.slug) for row in rows]


def require_collection_approved(session: Session, source_slug: str) -> dict[str, Any]:
    policy = get_source_policy(session, source_slug)
    if policy["review_status"] != "APPROVED" or not policy["collection_allowed"]:
        raise PermissionError(
            f"Source policy does not approve automated collection: {source_slug} "
            f"({policy['review_status']})"
        )
    return policy


def seed_known_source_policies(session: Session) -> dict[str, Any]:
    """Seed only sources whose reuse terms are explicitly known in this project."""
    from app.services.source_registry import seed_default_sources

    seed_default_sources(session)

    result = upsert_source_policy(
        session,
        source_slug="openfootball-json",
        review_status="APPROVED",
        license_id="CC0-1.0",
        license_url="https://github.com/openfootball/football.json/blob/master/LICENSE.md",
        terms_url="https://github.com/openfootball/football.json",
        collection_allowed=True,
        redistribution_allowed=True,
        commercial_use_allowed=True,
        review_note=(
            "OpenFootball states that its football.json schema, data and scripts are "
            "dedicated to the public domain and may be used without restriction."
        ),
    )
    return {"status": "success", "policies_seeded": 1, "items": [result]}
