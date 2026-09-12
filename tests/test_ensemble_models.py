import csv
import json
from pathlib import Path

import numpy as np

from app.services.ensemble_models import (
    apply_temperature,
    blend_probabilities,
    curated_feature_columns,
    evaluate_phase4_ensemble,
    optimize_blend_weights,
)


def test_curated_features_are_production_safe():
    fields = [
        "target_class",
        "market_avg_close_home_prob",
        "market_home_prob_move",
        "market_avg_open_home_prob",
        "elo_diff_pre",
        "form5_points_pg_diff",
    ]
    selected = curated_feature_columns(fields)
    assert "market_avg_open_home_prob" in selected
    assert "elo_diff_pre" in selected
    assert "form5_points_pg_diff" in selected
    assert "market_avg_close_home_prob" not in selected
    assert "market_home_prob_move" not in selected
    assert "target_class" not in selected


def test_temperature_preserves_probability_rows():
    probs = np.asarray([[0.70, 0.20, 0.10], [0.20, 0.30, 0.50]])
    result = apply_temperature(probs, 1.5)
    assert np.allclose(result.sum(axis=1), 1.0)
    assert np.all(result > 0)


def test_blend_weights_are_normalized():
    models = {
        "a": np.asarray([[0.6, 0.2, 0.2]]),
        "b": np.asarray([[0.3, 0.4, 0.3]]),
    }
    result = blend_probabilities(models, {"a": 2.0, "b": 1.0})
    assert np.allclose(result.sum(axis=1), 1.0)
    assert result.shape == (1, 3)


def test_weight_optimizer_can_select_strong_model():
    y = np.asarray([0, 1, 2, 0, 1, 2] * 10)
    perfect = np.full((len(y), 3), 0.05)
    perfect[np.arange(len(y)), y] = 0.90
    weak = np.full((len(y), 3), 1 / 3)
    weights, loss = optimize_blend_weights(
        y,
        {"perfect": perfect, "weak": weak},
        candidates=500,
    )
    assert abs(sum(weights.values()) - 1.0) < 1e-6
    assert weights["perfect"] > weights["weak"]
    assert loss < 0.2


