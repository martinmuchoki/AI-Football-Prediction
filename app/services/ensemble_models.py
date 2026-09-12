from __future__ import annotations

import csv
import json
import math
from pathlib import Path
from typing import Any

import numpy as np
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from app.services.baseline_models import (
    CLASS_NAMES,
    _class_prior,
    _elo_probs,
    _fit_poisson,
    _labels,
    _market_probs,
    _matrix,
    _metrics,
    _normalize_probability_rows,
    _read_csv,
)


CURATED_FEATURE_CANDIDATES = [
    # Match / schedule state
    "home_matches_played",
    "away_matches_played",
    "season_progress",
    "home_rest_days",
    "away_rest_days",
    # Elo
    "home_elo_pre",
    "away_elo_pre",
    "elo_diff_pre",
    "elo_home_expected",
    # Rolling differences: compact, stable, leakage-safe summaries
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
    # Venue strength
    "home_home5_points_pg",
    "away_away5_points_pg",
    "home_home5_goal_diff_pg",
    "away_away5_goal_diff_pg",
    "venue5_points_pg_diff",
    "venue5_goal_diff_pg_diff",
    # Opening 1X2 market
    "market_avg_open_home_prob",
    "market_avg_open_draw_prob",
    "market_avg_open_away_prob",
    "market_avg_open_overround",
    # Opening totals market
    "market_avg_open_over25_prob",
    "market_avg_open_under25_prob",
    "market_avg_open_total_overround",
    # Opening Asian handicap market
    "market_ah_open_line",
    "market_ah_open_home_prob",
    "market_ah_open_away_prob",
    "market_ah_open_overround",
]


def curated_feature_columns(fields: list[str]) -> list[str]:
    """Return the stable production-safe Phase 4 feature subset."""
    available = set(fields)
    selected = [name for name in CURATED_FEATURE_CANDIDATES if name in available]
    if not selected:
        raise ValueError("No curated Phase 4 feature columns were found in the dataset")
    forbidden = [
        name
        for name in selected
        if "close" in name.lower()
        or "_prob_move" in name.lower()
        or name.startswith("target_")
    ]
    if forbidden:
        raise AssertionError(f"Forbidden leakage-prone features selected: {forbidden}")
    return selected


def _ordered_predict_proba(model: Any, x: np.ndarray) -> np.ndarray:
    probs = model.predict_proba(x)
    classes = list(model.classes_)
    ordered = np.zeros((len(x), 3), dtype=float)
    for source_index, class_value in enumerate(classes):
        ordered[:, int(class_value)] = probs[:, source_index]
    return _normalize_probability_rows(ordered)


