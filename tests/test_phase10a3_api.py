from fastapi.testclient import TestClient

from app.main import app


def test_web_targets_api_route_exists():
    with TestClient(app) as client:
        response = client.get("/api/v1/web/targets")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
