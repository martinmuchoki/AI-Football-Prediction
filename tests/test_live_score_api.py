import httpx
import pytest

from app.config import Settings
from app.providers.live_score_api import LiveScoreApiClient


@pytest.mark.asyncio
async def test_live_score_connector_adds_credentials_and_filters():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["url"] = str(request.url)
        return httpx.Response(
            200,
            json={"success": True, "data": {"fixtures": []}},
        )

    settings = Settings(
        _env_file=None,
        ls_api_key="test-key",
        ls_api_secret="test-secret",
        ls_base_url="https://livescore-api.com/api-client",
    )
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url=settings.ls_base_url,
    ) as http_client:
        client = LiveScoreApiClient(settings, client=http_client)
        response = await client.get_fixtures(competition_id=2, page=3)

    assert "key=test-key" in seen["url"]
    assert "secret=test-secret" in seen["url"]
    assert "competition_id=2" in seen["url"]
    assert "page=3" in seen["url"]
    assert response.data["fixtures"] == []
