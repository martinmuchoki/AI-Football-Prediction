from __future__ import annotations

import hashlib
import ipaddress
import socket
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlsplit, urlunsplit
from urllib.robotparser import RobotFileParser

import httpx

from app.config import Settings


@dataclass(frozen=True)
class PublicPage:
    source_url: str
    canonical_url: str
    status_code: int
    content_type: str
    body_text: str | None
    body_bytes: int
    content_hash: str
    robots_allowed: bool
    collected_at: datetime


def _canonical_url(url: str) -> str:
    parts = urlsplit(url)
    scheme = parts.scheme.lower()
    host = (parts.hostname or "").lower()
    port = parts.port
    netloc = host
    if port and not ((scheme == "http" and port == 80) or (scheme == "https" and port == 443)):
        netloc = f"{host}:{port}"
    return urlunsplit((scheme, netloc, parts.path or "/", parts.query, ""))


def _is_public_ip(value: str) -> bool:
    ip = ipaddress.ip_address(value)
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _default_resolver(host: str) -> list[str]:
    out = []
    for item in socket.getaddrinfo(host, None, type=socket.SOCK_STREAM):
        address = item[4][0]
        if address not in out:
            out.append(address)
    return out


def validate_public_url(
    url: str,
    *,
    allowed_domains: list[str],
    resolver: Callable[[str], list[str]] = _default_resolver,
) -> str:
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"}:
        raise ValueError("Only http/https public URLs are allowed")
    if parts.username or parts.password:
        raise ValueError("URLs containing credentials are not allowed")
    host = (parts.hostname or "").lower().strip(".")
    if not host:
        raise ValueError("URL host is missing")

    allowed = [d.lower().strip().strip(".") for d in allowed_domains if d.strip()]
    if not allowed:
        raise ValueError("Public web collection is disabled until PUBLIC_WEB_ALLOWED_DOMAINS is configured")

    if not any(host == domain or host.endswith("." + domain) for domain in allowed):
        raise ValueError(f"Domain is not allowlisted: {host}")

    addresses = resolver(host)
    if not addresses:
        raise ValueError(f"Could not resolve host: {host}")
    if any(not _is_public_ip(address) for address in addresses):
        raise ValueError(f"Host resolves to a non-public address: {host}")

    return _canonical_url(url)


class PublicWebClient:
    """Respectful public-web collector.

    Safety policy:
    - explicit domain allowlist
    - public IPs only (blocks localhost/private-network SSRF)
    - robots.txt checked before collection
    - no login, CAPTCHA, paywall, or anti-bot bypass logic
    - bounded body size
    """

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: Callable[[str], list[str]] = _default_resolver,
    ) -> None:
        self.settings = settings
        self.resolver = resolver
        self.client = httpx.AsyncClient(
            timeout=settings.public_web_timeout_seconds,
            follow_redirects=True,
            headers={"User-Agent": settings.public_web_user_agent},
            transport=transport,
        )

    async def __aenter__(self) -> "PublicWebClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()

    async def _robots_allowed(self, canonical_url: str) -> bool:
        parts = urlsplit(canonical_url)
        robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
        try:
            response = await self.client.get(robots_url)
            if response.status_code >= 400:
                return not self.settings.public_web_robots_fail_closed
            parser = RobotFileParser()
            parser.set_url(robots_url)
            parser.parse(response.text.splitlines())
            return bool(parser.can_fetch(self.settings.public_web_user_agent, canonical_url))
        except Exception:
            return not self.settings.public_web_robots_fail_closed

    async def fetch(self, url: str) -> PublicPage:
        canonical = validate_public_url(
            url,
            allowed_domains=self.settings.public_web_allowed_domain_list(),
            resolver=self.resolver,
        )
        robots_allowed = await self._robots_allowed(canonical)
        if not robots_allowed:
            raise PermissionError("robots.txt does not allow collection for this URL")

        response = await self.client.get(canonical)
        response.raise_for_status()

        body = response.content
        max_bytes = int(self.settings.public_web_max_body_bytes)
        if len(body) > max_bytes:
            raise ValueError(f"Response body exceeds PUBLIC_WEB_MAX_BODY_BYTES ({max_bytes})")

        content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
        textual = content_type.startswith("text/") or content_type in {
            "application/json",
            "application/ld+json",
            "application/xml",
            "application/xhtml+xml",
        }
        body_text = response.text if textual else None

        return PublicPage(
            source_url=url,
            canonical_url=canonical,
            status_code=response.status_code,
            content_type=content_type,
            body_text=body_text,
            body_bytes=len(body),
            content_hash=hashlib.sha256(body).hexdigest(),
            robots_allowed=True,
            collected_at=datetime.now(timezone.utc),
        )