def _write_dataset(path: Path, season: int, count: int, offset: int) -> None:
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
        "form3_points_pg_diff",
        "form3_win_rate_diff",
        "form3_goals_for_pg_diff",
        "form3_goals_against_pg_diff",
        "form3_goal_diff_pg_diff",
        "form3_shots_for_pg_diff",
        "form3_sot_for_pg_diff",
        "form3_corners_for_pg_diff",
        "form5_points_pg_diff",
        "form5_win_rate_diff",
        "form5_goals_for_pg_diff",
        "form5_goals_against_pg_diff",
        "form5_goal_diff_pg_diff",
        "form5_shots_for_pg_diff",
        "form5_sot_for_pg_diff",
        "form5_corners_for_pg_diff",
        "form10_points_pg_diff",
        "form10_win_rate_diff",
        "form10_goals_for_pg_diff",
        "form10_goals_against_pg_diff",
        "form10_goal_diff_pg_diff",
        "form10_shots_for_pg_diff",
        "form10_sot_for_pg_diff",
        "form10_corners_for_pg_diff",
        "home_home5_points_pg",
        "away_away5_points_pg",
        "home_home5_goal_diff_pg",
        "away_away5_goal_diff_pg",
        "venue5_points_pg_diff",
        "venue5_goal_diff_pg_diff",
        "market_avg_open_home_prob",
        "market_avg_open_draw_prob",
        "market_avg_open_away_prob",
        "market_avg_open_overround",
        "market_avg_open_over25_prob",
        "market_avg_open_under25_prob",
        "market_avg_open_total_overround",
        "market_ah_open_line",
        "market_ah_open_home_prob",
        "market_ah_open_away_prob",
        "market_ah_open_overround",
        "target_result",
        "target_class",
        "target_home_goals",
        "target_away_goals",
    ]
    rows = []
    for j in range(count):
        i = offset + j
        cls = i % 3
        result = ("H", "D", "A")[cls]
        hg, ag = ((2, 0), (1, 1), (0, 2))[cls]
        # A useful but imperfect market signal.
        if cls == 0:
            p = (0.56, 0.24, 0.20)
        elif cls == 1:
            p = (0.34, 0.40, 0.26)
        else:
            p = (0.28, 0.24, 0.48)
        signal = 1.0 if cls == 0 else -1.0 if cls == 2 else 0.0
        row = {
            "fixture_id": i,
            "provider_fixture_id": 100000 + i,
            "season": season,
            "kickoff_utc": f"{season+1}-01-{(j % 28)+1:02d}T15:00:00",
            "home_team": f"H{j % 10}",
            "away_team": f"A{j % 10}",
            "home_matches_played": j % 30,
            "away_matches_played": (j + 3) % 30,
            "season_progress": (j % 30) / 38,
            "home_rest_days": "" if j % 31 == 0 else 7,
            "away_rest_days": "" if j % 29 == 0 else 7,
            "home_elo_pre": 1500 + 50 * signal,
            "away_elo_pre": 1500,
            "elo_diff_pre": 50 * signal,
            "elo_home_expected": 0.58 if cls == 0 else 0.44 if cls == 2 else 0.51,
            "market_avg_open_home_prob": p[0],
            "market_avg_open_draw_prob": p[1],
            "market_avg_open_away_prob": p[2],
            "market_avg_open_overround": 1.05,
            "market_avg_open_over25_prob": 0.52,
            "market_avg_open_under25_prob": 0.48,
            "market_avg_open_total_overround": 1.04,
            "market_ah_open_line": -0.25 * signal,
            "market_ah_open_home_prob": 0.54 if cls == 0 else 0.46,
            "market_ah_open_away_prob": 0.46 if cls == 0 else 0.54,
            "market_ah_open_overround": 1.03,
            "target_result": result,
            "target_class": cls,
            "target_home_goals": hg,
            "target_away_goals": ag,
        }
        for window in (3, 5, 10):
            for key in (
                "points_pg_diff",
                "win_rate_diff",
                "goals_for_pg_diff",
                "goals_against_pg_diff",
                "goal_diff_pg_diff",
                "shots_for_pg_diff",
                "sot_for_pg_diff",
                "corners_for_pg_diff",
            ):
                row[f"form{window}_{key}"] = signal + ((j % 5) - 2) * 0.02
        row["home_home5_points_pg"] = 1.8 + 0.2 * signal
        row["away_away5_points_pg"] = 1.3 - 0.2 * signal
        row["home_home5_goal_diff_pg"] = 0.4 * signal
        row["away_away5_goal_diff_pg"] = -0.3 * signal
        row["venue5_points_pg_diff"] = 0.4 * signal
        row["venue5_goal_diff_pg_diff"] = 0.5 * signal
        rows.append(row)

    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def test_phase4_end_to_end_keeps_holdout_sealed(tmp_path: Path):
    f1_train = tmp_path / "f1-train.csv"
    f1_val = tmp_path / "f1-val.csv"
    f2_train = tmp_path / "f2-train.csv"
    f2_val = tmp_path / "f2-val.csv"
    out = tmp_path / "phase4.json"

    _write_dataset(f1_train, 2022, 120, 0)
    _write_dataset(f1_val, 2023, 90, 1000)
    _write_dataset(f2_train, 2022, 210, 2000)
    _write_dataset(f2_val, 2024, 90, 3000)

    result = evaluate_phase4_ensemble(
        fold1_train_path=str(f1_train),
        fold1_validation_path=str(f1_val),
        fold2_train_path=str(f2_train),
        fold2_validation_path=str(f2_val),
        output_path=str(out),
    )

    assert result["status"] == "success"
    assert result["final_holdout_touched"] is False
    assert result["fold1_validation_rows"] == 90
    assert result["fold2_validation_rows"] == 90
    assert abs(sum(result["ensemble_weights"].values()) - 1.0) < 1e-5
    assert 0.55 <= result["temperature"] <= 2.50

    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["final_holdout_touched"] is False
    assert "2025/26 final holdout" in payload["holdout_policy"]
    assert Path(payload["fold1"]["predictions"]).exists()
    assert Path(payload["fold2"]["predictions"]).exists()
