import base64
import json

import httpx
import pytest

from app.providers.openfootball_json import OpenFootballJsonClient


@pytest.mark.asyncio
async def test_openfootball_client_decodes_github_contents_api():
    payload = {
        "name": "Premier League 2026/27",
        "matches": [
            {
                "round": "Matchday 1",
                "date": "2026-08-15",
                "team1": "Chelsea",
                "team2": "Arsenal",
                "score": {"ft": [2, 1]},
            }
        ],
    }
    encoded = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "api.github.com"
        return httpx.Response(
            200,
            json={"encoding": "base64", "content": encoded},
            headers={"etag": '"abc123"'},
        )

    async with OpenFootballJsonClient(
        transport=httpx.MockTransport(handler)
    ) as client:
        doc = await client.fetch_league(season_label="2026-27")

    assert doc.payload["matches"][0]["team1"] == "Chelsea"
    assert doc.response_etag == '"abc123"'
