import csv
import json
from pathlib import Path

import numpy as np

from app.services.baseline_models import (
    accuracy_score_3way,
    brier_score_3way,
    evaluate_baselines,
    log_loss_3way,
    poisson_feature_columns,
    production_feature_columns,
)


def test_metrics_are_zero_or_one_for_perfect_probabilities():
    y = np.asarray([0, 1, 2], dtype=int)
    probs = np.asarray(
        [
            [1.0, 0.0, 0.0],
            [0.0, 1.0, 0.0],
            [0.0, 0.0, 1.0],
        ]
    )
    assert accuracy_score_3way(y, probs) == 1.0
    assert log_loss_3way(y, probs) < 1e-10
    assert brier_score_3way(y, probs) < 1e-10


def test_production_features_exclude_targets_ids_and_closing_market():
    fields = [
        "fixture_id",
        "season",
        "home_team",
        "target_class",
        "home_form5_points_pg",
        "market_avg_open_home_prob",
        "market_avg_close_home_prob",
        "market_home_prob_move",
    ]
    cols = production_feature_columns(fields)
    assert "home_form5_points_pg" in cols
    assert "market_avg_open_home_prob" in cols
    assert "market_avg_close_home_prob" not in cols
    assert "market_home_prob_move" not in cols
    assert "target_class" not in cols
    assert "fixture_id" not in cols

    poisson_cols = poisson_feature_columns(fields)
    assert "home_form5_points_pg" in poisson_cols
    assert all(not c.startswith("market_") for c in poisson_cols)


def _write_dataset(path: Path, *, start: int, count: int) -> None:
    fields = [
        "fixture_id",
        "provider_fixture_id",
        "season",
        "kickoff_utc",
        "home_team",
        "away_team",
        "home_matches_played",
        "away_matches_played",
        "season_progress",
        "home_rest_days",
        "away_rest_days",
        "home_elo_pre",
        "away_elo_pre",
        "elo_diff_pre",
        "elo_home_expected",
        "home_form5_points_pg",
        "away_form5_points_pg",
        "home_form5_goals_for_pg",
        "away_form5_goals_for_pg",
        "home_form5_goals_against_pg",
        "away_form5_goals_against_pg",
        "home_home5_points_pg",
        "away_away5_points_pg",
        "market_avg_open_home_prob",
        "market_avg_open_draw_prob",
        "market_avg_open_away_prob",
        "market_avg_close_home_prob",
        "market_avg_close_draw_prob",
        "market_avg_close_away_prob",
        "market_home_prob_move",
        "target_result",
        "target_class",
        "target_home_goals",
        "target_away_goals",
    ]
    rows = []
    for i in range(start, start + count):
        cls = i % 3
        if cls == 0:
            result, hg, ag = "H", 2, 0
        elif cls == 1:
            result, hg, ag = "D", 1, 1
        else:
            result, hg, ag = "A", 0, 2

        home_prob = 0.56 if cls == 0 else 0.28
        draw_prob = 0.50 if cls == 1 else 0.22
        away_prob = 1.0 - home_prob - draw_prob
        # Keep all probabilities positive.
        if away_prob <= 0:
            away_prob = 0.22
            total = home_prob + draw_prob + away_prob
            home_prob /= total
            draw_prob /= total
            away_prob /= total

        rows.append(
            {
                "fixture_id": i,
                "provider_fixture_id": 10000 + i,
                "season": 2022 if i < start + count // 2 else 2023,
                "kickoff_utc": f"2024-01-{(i % 28) + 1:02d}T15:00:00",
                "home_team": f"H{i % 8}",
                "away_team": f"A{i % 8}",
                "home_matches_played": i % 20,
                "away_matches_played": (i + 3) % 20,
                "season_progress": (i % 20) / 38,
                "home_rest_days": "" if i % 19 == 0 else 7,
                "away_rest_days": "" if i % 17 == 0 else 7,
                "home_elo_pre": 1500 + (30 if cls == 0 else -20 if cls == 2 else 0),
                "away_elo_pre": 1500,
                "elo_diff_pre": 30 if cls == 0 else -20 if cls == 2 else 0,
                "elo_home_expected": 0.58 if cls == 0 else 0.46 if cls == 2 else 0.52,
                "home_form5_points_pg": 2.0 if cls == 0 else 1.1,
                "away_form5_points_pg": 2.0 if cls == 2 else 1.0,
                "home_form5_goals_for_pg": 1.8 if cls == 0 else 1.1,
                "away_form5_goals_for_pg": 1.8 if cls == 2 else 1.0,
                "home_form5_goals_against_pg": 0.8 if cls == 0 else 1.2,
                "away_form5_goals_against_pg": 0.8 if cls == 2 else 1.2,
                "home_home5_points_pg": 2.1 if cls == 0 else 1.0,
                "away_away5_points_pg": 2.0 if cls == 2 else 1.0,
                "market_avg_open_home_prob": home_prob,
                "market_avg_open_draw_prob": draw_prob,
                "market_avg_open_away_prob": away_prob,
                "market_avg_close_home_prob": home_prob,
                "market_avg_close_draw_prob": draw_prob,
                "market_avg_close_away_prob": away_prob,
                "market_home_prob_move": 0.0,
                "target_result": result,
                "target_class": cls,
                "target_home_goals": hg,
                "target_away_goals": ag,
            }
        )

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_evaluate_baselines_writes_report_without_touching_holdout(tmp_path: Path):
    train = tmp_path / "train.csv"
    validation = tmp_path / "validation.csv"
    report = tmp_path / "baseline-report.json"
    _write_dataset(train, start=0, count=90)
    _write_dataset(validation, start=100, count=30)

    result = evaluate_baselines(
        train_path=str(train),
        validation_path=str(validation),
        output_path=str(report),
    )

    assert result["status"] == "success"
    assert result["train_rows"] == 90
    assert result["validation_rows"] == 30
    assert result["final_holdout_touched"] is False
    assert set(result["models"]) == {
        "majority",
        "bookmaker_open",
        "bookmaker_close",
        "elo",
        "poisson",
        "logistic",
    }

    payload = json.loads(report.read_text(encoding="utf-8"))
    assert "2025/26 final holdout" in payload["evaluation_policy"]
    assert payload["model_details"]["logistic"]["excludes_closing_market"] is True
    assert Path(result["predictions"]).exists()
