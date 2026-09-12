import httpx
import pytest

from app.config import Settings
from app.providers.live_score_api import LiveScoreApiClient


@pytest.mark.asyncio
async def test_retries_429_without_exposing_credentials():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"success": True, "data": {"fixtures": []}}, request=request)

    settings = Settings(
        _env_file=None,
        ls_api_key="private-key",
        ls_api_secret="private-secret",
        ls_max_retries=3,
        ls_retry_base_seconds=0,
        ls_retry_max_seconds=0,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.ls_base_url,
    ) as http_client:
        client = LiveScoreApiClient(settings, client=http_client)
        result = await client.get_fixtures(competition_id=2)

    assert calls["count"] == 2
    assert result.data["fixtures"] == []


@pytest.mark.asyncio
async def test_final_http_error_message_does_not_leak_credentials():
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, request=request)

    settings = Settings(
        _env_file=None,
        ls_api_key="private-key",
        ls_api_secret="private-secret",
        ls_max_retries=1,
        ls_retry_base_seconds=0,
        ls_retry_max_seconds=0,
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.ls_base_url,
    ) as http_client:
        client = LiveScoreApiClient(settings, client=http_client)
        with pytest.raises(Exception) as excinfo:
            await client.get_fixtures(competition_id=2)

    message = str(excinfo.value)
    assert "private-key" not in message
    assert "private-secret" not in message
    assert "HTTP 503" in message
