from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

import app.main as main_module
from app.db import Base
from app.main import app
from app.services.source_registry import seed_default_sources


def test_phase10a_health():
    with TestClient(app) as client:
        result = client.get("/api/v1/health")
        assert result.status_code == 200
        body = result.json()
        assert body["phase"] == "10A"
        assert body["service"] == "MDRN SportsQ API"


def test_sources_endpoint(monkeypatch):
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    with Factory() as session:
        seed_default_sources(session)

    monkeypatch.setattr(main_module, "SessionLocal", Factory)
    with TestClient(app) as client:
        result = client.get("/api/v1/sources")
        assert result.status_code == 200
        rows = result.json()
        assert {row["slug"] for row in rows} == {
            "live-score-api",
            "football-data-csv",
            "api-football",
            "openfootball-json",
        }
        assert rows[0]["slug"] == "live-score-api"
