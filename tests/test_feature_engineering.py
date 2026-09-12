from __future__ import annotations

import json

from sqlalchemy import select

from app.models import Fixture
from app.services.feature_engineering import build_feature_dataset, build_feature_rows, load_match_records
from app.services.football_data_ingestor import ingest_rows


def _row(date, home, away, hg, ag, hs, ass, *, avgh="2.00", avgd="3.40", avga="4.00", avgch="1.90", avgcd="3.50", avgca="4.20"):
    return {
        "Div": "E0",
        "Date": date,
        "Time": "15:00",
        "HomeTeam": home,
        "AwayTeam": away,
        "FTHG": str(hg),
        "FTAG": str(ag),
        "FTR": "H" if hg > ag else "A" if hg < ag else "D",
        "HTHG": "0",
        "HTAG": "0",
        "HS": str(hs),
        "AS": str(ass),
        "HST": "7",
        "AST": "2",
        "HF": "10",
        "AF": "11",
        "HC": "6",
        "AC": "3",
        "HY": "1",
        "AY": "2",
        "HR": "0",
        "AR": "0",
        "AvgH": avgh,
        "AvgD": avgd,
        "AvgA": avga,
        "B365H": avgh,
        "B365D": avgd,
        "B365A": avga,
        "PSH": avgh,
        "PSD": avgd,
        "PSA": avga,
        "AvgCH": avgch,
        "AvgCD": avgcd,
        "AvgCA": avgca,
        "B365CH": avgch,
        "B365CD": avgcd,
        "B365CA": avgca,
        "PSCH": avgch,
        "PSCD": avgcd,
        "PSCA": avgca,
        "Avg>2.5": "1.90",
        "Avg<2.5": "2.00",
        "AvgC>2.5": "1.85",
        "AvgC<2.5": "2.05",
        "AHh": "-0.5",
        "AvgAHH": "1.95",
        "AvgAHA": "1.95",
        "AHCh": "-0.75",
        "AvgCAHH": "1.96",
        "AvgCAHA": "1.94",
    }


def test_feature_rows_do_not_leak_current_match_stats(session):
    ingest_rows(
        session,
        [
            _row("01/08/2025", "Alpha", "Beta", 2, 0, 20, 5),
            _row("08/08/2025", "Beta", "Alpha", 5, 4, 99, 88),
        ],
        season=2025,
        league_code="E0",
    )

    records = load_match_records(session, from_season=2025, to_season=2025)
    rows = build_feature_rows(records)

    first, second = rows
    assert first["home_form3_matches"] == 0.0
    assert first["home_form3_shots_for_pg"] == 0.0
    assert first["away_form3_shots_for_pg"] == 0.0

    # Match 2 uses Match 1 only. Its own 99/88 shots must not enter its pre-match features.
    assert second["home_team"] == "Beta"
    assert second["away_team"] == "Alpha"
    assert second["home_form3_shots_for_pg"] == 5.0
    assert second["away_form3_shots_for_pg"] == 20.0
    assert second["home_form3_points_pg"] == 0.0
    assert second["away_form3_points_pg"] == 3.0


def test_market_probabilities_are_devigged(session):
    ingest_rows(
        session,
        [_row("01/08/2025", "Alpha", "Beta", 1, 0, 10, 8)],
        season=2025,
        league_code="E0",
    )
    row = build_feature_rows(load_match_records(session, from_season=2025, to_season=2025))[0]
    total = row["market_avg_open_home_prob"] + row["market_avg_open_draw_prob"] + row["market_avg_open_away_prob"]
    assert abs(total - 1.0) < 1e-12
    assert row["market_avg_open_overround"] > 1.0
    assert row["market_home_prob_move"] is not None


def test_season_state_resets(session):
    ingest_rows(session, [_row("01/08/2024", "Alpha", "Beta", 3, 0, 12, 4)], season=2024, league_code="E0")
    ingest_rows(session, [_row("01/08/2025", "Alpha", "Beta", 0, 0, 8, 8)], season=2025, league_code="E0")
    rows = build_feature_rows(load_match_records(session, from_season=2024, to_season=2025))
    second = rows[1]
    assert second["season"] == 2025
    assert second["home_matches_played"] == 0
    assert second["away_matches_played"] == 0
    assert second["home_elo_pre"] == 1500.0
    assert second["away_elo_pre"] == 1500.0


def test_build_feature_dataset_writes_csv_and_metadata(session, tmp_path):
    ingest_rows(
        session,
        [
            _row("01/08/2025", "Alpha", "Beta", 2, 1, 14, 9),
            _row("08/08/2025", "Beta", "Alpha", 1, 1, 11, 10),
        ],
        season=2025,
        league_code="E0",
    )
    output = tmp_path / "features.csv"
    result = build_feature_dataset(
        session,
        output_path=str(output),
        from_season=2025,
        to_season=2025,
    )
    assert result["rows_written"] == 2
    assert result["feature_columns"] > 100
    assert output.exists()
    meta = json.loads((tmp_path / "features.csv.meta.json").read_text(encoding="utf-8"))
    assert meta["rows"] == 2
    assert "updated only after" in meta["leakage_policy"]
