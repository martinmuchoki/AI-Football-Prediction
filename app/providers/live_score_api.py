from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import date
from email.utils import parsedate_to_datetime
from typing import Any

import httpx

from app.config import Settings, get_settings


class LiveScoreApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class LiveScoreResponse:
    data: dict[str, Any]

    @property
    def has_next_page(self) -> bool:
        return bool(self.data.get("next_page"))

    @property
    def has_prev_page(self) -> bool:
        return bool(self.data.get("prev_page"))


class LiveScoreApiClient:
    def __init__(
        self,
        settings: Settings | None = None,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.settings = settings or get_settings()
        self._external_client = client is not None
        self._key, self._secret = self.settings.require_live_score_credentials()

        timeout = httpx.Timeout(
            connect=max(0.1, float(self.settings.ls_connect_timeout_seconds)),
            read=max(0.1, float(self.settings.ls_read_timeout_seconds or self.settings.ls_timeout_seconds)),
            write=max(0.1, float(self.settings.ls_write_timeout_seconds)),
            pool=max(0.1, float(self.settings.ls_pool_timeout_seconds)),
        )
        self.client = client or httpx.AsyncClient(
            base_url=self.settings.ls_base_url.rstrip("/"),
            timeout=timeout,
            headers={
                "Accept": "application/json",
                "User-Agent": "AI-Football-Prediction/0.1.2-resilient-sync",
            },
        )

    async def __aenter__(self) -> "LiveScoreApiClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        if not self._external_client:
            await self.client.aclose()

    def _retry_delay(self, attempt: int, response: httpx.Response | None = None) -> float:
        # Respect Retry-After when it is safely parseable; otherwise use exponential backoff.
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    return min(float(retry_after), float(self.settings.ls_retry_max_seconds))
                except ValueError:
                    try:
                        retry_at = parsedate_to_datetime(retry_after)
                        seconds = max(0.0, retry_at.timestamp() - __import__("time").time())
                        return min(seconds, float(self.settings.ls_retry_max_seconds))
                    except (TypeError, ValueError, OverflowError):
                        pass
        base = max(0.0, float(self.settings.ls_retry_base_seconds))
        cap = max(base, float(self.settings.ls_retry_max_seconds))
        return min(base * (2 ** attempt), cap)

    @staticmethod
    def _safe_error(exc: Exception | None) -> str:
        # Never stringify HTTP exceptions because request URLs contain API credentials.
        if exc is None:
            return "unknown error"
        if isinstance(exc, httpx.HTTPStatusError):
            return f"HTTP {exc.response.status_code}"
        if isinstance(exc, httpx.RequestError):
            return type(exc).__name__
        if isinstance(exc, LiveScoreApiError):
            return str(exc)
        return type(exc).__name__

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> LiveScoreResponse:
        query = {"key": self._key, "secret": self._secret}
        if params:
            query.update({k: v for k, v in params.items() if v is not None})

        max_attempts = max(1, int(self.settings.ls_max_retries))
        last_error: Exception | None = None

        for attempt in range(max_attempts):
            response: httpx.Response | None = None
            try:
                response = await self.client.get(endpoint, params=query)

                if response.status_code == 429 or 500 <= response.status_code <= 599:
                    last_error = httpx.HTTPStatusError(
                        f"retryable HTTP {response.status_code}",
                        request=response.request,
                        response=response,
                    )
                    if attempt + 1 < max_attempts:
                        delay = self._retry_delay(attempt, response)
                        if delay > 0:
                            await asyncio.sleep(delay)
                        continue

                response.raise_for_status()
                payload = response.json()
                if payload.get("success") is not True:
                    provider_error = payload.get("error") or "Live Score API request failed"
                    raise LiveScoreApiError(str(provider_error))
                data = payload.get("data")
                if not isinstance(data, dict):
                    raise LiveScoreApiError("Live Score API data field is not an object")
                return LiveScoreResponse(data=data)

            except LiveScoreApiError:
                raise
            except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as exc:
                last_error = exc
                if isinstance(exc, httpx.HTTPStatusError):
                    status = exc.response.status_code
                    if status != 429 and not 500 <= status <= 599:
                        break
                if attempt + 1 < max_attempts:
                    delay = self._retry_delay(attempt, response)
                    if delay > 0:
                        await asyncio.sleep(delay)

        raise LiveScoreApiError(
            f"Live Score API request failed for {endpoint}: {self._safe_error(last_error)}"
        ) from last_error

    async def verify(self) -> LiveScoreResponse:
        return await self._get("/users/pair.json")

    async def get_fixtures(
        self,
        *,
        competition_id: int,
        date_value: date | str | None = None,
        page: int | None = None,
        team: int | None = None,
        round_value: str | int | None = None,
    ) -> LiveScoreResponse:
        return await self._get(
            "/fixtures/list.json",
            {
                "competition_id": int(competition_id),
                "date": date_value.isoformat() if isinstance(date_value, date) else date_value,
                "page": page,
                "team": team,
                "round": round_value,
            },
        )

    async def get_history(
        self,
        *,
        competition_id: int,
        from_date: date | None = None,
        to_date: date | None = None,
        page: int | None = None,
        team_id: int | None = None,
    ) -> LiveScoreResponse:
        return await self._get(
            "/matches/history.json",
            {
                "competition_id": int(competition_id),
                "from": from_date.isoformat() if from_date else None,
                "to": to_date.isoformat() if to_date else None,
                "page": page,
                "team_id": team_id,
            },
        )
