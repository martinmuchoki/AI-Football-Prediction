from fastapi.testclient import TestClient

from app.main import app


def test_odds_history_api_routes_exist():
    with TestClient(app) as client:
        result = client.get("/api/v1/odds/history?competition=2&season=2026")
        assert result.status_code == 200
        assert isinstance(result.json(), list)

        result = client.get("/api/v1/odds/movement?fixture_id=999999999")
        assert result.status_code == 200
        assert result.json()["status"] == "not_found"
