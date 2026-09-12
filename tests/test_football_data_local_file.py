from pathlib import Path
from sqlalchemy import func, select

from app.models import Fixture
from app.services.football_data_ingestor import sync_football_data_history_file


SAMPLE = """Div,Date,Time,HomeTeam,AwayTeam,FTHG,FTAG,FTR,HTHG,HTAG,HTR,Referee
E0,15/08/2025,20:00,Liverpool,Bournemouth,4,2,H,1,0,H,A Ref
E0,16/08/2025,12:30,Aston Villa,Newcastle,0,0,D,0,0,D,B Ref
"""


def test_local_csv_import(session, tmp_path: Path):
    csv_path = tmp_path / "E0.csv"
    csv_path.write_text(SAMPLE, encoding="utf-8")

    result = sync_football_data_history_file(
        session,
        file_path=str(csv_path),
        season=2025,
        league_code="E0",
    )

    assert result["received"] == 2
    assert result["inserted"] == 2
    assert result["rejected"] == 0
    assert result["source_file"].endswith("E0.csv")
    assert session.scalar(select(func.count(Fixture.id))) == 2


def test_local_csv_import_is_idempotent(session, tmp_path: Path):
    csv_path = tmp_path / "E0.csv"
    csv_path.write_text(SAMPLE, encoding="utf-8")

    first = sync_football_data_history_file(
        session,
        file_path=str(csv_path),
        season=2025,
        league_code="E0",
    )
    second = sync_football_data_history_file(
        session,
        file_path=str(csv_path),
        season=2025,
        league_code="E0",
    )

    assert first["inserted"] == 2
    assert second["updated"] == 2
    assert session.scalar(select(func.count(Fixture.id))) == 2
