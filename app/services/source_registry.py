from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import DataSource, SourceObservation


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


DEFAULT_SOURCES = (
    {
        "slug": "live-score-api",
        "name": "Live Score API",
        "source_type": "api",
        "priority": 10,
        "base_url": "https://livescore-api.com/api-client",
        "parser_version": "live-score-v1",
    },
    {
        "slug": "football-data-csv",
        "name": "Football-Data.co.uk CSV",
        "source_type": "csv",
        "priority": 20,
        "base_url": "https://www.football-data.co.uk/",
        "parser_version": "football-data-v1",
    },
    {
        "slug": "api-football",
        "name": "API-Football",
        "source_type": "api",
        "priority": 30,
        "base_url": "https://v3.football.api-sports.io",
        "parser_version": "api-football-v1",
    },
    {
        "slug": "openfootball-json",
        "name": "OpenFootball football.json (CC0)",
        "source_type": "api",
        "priority": 40,
        "base_url": "https://api.github.com/repos/openfootball/football.json/contents/2026-27/en.1.json?ref=master",
        "parser_version": "openfootball-json-v1",
    },
)


def seed_default_sources(session: Session) -> dict[str, Any]:
    inserted = 0
    updated = 0

    for item in DEFAULT_SOURCES:
        row = session.scalar(select(DataSource).where(DataSource.slug == item["slug"]))
        if row is None:
            row = DataSource(
                slug=item["slug"],
                name=item["name"],
                source_type=item["source_type"],
                priority=item["priority"],
                enabled=True,
                base_url=item["base_url"],
                parser_version=item["parser_version"],
                health_status="UNKNOWN",
            )
            session.add(row)
            inserted += 1
        else:
            changed = False
            for key in ("name", "source_type", "priority", "base_url", "parser_version"):
                if getattr(row, key) != item[key]:
                    setattr(row, key, item[key])
                    changed = True
            if changed:
                updated += 1

    session.commit()
    return {
        "status": "success",
        "inserted": inserted,
        "updated": updated,
        "total_defaults": len(DEFAULT_SOURCES),
    }


