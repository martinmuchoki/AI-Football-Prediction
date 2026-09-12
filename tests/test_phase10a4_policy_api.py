from fastapi.testclient import TestClient

from app.main import app


def test_source_policy_api_route_exists():
    with TestClient(app) as client:
        response = client.get("/api/v1/sources/policies")
        assert response.status_code == 200
        assert isinstance(response.json(), list)
