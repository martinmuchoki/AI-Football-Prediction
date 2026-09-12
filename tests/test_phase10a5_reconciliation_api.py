from fastapi.testclient import TestClient

from app.main import app


def test_reconciliation_api_routes_exist():
    with TestClient(app) as client:
        response = client.get("/api/v1/reconciliation/summary?competition=2&season=2026")
        assert response.status_code == 200
        assert response.json()["quality_gate"] in {"UNKNOWN", "PASS", "WARN", "FAIL"}

        response = client.get("/api/v1/reconciliation/issues?competition=2&season=2026")
        assert response.status_code == 200
        assert isinstance(response.json(), list)

        response = client.get("/api/v1/reconciliation/health?competition=2&season=2026")
        assert response.status_code == 200

        health = response.json()

        assert health["operational_status"] in {
            "HEALTHY",
            "DEGRADED",
            "BLOCKED",
            "STALE",
            "UNINITIALIZED",
        }
        assert health["quality_gate"] in {
            "UNKNOWN",
            "PASS",
            "WARN",
            "FAIL",
            "STALE",
        }
        assert isinstance(
            health["prediction_lock_allowed"],
            bool,
        )
        assert health["final_holdout_touched"] is False
