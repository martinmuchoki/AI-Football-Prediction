from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import DataSource, WebCollectionTarget
from app.providers.public_web import PublicWebClient
from app.services.source_registry import get_source
from app.services.web_collector import collect_public_url


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_host_or_subdomain(url: str, base_url: str | None) -> bool:
    if not base_url:
        return True
    host = (urlsplit(url).hostname or "").lower().strip(".")
    base_host = (urlsplit(base_url).hostname or "").lower().strip(".")
    if not host or not base_host:
        return False
    return host == base_host or host.endswith("." + base_host) or base_host.endswith("." + host)


def add_web_target(
    session: Session,
    *,
    source_slug: str,
    url: str,
    interval_minutes: int = 60,
    enabled: bool = True,
) -> dict[str, Any]:
    source = get_source(session, source_slug)
    if source.source_type not in {"web", "official-web"}:
        raise ValueError("web collection targets require a web/official-web source")
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("target URL must be a valid public http/https URL")
    if parts.username or parts.password:
        raise ValueError("target URL must not contain credentials")
    if not _same_host_or_subdomain(url, source.base_url):
        raise ValueError("target URL host must match the registered source base URL")

    target = session.scalar(
        select(WebCollectionTarget).where(
            WebCollectionTarget.source_id == source.id,
            WebCollectionTarget.url == url,
        )
    )
    created = target is None
    if target is None:
        target = WebCollectionTarget(source_id=source.id, url=url)
        session.add(target)
    target.interval_minutes = max(15, int(interval_minutes))
    target.enabled = bool(enabled)
    session.commit()
    return {
        "status": "success",
        "created": created,
        "source": source.slug,
        "url": target.url,
        "enabled": target.enabled,
        "interval_minutes": target.interval_minutes,
    }


def list_web_targets(session: Session) -> list[dict[str, Any]]:
    stmt = (
        select(WebCollectionTarget, DataSource)
        .join(DataSource, WebCollectionTarget.source_id == DataSource.id)
        .order_by(DataSource.priority, DataSource.slug, WebCollectionTarget.id)
    )
    rows = []
    for target, source in session.execute(stmt).all():
        rows.append(
            {
                "id": target.id,
                "source": source.slug,
                "source_type": source.source_type,
                "url": target.url,
                "enabled": target.enabled,
                "interval_minutes": target.interval_minutes,
                "last_collected_at": target.last_collected_at.isoformat() if target.last_collected_at else None,
                "last_status": target.last_status,
                "last_error": target.last_error,
            }
        )
    return rows


def _domain_is_allowlisted(url: str, settings: Settings) -> bool:
    host = (urlsplit(url).hostname or "").lower().strip(".")
    return any(host == d or host.endswith("." + d) for d in settings.public_web_allowed_domain_list())


async def collect_due_web_targets(
    session: Session,
    settings: Settings,
    *,
    force: bool = False,
    only_source: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    now_utc = _utc(now)
    stmt = (
        select(WebCollectionTarget, DataSource)
        .join(DataSource, WebCollectionTarget.source_id == DataSource.id)
        .where(WebCollectionTarget.enabled.is_(True), DataSource.enabled.is_(True))
        .order_by(DataSource.priority, WebCollectionTarget.id)
    )
    if only_source:
        stmt = stmt.where(DataSource.slug == only_source)
    selected = list(session.execute(stmt).all())

    result: dict[str, Any] = {
        "status": "success",
        "targets": len(selected),
        "collected": 0,
        "unchanged": 0,
        "not_due": 0,
        "config_required": 0,
        "failed": 0,
        "items": [],
    }

    if not settings.public_web_allowed_domain_list():
        result["config_required"] = len(selected)
        if selected:
            result["status"] = "config_required"
        for target, source in selected:
            target.last_status = "CONFIG_REQUIRED"
            target.last_error = "PUBLIC_WEB_ALLOWED_DOMAINS is not configured"
            result["items"].append({"source": source.slug, "url": target.url, "status": "CONFIG_REQUIRED"})
        session.commit()
        return result

    async with PublicWebClient(settings) as client:
        for target, source in selected:
            last = _utc(target.last_collected_at) if target.last_collected_at else None
            due = force or last is None or now_utc - last >= timedelta(minutes=max(15, target.interval_minutes))
            if not due:
                result["not_due"] += 1
                continue
            if not _domain_is_allowlisted(target.url, settings):
                target.last_status = "CONFIG_REQUIRED"
                target.last_error = "Target domain is not in PUBLIC_WEB_ALLOWED_DOMAINS"
                result["config_required"] += 1
                result["items"].append({"source": source.slug, "url": target.url, "status": "CONFIG_REQUIRED"})
                continue
            try:
                collected = await collect_public_url(
                    session,
                    client,
                    source_slug=source.slug,
                    url=target.url,
                )
                target.last_collected_at = now_utc
                target.last_status = "OK"
                target.last_error = None
                if collected["snapshot_inserted"]:
                    result["collected"] += 1
                else:
                    result["unchanged"] += 1
                result["items"].append(
                    {
                        "source": source.slug,
                        "url": target.url,
                        "status": "OK",
                        "snapshot_inserted": bool(collected["snapshot_inserted"]),
                        "content_hash": collected["content_hash"],
                    }
                )
            except Exception as exc:
                target.last_status = "FAILED"
                target.last_error = type(exc).__name__
                result["failed"] += 1
                result["items"].append({"source": source.slug, "url": target.url, "status": "FAILED", "detail": type(exc).__name__})
            session.commit()

    if result["failed"] or result["config_required"]:
        result["status"] = "partial" if result["collected"] or result["unchanged"] else "attention_required"
    return result
