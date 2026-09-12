from fastapi.testclient import TestClient

from app.main import app


def test_market_status_route_exists():
    with TestClient(app) as client:
        result = client.get("/api/v1/market/status?competition=2&season=2026")
        assert result.status_code == 200
        payload = result.json()
        assert payload["status"] == "ok"
        assert payload["competition_id"] == 2
        assert payload["final_holdout_touched"] is False