def list_sources(session: Session) -> list[dict[str, Any]]:
    rows = list(session.scalars(select(DataSource).order_by(DataSource.priority, DataSource.slug)).all())
    return [
        {
            "slug": row.slug,
            "name": row.name,
            "source_type": row.source_type,
            "priority": row.priority,
            "enabled": row.enabled,
            "base_url": row.base_url,
            "parser_version": row.parser_version,
            "health_status": row.health_status,
            "last_checked_at": row.last_checked_at.isoformat() if row.last_checked_at else None,
            "last_success_at": row.last_success_at.isoformat() if row.last_success_at else None,
            "last_error_at": row.last_error_at.isoformat() if row.last_error_at else None,
            "consecutive_failures": row.consecutive_failures,
            "error_message": row.error_message,
        }
        for row in rows
    ]


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def source_health_summary(
    session: Session,
    *,
    stale_after_minutes: int | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    rows = list_sources(session)
    now_utc = _as_utc(now) or utcnow()
    stale_after = None
    if stale_after_minutes is not None:
        stale_after = timedelta(minutes=max(1, int(stale_after_minutes)))

    counts: dict[str, int] = {}
    for row in rows:
        effective = row["health_status"]
        if stale_after and effective == "OK" and row.get("last_success_at"):
            try:
                last_success = _as_utc(datetime.fromisoformat(row["last_success_at"]))
                if last_success and now_utc - last_success > stale_after:
                    effective = "STALE"
            except (TypeError, ValueError):
                pass
        row["effective_health_status"] = effective
        counts[effective] = counts.get(effective, 0) + 1
    return {
        "status": "success",
        "sources": len(rows),
        "health_counts": counts,
        "stale_after_minutes": stale_after_minutes,
        "items": rows,
    }


def get_source(session: Session, slug: str) -> DataSource:
    row = session.scalar(select(DataSource).where(DataSource.slug == slug))
    if row is None:
        raise ValueError(f"Unknown source: {slug}")
    return row


def mark_source_success(session: Session, source: DataSource, *, checked_at: datetime | None = None) -> None:
    when = checked_at or utcnow()
    source.health_status = "OK"
    source.last_checked_at = when
    source.last_success_at = when
    source.consecutive_failures = 0
    source.error_message = None


def mark_source_failure(
    session: Session,
    source: DataSource,
    error: str,
    *,
    checked_at: datetime | None = None,
) -> None:
    when = checked_at or utcnow()
    source.health_status = "FAILED"
    source.last_checked_at = when
    source.last_error_at = when
    source.consecutive_failures = int(source.consecutive_failures or 0) + 1
    source.error_message = str(error)[:2000]



def mark_source_transient_failure(
    session: Session,
    source: DataSource,
    error: str,
    *,
    checked_at: datetime | None = None,
    failure_threshold: int = 3,
) -> str:
    """Record a transient provider problem without declaring hard failure immediately.

    The first N-1 consecutive transient failures are DEGRADED. The Nth and later
    failures are FAILED. Any successful check resets the counter to zero.
    """
    when = checked_at or utcnow()
    threshold = max(1, int(failure_threshold))
    failures = int(source.consecutive_failures or 0) + 1
    source.consecutive_failures = failures
    source.health_status = "FAILED" if failures >= threshold else "DEGRADED"
    source.last_checked_at = when
    source.last_error_at = when
    source.error_message = str(error)[:2000]
    return source.health_status


def mark_source_config_required(
    session: Session,
    source: DataSource,
    reason: str,
    *,
    checked_at: datetime | None = None,
) -> None:
    """Record a non-failure health state when credentials/approval are absent."""
    when = checked_at or utcnow()
    source.health_status = "CONFIG_REQUIRED"
    source.last_checked_at = when
    source.consecutive_failures = 0
    source.error_message = str(reason)[:2000]


def record_observation(
    session: Session,
    *,
    source_slug: str,
    entity_type: str,
    entity_key: str,
    field_name: str,
    value: Any,
    source_url: str | None = None,
    observed_at: datetime | None = None,
    raw_hash: str | None = None,
    parser_version: str | None = None,
) -> SourceObservation:
    source = get_source(session, source_slug)
    row = SourceObservation(
        source_id=source.id,
        entity_type=entity_type,
        entity_key=entity_key,
        field_name=field_name,
        value_json=json.dumps(value, ensure_ascii=False, sort_keys=True),
        source_url=source_url,
        observed_at=observed_at or utcnow(),
        collected_at=utcnow(),
        raw_hash=raw_hash,
        parser_version=parser_version or source.parser_version,
    )
    session.add(row)
    session.flush()
    return row


def resolve_field(
    session: Session,
    *,
    entity_type: str,
    entity_key: str,
    field_name: str,
) -> dict[str, Any]:
    stmt = (
        select(SourceObservation, DataSource)
        .join(DataSource, SourceObservation.source_id == DataSource.id)
        .where(
            SourceObservation.entity_type == entity_type,
            SourceObservation.entity_key == entity_key,
            SourceObservation.field_name == field_name,
            DataSource.enabled.is_(True),
        )
        .order_by(
            DataSource.priority.asc(),
            SourceObservation.observed_at.desc(),
            SourceObservation.id.desc(),
        )
    )
    rows = list(session.execute(stmt).all())
    if not rows:
        return {
            "status": "not_found",
            "entity_type": entity_type,
            "entity_key": entity_key,
            "field_name": field_name,
            "chosen": None,
            "conflict": False,
            "candidates": [],
        }

    candidates = []
    distinct_values = set()
    for obs, source in rows:
        value = json.loads(obs.value_json)
        distinct_values.add(obs.value_json)
        candidates.append(
            {
                "source": source.slug,
                "priority": source.priority,
                "value": value,
                "observed_at": obs.observed_at.isoformat(),
                "source_url": obs.source_url,
                "parser_version": obs.parser_version,
                "raw_hash": obs.raw_hash,
            }
        )

    return {
        "status": "success",
        "entity_type": entity_type,
        "entity_key": entity_key,
        "field_name": field_name,
        "chosen": candidates[0],
        "conflict": len(distinct_values) > 1,
        "candidates": candidates,
    }


def upsert_source(
    session: Session,
    *,
    slug: str,
    name: str,
    source_type: str,
    priority: int,
    base_url: str | None,
    parser_version: str = "v1",
    enabled: bool = True,
) -> dict[str, Any]:
    clean_slug = slug.strip().lower()
    if not clean_slug:
        raise ValueError("source slug is required")
    if source_type not in {"api", "csv", "web", "official-web"}:
        raise ValueError("source_type must be api, csv, web, or official-web")
    if priority < 1:
        raise ValueError("priority must be >= 1")

    row = session.scalar(select(DataSource).where(DataSource.slug == clean_slug))
    created = row is None
    if row is None:
        row = DataSource(slug=clean_slug)
        session.add(row)

    row.name = name.strip() or clean_slug
    row.source_type = source_type
    row.priority = int(priority)
    row.base_url = base_url
    row.parser_version = parser_version.strip() or "v1"
    row.enabled = bool(enabled)
    if created:
        row.health_status = "UNKNOWN"
        row.consecutive_failures = 0
    session.commit()

    return {
        "status": "success",
        "created": created,
        "source": clean_slug,
        "source_type": source_type,
        "priority": int(priority),
        "enabled": bool(enabled),
    }