def _fit_reduced_logistic(
    train_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    fields: list[str],
    y_train: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    columns = curated_feature_columns(fields)
    x_train = _matrix(train_rows, columns)
    x_val = _matrix(validation_rows, columns)

    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=0.05,
                    solver="lbfgs",
                    max_iter=2500,
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(x_train, y_train)
    probs = _ordered_predict_proba(model.named_steps["model"], model[:-1].transform(x_val))
    return probs, {
        "feature_count": len(columns),
        "features": columns,
        "regularization_C": 0.05,
    }


def _fit_gradient_boosting(
    train_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    fields: list[str],
    y_train: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    columns = curated_feature_columns(fields)
    x_train = _matrix(train_rows, columns)
    x_val = _matrix(validation_rows, columns)

    imputer = SimpleImputer(strategy="median", keep_empty_features=True)
    x_train_i = imputer.fit_transform(x_train)
    x_val_i = imputer.transform(x_val)

    model = HistGradientBoostingClassifier(
        learning_rate=0.05,
        max_iter=120,
        max_leaf_nodes=7,
        min_samples_leaf=30,
        l2_regularization=2.0,
        random_state=42,
    )
    model.fit(x_train_i, y_train)
    probs = _ordered_predict_proba(model, x_val_i)
    return probs, {
        "feature_count": len(columns),
        "features": columns,
        "learning_rate": 0.05,
        "max_iter": 120,
        "max_leaf_nodes": 7,
        "min_samples_leaf": 30,
        "l2_regularization": 2.0,
    }


def _fit_fold_models(
    train_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    fields: list[str],
) -> tuple[dict[str, np.ndarray], dict[str, Any]]:
    y_train = _labels(train_rows)
    prior = _class_prior(y_train)
    draw_rate = float(prior[1])

    probabilities: dict[str, np.ndarray] = {}
    details: dict[str, Any] = {}

    probabilities["bookmaker_open"] = _market_probs(
        validation_rows,
        prefix="avg_open",
        fallback=prior,
    )
    details["bookmaker_open"] = {
        "source": "market_avg_open_*_prob",
        "role": "production market anchor",
    }

    probabilities["elo"] = _elo_probs(validation_rows, draw_rate)
    details["elo"] = {"training_draw_rate": round(draw_rate, 6)}

    poisson_probs, poisson_details = _fit_poisson(
        train_rows,
        validation_rows,
        fields,
    )
    probabilities["poisson"] = poisson_probs
    details["poisson"] = poisson_details

    logistic_probs, logistic_details = _fit_reduced_logistic(
        train_rows,
        validation_rows,
        fields,
        y_train,
    )
    probabilities["reduced_logistic"] = logistic_probs
    details["reduced_logistic"] = logistic_details

    gradient_probs, gradient_details = _fit_gradient_boosting(
        train_rows,
        validation_rows,
        fields,
        y_train,
    )
    probabilities["gradient_boosting"] = gradient_probs
    details["gradient_boosting"] = gradient_details

    return probabilities, details


def blend_probabilities(
    model_probs: dict[str, np.ndarray],
    weights: dict[str, float],
) -> np.ndarray:
    if set(weights) != set(model_probs):
        raise ValueError("Weight keys must exactly match model probability keys")
    total = float(sum(weights.values()))
    if total <= 0:
        raise ValueError("Ensemble weights must sum to a positive value")
    normalized_weights = {name: float(value) / total for name, value in weights.items()}
    result = np.zeros_like(next(iter(model_probs.values())), dtype=float)
    for name, probs in model_probs.items():
        result += normalized_weights[name] * probs
    return _normalize_probability_rows(result)


def optimize_blend_weights(
    y_true: np.ndarray,
    model_probs: dict[str, np.ndarray],
    *,
    seed: int = 42,
    candidates: int = 6000,
) -> tuple[dict[str, float], float]:
    """
    Deterministic non-negative simplex search.

    The search runs only on Fold 1 validation predictions. Fold 2 stays unseen
    until weights are frozen.
    """
    names = list(model_probs)
    stack = np.stack([model_probs[name] for name in names], axis=1)  # n x models x 3
    rng = np.random.default_rng(seed)

    candidate_weights = [
        np.ones(len(names), dtype=float) / len(names),
    ]
    # Include every single-model corner so the ensemble can honestly fall back
    # to the market if no other signal improves validation log loss.
    for i in range(len(names)):
        corner = np.zeros(len(names), dtype=float)
        corner[i] = 1.0
        candidate_weights.append(corner)

    # Encourage practical blends while still allowing sparse solutions.
    random_weights = rng.dirichlet(np.full(len(names), 1.5), size=candidates)
    candidate_weights.extend(random_weights)

    best_loss = float("inf")
    best = candidate_weights[0]

    # Chunked evaluation keeps memory low on ordinary Windows machines.
    all_weights = np.asarray(candidate_weights, dtype=float)
    for start in range(0, len(all_weights), 500):
        chunk = all_weights[start : start + 500]
        blended = np.einsum("wm,nmc->wnc", chunk, stack)
        blended = np.clip(blended, 1e-15, 1.0)
        blended /= blended.sum(axis=2, keepdims=True)
        selected = blended[:, np.arange(len(y_true)), y_true]
        losses = -np.mean(np.log(selected), axis=1)
        local_index = int(np.argmin(losses))
        local_loss = float(losses[local_index])
        if local_loss < best_loss:
            best_loss = local_loss
            best = chunk[local_index].copy()

    weights = {
        name: round(float(value), 8)
        for name, value in zip(names, best / best.sum())
    }
    return weights, best_loss


def apply_temperature(probs: np.ndarray, temperature: float) -> np.ndarray:
    if temperature <= 0:
        raise ValueError("Temperature must be positive")
    p = _normalize_probability_rows(probs)
    adjusted = np.power(np.clip(p, 1e-15, 1.0), 1.0 / float(temperature))
    return _normalize_probability_rows(adjusted)


def fit_temperature(
    y_true: np.ndarray,
    probs: np.ndarray,
) -> tuple[float, float]:
    """
    Grid-search one scalar temperature using Fold 1 only.
    T > 1 softens overconfident probabilities; T < 1 sharpens them.
    """
    best_t = 1.0
    best_loss = float("inf")
    for temperature in np.linspace(0.55, 2.50, 196):
        calibrated = apply_temperature(probs, float(temperature))
        selected = calibrated[np.arange(len(y_true)), y_true]
        loss = float(-np.mean(np.log(np.clip(selected, 1e-15, 1.0))))
        if loss < best_loss:
            best_loss = loss
            best_t = float(temperature)
    return round(best_t, 4), best_loss


def _write_predictions(
    path: Path,
    rows: list[dict[str, str]],
    probs_by_model: dict[str, np.ndarray],
) -> None:
    output_rows: list[dict[str, Any]] = []
    for index, source in enumerate(rows):
        item: dict[str, Any] = {
            "fixture_id": source.get("fixture_id", ""),
            "season": source.get("season", ""),
            "kickoff_utc": source.get("kickoff_utc", ""),
            "home_team": source.get("home_team", ""),
            "away_team": source.get("away_team", ""),
            "target_result": source.get("target_result", ""),
            "target_class": source.get("target_class", ""),
        }
        for name, probs in probs_by_model.items():
            p = probs[index]
            item[f"{name}_p_home"] = round(float(p[0]), 8)
            item[f"{name}_p_draw"] = round(float(p[1]), 8)
            item[f"{name}_p_away"] = round(float(p[2]), 8)
            item[f"{name}_prediction"] = CLASS_NAMES[int(np.argmax(p))]
        output_rows.append(item)

    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(output_rows[0].keys()))
        writer.writeheader()
        writer.writerows(output_rows)


def evaluate_phase4_ensemble(
    *,
    fold1_train_path: str,
    fold1_validation_path: str,
    fold2_train_path: str,
    fold2_validation_path: str,
    output_path: str,
) -> dict[str, Any]:
    fold1_train, fields1 = _read_csv(fold1_train_path)
    fold1_val, fields1v = _read_csv(fold1_validation_path)
    fold2_train, fields2 = _read_csv(fold2_train_path)
    fold2_val, fields2v = _read_csv(fold2_validation_path)

    if not (fields1 == fields1v == fields2 == fields2v):
        raise ValueError("All Phase 4 datasets must have identical schemas")

    y1 = _labels(fold1_val)
    y2 = _labels(fold2_val)

    fold1_probs, fold1_details = _fit_fold_models(fold1_train, fold1_val, fields1)
    fold2_probs, fold2_details = _fit_fold_models(fold2_train, fold2_val, fields1)

    # Freeze ensemble weights using Fold 1 only.
    weights, fold1_optimized_loss = optimize_blend_weights(y1, fold1_probs)
    fold1_blend = blend_probabilities(fold1_probs, weights)

    # Freeze one calibration scalar using Fold 1 only.
    temperature, fold1_calibrated_loss = fit_temperature(y1, fold1_blend)
    fold1_calibrated = apply_temperature(fold1_blend, temperature)

    # Honest meta-validation: Fold 2 is first used after weights/T are frozen.
    fold2_blend = blend_probabilities(fold2_probs, weights)
    fold2_calibrated = apply_temperature(fold2_blend, temperature)

    fold1_report_probs = dict(fold1_probs)
    fold1_report_probs["ensemble_uncalibrated"] = fold1_blend
    fold1_report_probs["ensemble_calibrated"] = fold1_calibrated

    fold2_report_probs = dict(fold2_probs)
    fold2_report_probs["ensemble_uncalibrated"] = fold2_blend
    fold2_report_probs["ensemble_calibrated"] = fold2_calibrated

    fold1_metrics = {
        name: _metrics(y1, probs)
        for name, probs in fold1_report_probs.items()
    }
    fold2_metrics = {
        name: _metrics(y2, probs)
        for name, probs in fold2_report_probs.items()
    }

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    fold1_predictions = output.with_suffix(output.suffix + ".fold1.predictions.csv")
    fold2_predictions = output.with_suffix(output.suffix + ".fold2.predictions.csv")
    _write_predictions(fold1_predictions, fold1_val, fold1_report_probs)
    _write_predictions(fold2_predictions, fold2_val, fold2_report_probs)

    market = fold2_metrics["bookmaker_open"]
    final_ensemble = fold2_metrics["ensemble_calibrated"]
    improvement = {
        "accuracy_delta": round(final_ensemble["accuracy"] - market["accuracy"], 6),
        "log_loss_delta": round(final_ensemble["log_loss"] - market["log_loss"], 6),
        "brier_delta": round(
            final_ensemble["brier_multiclass"] - market["brier_multiclass"],
            6,
        ),
        "beats_market_log_loss": bool(
            final_ensemble["log_loss"] < market["log_loss"]
        ),
        "beats_market_brier": bool(
            final_ensemble["brier_multiclass"] < market["brier_multiclass"]
        ),
    }

    report = {
        "version": "0.4.0-phase4-ensemble-calibration",
        "status": "success",
        "holdout_policy": (
            "The 2025/26 final holdout is not accepted as an input and is never read. "
            "Fold 1 (2023/24) tunes blend weights and temperature. "
            "Fold 2 (2024/25) is the honest Phase 4 meta-validation season."
        ),
        "ensemble_models": list(fold1_probs),
        "weights_fitted_on_fold1_only": weights,
        "temperature_fitted_on_fold1_only": temperature,
        "fold1_optimizer_log_loss": round(float(fold1_optimized_loss), 6),
        "fold1_calibrated_log_loss": round(float(fold1_calibrated_loss), 6),
        "fold1": {
            "train_rows": len(fold1_train),
            "validation_rows": len(fold1_val),
            "validation_seasons": sorted(set(row["season"] for row in fold1_val)),
            "metrics": fold1_metrics,
            "model_details": fold1_details,
            "predictions": str(fold1_predictions),
        },
        "fold2": {
            "train_rows": len(fold2_train),
            "validation_rows": len(fold2_val),
            "validation_seasons": sorted(set(row["season"] for row in fold2_val)),
            "metrics": fold2_metrics,
            "model_details": fold2_details,
            "predictions": str(fold2_predictions),
        },
        "fold2_vs_bookmaker_open": improvement,
        "final_holdout_touched": False,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "status": "success",
        "fold1_train_rows": len(fold1_train),
        "fold1_validation_rows": len(fold1_val),
        "fold2_train_rows": len(fold2_train),
        "fold2_validation_rows": len(fold2_val),
        "ensemble_weights": weights,
        "temperature": temperature,
        "fold2_metrics": fold2_metrics,
        "fold2_vs_bookmaker_open": improvement,
        "report": str(output),
        "final_holdout_touched": False,
    }
