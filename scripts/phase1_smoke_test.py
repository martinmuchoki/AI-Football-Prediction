from datetime import datetime, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app.db import Base
from app.models import Fixture
from app.services.fixture_ingestor import ingest_payload
from tests.sample_data import fixture_payload


engine = create_engine("sqlite+pysqlite:///:memory:")
Base.metadata.create_all(engine)
Session = sessionmaker(bind=engine, expire_on_commit=False)

with Session() as session:
    ingest_payload(session, [fixture_payload()], "smoke")
    count = session.scalar(select(func.count(Fixture.id)))
    assert count == 1

print("Phase 1 smoke test passed at", datetime.now(timezone.utc).isoformat())
