import httpx
import pytest

from app.config import Settings
from app.providers.api_football import ApiFootballClient


@pytest.mark.asyncio
async def test_connector_sends_api_key_and_fixture_filters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["key"] = request.headers.get("x-apisports-key")
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={
                "get": "fixtures",
                "parameters": {"league": "39"},
                "errors": [],
                "results": 0,
                "paging": {"current": 1, "total": 1},
                "response": [],
            },
            headers={"x-ratelimit-requests-remaining": "99"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(
        transport=transport,
        base_url="https://v3.football.api-sports.io",
        headers={"x-apisports-key": "test-secret"},
    ) as http_client:
        settings = Settings(
            _env_file=None,
            af_api_key="test-secret",
            af_base_url="https://v3.football.api-sports.io",
        )
        client = ApiFootballClient(settings, client=http_client)
        result = await client.get_fixtures(league=39, season=2026)

    assert seen["key"] == "test-secret"
    assert "league=39" in seen["url"]
    assert "season=2026" in seen["url"]
    assert "timezone=UTC" in seen["url"]
    assert result.requests_remaining == 99


@pytest.mark.asyncio
async def test_ids_are_limited_to_twenty():
    settings = Settings(_env_file=None, af_api_key="x")
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(200, json={"errors": [], "results": 0, "paging": {}, "response": []})),
        base_url="https://v3.football.api-sports.io",
    ) as http_client:
        client = ApiFootballClient(settings, client=http_client)
        with pytest.raises(ValueError):
            await client.get_fixtures(ids=range(1, 22))
