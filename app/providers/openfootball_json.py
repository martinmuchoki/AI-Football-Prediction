from __future__ import annotations

import base64
import json
from dataclasses import dataclass
from typing import Any

import httpx


@dataclass(frozen=True)
class OpenFootballDocument:
    api_url: str
    payload: dict[str, Any]
    raw_text: str
    response_etag: str | None


class OpenFootballJsonClient:
    """Public CC0 OpenFootball dataset reader via GitHub Contents API."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 20.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.client = httpx.AsyncClient(
            timeout=max(1.0, float(timeout_seconds)),
            follow_redirects=True,
            headers={
                "User-Agent": "MDRN-SportsQ-OpenFootball/0.10.4",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            },
            transport=transport,
        )

    async def __aenter__(self) -> "OpenFootballJsonClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.client.aclose()

    @staticmethod
    def api_url(*, season_label: str = "2026-27", league_file: str = "en.1.json") -> str:
        clean_season = season_label.strip()
        clean_file = league_file.strip()
        if not clean_season or "/" in clean_season or ".." in clean_season:
            raise ValueError("invalid season_label")
        if not clean_file or "/" in clean_file or ".." in clean_file:
            raise ValueError("invalid league_file")
        return (
            "https://api.github.com/repos/openfootball/football.json/contents/"
            f"{clean_season}/{clean_file}?ref=master"
        )

    async def fetch_league(
        self,
        *,
        season_label: str = "2026-27",
        league_file: str = "en.1.json",
    ) -> OpenFootballDocument:
        url = self.api_url(season_label=season_label, league_file=league_file)
        response = await self.client.get(url)
        response.raise_for_status()
        envelope = response.json()
        if not isinstance(envelope, dict):
            raise ValueError("OpenFootball GitHub response is not an object")
        if envelope.get("encoding") != "base64" or not isinstance(envelope.get("content"), str):
            raise ValueError("OpenFootball GitHub response has no base64 file content")

        raw_bytes = base64.b64decode(envelope["content"], validate=False)
        raw_text = raw_bytes.decode("utf-8-sig")
        payload = json.loads(raw_text)
        if not isinstance(payload, dict) or not isinstance(payload.get("matches"), list):
            raise ValueError("OpenFootball payload has no matches list")

        return OpenFootballDocument(
            api_url=url,
            payload=payload,
            raw_text=raw_text,
            response_etag=response.headers.get("etag"),
        )
