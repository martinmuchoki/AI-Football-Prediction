from fastapi.testclient import TestClient

from app.main import app


def test_phase8_health():
    with TestClient(app) as client:
        result = client.get("/health")
        assert result.status_code == 200
        assert result.json()["phase"] >= 8


def test_phase8_routes_exist():
    paths = {route.path for route in app.routes}
    assert "/predictions/live" in paths
    assert "/predictions/performance" in paths
