from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import SourceSnapshot
from app.providers.public_web import PublicWebClient
from app.services.source_registry import get_source, mark_source_failure, mark_source_success
from app.services.source_policy import require_collection_approved


async def collect_public_url(
    session: Session,
    client: PublicWebClient,
    *,
    source_slug: str,
    url: str,
) -> dict[str, Any]:
    source = get_source(session, source_slug)
    if not source.enabled:
        raise ValueError(f"Source is disabled: {source_slug}")
    if source.source_type not in {"web", "official-web"}:
        raise ValueError(
            f"Source {source_slug} is type {source.source_type}; public URL collection requires web/official-web"
        )
    require_collection_approved(session, source_slug)

    try:
        page = await client.fetch(url)
        existing = session.scalar(
            select(SourceSnapshot).where(
                SourceSnapshot.source_id == source.id,
                SourceSnapshot.canonical_url == page.canonical_url,
                SourceSnapshot.content_hash == page.content_hash,
            )
        )

        inserted = False
        if existing is None:
            existing = SourceSnapshot(
                source_id=source.id,
                source_url=page.source_url,
                canonical_url=page.canonical_url,
                status_code=page.status_code,
                content_type=page.content_type,
                content_hash=page.content_hash,
                body_text=page.body_text,
                body_bytes=page.body_bytes,
                robots_allowed=page.robots_allowed,
                parser_version=source.parser_version,
                collected_at=page.collected_at,
            )
            session.add(existing)
            inserted = True

        mark_source_success(session, source, checked_at=page.collected_at)
        session.commit()

        return {
            "status": "success",
            "source": source.slug,
            "url": page.canonical_url,
            "status_code": page.status_code,
            "content_type": page.content_type,
            "body_bytes": page.body_bytes,
            "content_hash": page.content_hash,
            "snapshot_inserted": inserted,
            "robots_allowed": page.robots_allowed,
            "collected_at": page.collected_at.isoformat(),
        }
    except Exception as exc:
        mark_source_failure(session, source, str(exc))
        session.commit()
        raise
