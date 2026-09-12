import asyncio

import httpx
import pytest

from app.config import Settings
from app.providers.public_web import PublicWebClient, validate_public_url
from app.services.source_registry import get_source, upsert_source
from app.services.source_policy import upsert_source_policy
from app.services.web_collector import collect_public_url


def test_public_url_requires_allowlist():
    with pytest.raises(ValueError, match="disabled"):
        validate_public_url(
            "https://example.com/match",
            allowed_domains=[],
            resolver=lambda host: ["93.184.216.34"],
        )


def test_public_url_blocks_private_resolution():
    with pytest.raises(ValueError, match="non-public"):
        validate_public_url(
            "https://example.com/match",
            allowed_domains=["example.com"],
            resolver=lambda host: ["127.0.0.1"],
        )


def test_public_collector_respects_robots_and_records_snapshot(session):
    upsert_source(
        session,
        slug="official-example",
        name="Official Example",
        source_type="official-web",
        priority=5,
        base_url="https://example.com",
        parser_version="example-v1",
    )
    upsert_source_policy(
        session,
        source_slug="official-example",
        review_status="APPROVED",
        license_id="TEST",
        collection_allowed=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nAllow: /\n")
        if request.url.path == "/match":
            return httpx.Response(
                200,
                text="<html><body>Fixture data</body></html>",
                headers={"content-type": "text/html; charset=utf-8"},
            )
        return httpx.Response(404)

    settings = Settings(
        public_web_allowed_domains="example.com",
        public_web_robots_fail_closed=True,
    )
    transport = httpx.MockTransport(handler)

    async def run():
        async with PublicWebClient(
            settings,
            transport=transport,
            resolver=lambda host: ["93.184.216.34"],
        ) as client:
            return await collect_public_url(
                session,
                client,
                source_slug="official-example",
                url="https://example.com/match",
            )

    result = asyncio.run(run())
    assert result["status"] == "success"
    assert result["robots_allowed"] is True
    assert result["snapshot_inserted"] is True

    source = get_source(session, "official-example")
    assert source.health_status == "OK"
    assert source.consecutive_failures == 0


def test_public_collector_rejects_robots_disallow(session):
    upsert_source(
        session,
        slug="official-example",
        name="Official Example",
        source_type="official-web",
        priority=5,
        base_url="https://example.com",
    )
    upsert_source_policy(
        session,
        source_slug="official-example",
        review_status="APPROVED",
        license_id="TEST",
        collection_allowed=True,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /\n")
        return httpx.Response(200, text="should not be fetched")

    settings = Settings(public_web_allowed_domains="example.com")

    async def run():
        async with PublicWebClient(
            settings,
            transport=httpx.MockTransport(handler),
            resolver=lambda host: ["93.184.216.34"],
        ) as client:
            await collect_public_url(
                session,
                client,
                source_slug="official-example",
                url="https://example.com/match",
            )

    with pytest.raises(PermissionError, match="robots.txt"):
        asyncio.run(run())

    source = get_source(session, "official-example")
    assert source.health_status == "FAILED"
    assert source.consecutive_failures == 1
