from __future__ import annotations

import asyncio
from dataclasses import dataclass

import httpx

from app.config import Settings, get_settings


class FootballDataError(RuntimeError):
    pass


@dataclass(frozen=True)
class FootballDataResponse:
    text: str
    url: str


def season_code(season_start: int) -> str:
    year = int(season_start)
    return f"{year % 100:02d}{(year + 1) % 100:02d}"


class FootballDataClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._external_client = client is not None
        self.client = client or httpx.AsyncClient(
            timeout=httpx.Timeout(self.settings.fd_timeout_seconds),
            headers={
                "Accept": "text/csv,text/plain,*/*",
                "User-Agent": "AI-Football-Prediction/0.1.4",
            },
            follow_redirects=True,
        )

    async def __aenter__(self) -> "FootballDataClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self._external_client:
            await self.client.aclose()

    def build_url(self, *, season: int, league_code: str = "E0") -> str:
        code = season_code(season)
        return f"{self.settings.fd_base_url.rstrip('/')}/{code}/{league_code}.csv"

    async def get_csv(self, *, season: int, league_code: str = "E0") -> FootballDataResponse:
        url = self.build_url(season=season, league_code=league_code)
        attempts = max(1, int(self.settings.fd_max_retries))
        last_error: Exception | None = None

        for attempt in range(attempts):
            try:
                response = await self.client.get(url)
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    if attempt + 1 < attempts:
                        await asyncio.sleep(min(2 ** attempt, 8))
                        continue
                response.raise_for_status()
                text = response.text
                if "HomeTeam" not in text or "AwayTeam" not in text:
                    raise FootballDataError("Football-Data response is not a valid league CSV")
                return FootballDataResponse(text=text, url=url)
            except (httpx.HTTPError, FootballDataError) as exc:
                last_error = exc
                if isinstance(exc, FootballDataError):
                    break
                if attempt + 1 < attempts:
                    await asyncio.sleep(min(2 ** attempt, 8))

        raise FootballDataError(
            f"Football-Data download failed for season {season}, league {league_code}: {last_error}"
        ) from last_error
