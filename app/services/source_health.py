from __future__ import annotations

from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlsplit

import httpx
from sqlalchemy.orm import Session

from app.config import Settings
from app.providers.api_football import ApiFootballClient
from app.providers.live_score_api import LiveScoreApiClient
from app.providers.public_web import PublicWebClient
from app.providers.openfootball_json import OpenFootballJsonClient
from app.services.source_registry import (
    get_source,
    list_sources,
    mark_source_config_required,
    mark_source_failure,
    mark_source_transient_failure,
    mark_source_success,
    seed_default_sources,
    source_health_summary,
)


def _is_transient_error(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        code = int(exc.response.status_code)
        return code == 429 or 500 <= code <= 599
    return isinstance(exc, httpx.RequestError)


def _safe_error(exc: Exception) -> str:
    """Return a credential-safe diagnostic suitable for DB/log output."""
    if isinstance(exc, httpx.HTTPStatusError):
        return f"HTTP {exc.response.status_code}"
    if isinstance(exc, httpx.RequestError):
        return type(exc).__name__
    text = str(exc)
    # Never retain request URLs/query strings in health messages.
    if "http://" in text.lower() or "https://" in text.lower() or "key=" in text.lower() or "secret=" in text.lower():
        return type(exc).__name__
    return text[:500] or type(exc).__name__


async def _check_live_score(settings: Settings) -> None:
    async with LiveScoreApiClient(settings) as client:
        await client.verify()


async def _check_api_football(settings: Settings) -> None:
    async with ApiFootballClient(settings) as client:
        await client.get_league(league=39, season=2026)


async def _check_openfootball(settings: Settings) -> None:
    async with OpenFootballJsonClient(timeout_seconds=settings.public_web_timeout_seconds) as client:
        await client.fetch_league(season_label="2026-27", league_file="en.1.json")


async def _check_plain_http(url: str, *, timeout: float) -> None:
    async with httpx.AsyncClient(
        timeout=max(1.0, float(timeout)),
        follow_redirects=True,
        headers={"User-Agent": "MDRN-SportsQ-Health/0.10.2"},
    ) as client:
        response = await client.get(url)
        response.raise_for_status()


async def run_source_health_checks(
    session: Session,
    settings: Settings,
    *,
    only_slug: str | None = None,
    retired_sources: set[str] | None = None,
) -> dict[str, Any]:
    """Check registered providers without exposing credentials in diagnostics."""
    seed_default_sources(session)
    selected = [row for row in list_sources(session) if row["enabled"]]
    if only_slug:
        selected = [row for row in selected if row["slug"] == only_slug]
        if not selected:
            raise ValueError(f"Unknown or disabled source: {only_slug}")

    checked_at = datetime.now(timezone.utc)
    results: list[dict[str, Any]] = []

    for item in selected:
        slug = item["slug"]
        source = get_source(session, slug)
        status = "UNKNOWN"
        detail = None
        try:
            if (
                slug == "live-score-api"
                and retired_sources
                and slug in retired_sources
            ):
                status = "RETIRED"
                detail = "retired_from_runtime"

            elif slug == "live-score-api":
                if not settings.ls_api_key.strip() or not settings.ls_api_secret.strip():
                    mark_source_config_required(session, source, "Live Score credentials are not configured", checked_at=checked_at)
                    status = "CONFIG_REQUIRED"
                    detail = "credentials_not_configured"
                else:
                    await _check_live_score(settings)
                    mark_source_success(session, source, checked_at=checked_at)
                    status = "OK"
            elif slug == "api-football":
                if not settings.af_api_key.strip():
                    mark_source_config_required(session, source, "API-Football credential is not configured", checked_at=checked_at)
                    status = "CONFIG_REQUIRED"
                    detail = "credential_not_configured"
                else:
                    await _check_api_football(settings)
                    mark_source_success(session, source, checked_at=checked_at)
                    status = "OK"
            elif slug == "openfootball-json":
                await _check_openfootball(settings)
                mark_source_success(session, source, checked_at=checked_at)
                status = "OK"
            elif source.source_type in {"web", "official-web"}:
                if not source.base_url:
                    mark_source_config_required(session, source, "Source base URL is not configured", checked_at=checked_at)
                    status = "CONFIG_REQUIRED"
                    detail = "base_url_not_configured"
                else:
                    host = (urlsplit(source.base_url).hostname or "").lower().strip(".")
                    allowed = settings.public_web_allowed_domain_list()
                    if not any(host == d or host.endswith("." + d) for d in allowed):
                        mark_source_config_required(session, source, "Domain is not in PUBLIC_WEB_ALLOWED_DOMAINS", checked_at=checked_at)
                        status = "CONFIG_REQUIRED"
                        detail = "domain_not_allowlisted"
                    else:
                        async with PublicWebClient(settings) as client:
                            await client.fetch(source.base_url)
                        mark_source_success(session, source, checked_at=checked_at)
                        status = "OK"
            else:
                if not source.base_url:
                    mark_source_config_required(session, source, "Source base URL is not configured", checked_at=checked_at)
                    status = "CONFIG_REQUIRED"
                    detail = "base_url_not_configured"
                else:
                    await _check_plain_http(source.base_url, timeout=settings.public_web_timeout_seconds)
                    mark_source_success(session, source, checked_at=checked_at)
                    status = "OK"
        except Exception as exc:
            detail = _safe_error(exc)
            if _is_transient_error(exc):
                status = mark_source_transient_failure(
                    session,
                    source,
                    detail,
                    checked_at=checked_at,
                    failure_threshold=settings.source_health_failure_threshold,
                )
            else:
                mark_source_failure(session, source, detail, checked_at=checked_at)
                status = "FAILED"

        session.commit()
        results.append({"source": slug, "status": status, "detail": detail})

    summary = source_health_summary(
        session,
        stale_after_minutes=settings.source_health_stale_after_minutes,
        now=checked_at,
    )
    return {
        "status": "success" if not any(r["status"] in {"DEGRADED", "FAILED"} for r in results) else "partial",
        "checked_at": checked_at.isoformat(),
        "checks": results,
        "summary": summary,
    }
