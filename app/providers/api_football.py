from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from typing import Any, Iterable

import httpx

from app.config import Settings, get_settings


class ApiFootballError(RuntimeError):
    pass


@dataclass(frozen=True)
class ApiResponse:
    data: list[dict[str, Any]]
    results: int
    page_current: int
    page_total: int
    requests_remaining: int | None


class ApiFootballClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._external_client = client is not None
        self.client = client or httpx.AsyncClient(
            base_url=self.settings.af_base_url.rstrip("/"),
            timeout=self.settings.af_timeout_seconds,
            headers={
                "x-apisports-key": self.settings.require_api_key(),
                "Accept": "application/json",
                "User-Agent": "AI-Football-Prediction/0.1.0-phase1",
            },
        )

    async def __aenter__(self) -> "ApiFootballClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self._external_client:
            await self.client.aclose()

    async def _get(self, endpoint: str, params: dict[str, Any]) -> ApiResponse:
        max_attempts = max(1, int(self.settings.af_max_retries))
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            try:
                response = await self.client.get(endpoint, params=params)
                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    if attempt + 1 < max_attempts:
                        await asyncio.sleep(min(2 ** attempt, 8))
                        continue
                response.raise_for_status()
                payload = response.json()
                errors = payload.get("errors")
                if errors:
                    raise ApiFootballError(f"API-Football returned errors: {errors}")
                data = payload.get("response")
                if not isinstance(data, list):
                    raise ApiFootballError("API-Football response field is not a list")

                paging = payload.get("paging") or {}
                remaining = response.headers.get("x-ratelimit-requests-remaining")
                return ApiResponse(
                    data=data,
                    results=int(payload.get("results") or len(data)),
                    page_current=int(paging.get("current") or 1),
                    page_total=int(paging.get("total") or 1),
                    requests_remaining=int(remaining) if remaining and remaining.isdigit() else None,
                )
            except (httpx.HTTPError, ValueError, ApiFootballError) as exc:
                last_error = exc
                if isinstance(exc, ApiFootballError):
                    break
                if attempt + 1 < max_attempts:
                    await asyncio.sleep(min(2 ** attempt, 8))

        raise ApiFootballError(f"API request failed for {endpoint}: {last_error}") from last_error

    async def get_fixtures(
        self,
        *,
        league: int | None = None,
        season: int | None = None,
        date_value: date | None = None,
        from_date: date | None = None,
        to_date: date | None = None,
        status: str | None = None,
        ids: Iterable[int] | None = None,
        timezone_name: str = "UTC",
    ) -> ApiResponse:
        params: dict[str, Any] = {"timezone": timezone_name}

        ids_list = [int(x) for x in ids] if ids is not None else []
        if ids_list:
            if len(ids_list) > 20:
                raise ValueError("API-Football fixtures ids query supports at most 20 IDs per request")
            params["ids"] = "-".join(str(x) for x in ids_list)
        else:
            if league is not None:
                params["league"] = int(league)
            if season is not None:
                params["season"] = int(season)
            if date_value is not None:
                params["date"] = date_value.isoformat()
            if from_date is not None:
                params["from"] = from_date.isoformat()
            if to_date is not None:
                params["to"] = to_date.isoformat()
            if status:
                params["status"] = status

        return await self._get("/fixtures", params)

    async def get_league(self, *, league: int, season: int) -> ApiResponse:
        return await self._get("/leagues", {"id": int(league), "season": int(season)})


    async def get_odds(
        self,
        *,
        fixture: int,
        bookmaker: int | None = None,
        bet: int | None = 1,
    ) -> ApiResponse:
        """Return API-Football pre-match odds for one fixture.

        SportsQ intentionally queries by fixture ID to keep provider identity
        deterministic and auditable.
        """
        params: dict[str, Any] = {
            "fixture": int(fixture),
        }
        if bookmaker is not None:
            params["bookmaker"] = int(bookmaker)
        if bet is not None:
            params["bet"] = int(bet)

        return await self._get("/odds", params)
