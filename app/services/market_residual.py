from __future__ import annotations

import csv
import json
from pathlib import Path
from typing import Any

import numpy as np
from scipy.optimize import minimize

from app.services.baseline_models import (
    CLASS_NAMES,
    _labels,
    _metrics,
    _normalize_probability_rows,
    _read_csv,
)
from app.services.ensemble_models import _fit_fold_models


INDEPENDENT_MODELS = (
    "elo",
    "poisson",
    "reduced_logistic",
    "gradient_boosting",
)


def _softmax(scores: np.ndarray) -> np.ndarray:
    values = np.asarray(scores, dtype=float)
    values = values - np.max(values, axis=1, keepdims=True)
    exp = np.exp(values)
    return exp / np.sum(exp, axis=1, keepdims=True)


def _log_loss(y_true: np.ndarray, probs: np.ndarray) -> float:
    p = _normalize_probability_rows(probs)
    selected = p[np.arange(len(y_true)), y_true]
    return float(-np.mean(np.log(np.clip(selected, 1e-15, 1.0))))


def market_bias_probabilities(
    market_probs: np.ndarray,
    bias: np.ndarray,
) -> np.ndarray:
    p = _normalize_probability_rows(market_probs)
    b = np.asarray(bias, dtype=float)
    if b.shape != (3,):
        raise ValueError("bias must contain H, D and A values")
    b = b - np.mean(b)
    return _softmax(np.log(np.clip(p, 1e-15, 1.0)) + b[None, :])


def fit_market_bias(
    y_true: np.ndarray,
    market_probs: np.ndarray,
    *,
    l2: float = 0.25,
) -> tuple[np.ndarray, float]:
    def objective(raw: np.ndarray) -> float:
        bias = np.asarray([raw[0], raw[1], -(raw[0] + raw[1])], dtype=float)
        probs = market_bias_probabilities(market_probs, bias)
        return _log_loss(y_true, probs) + l2 * float(np.sum(bias * bias))

    result = minimize(
        objective,
        x0=np.zeros(2, dtype=float),
        method="L-BFGS-B",
        bounds=[(-0.40, 0.40), (-0.40, 0.40)],
    )
    raw = np.asarray(result.x, dtype=float)
    bias = np.asarray([raw[0], raw[1], -(raw[0] + raw[1])], dtype=float)
    return bias, _log_loss(y_true, market_bias_probabilities(market_probs, bias))


def log_pool_probabilities(
    market_probs: np.ndarray,
    other_probs: dict[str, np.ndarray],
    weights: dict[str, float],
) -> np.ndarray:
    market = _normalize_probability_rows(market_probs)
    scores = np.log(np.clip(market, 1e-15, 1.0))

    for name, probs in other_probs.items():
        alpha = float(weights.get(name, 0.0))
        model = _normalize_probability_rows(probs)
        scores += alpha * (
            np.log(np.clip(model, 1e-15, 1.0))
            - np.log(np.clip(market, 1e-15, 1.0))
        )

    return _softmax(scores)


def fit_residual_log_pool(
    y_true: np.ndarray,
    market_probs: np.ndarray,
    other_probs: dict[str, np.ndarray],
    *,
    l2: float = 0.20,
) -> tuple[dict[str, float], float]:
    names = list(other_probs)

    def objective(raw: np.ndarray) -> float:
        weights = {name: float(value) for name, value in zip(names, raw)}
        probs = log_pool_probabilities(market_probs, other_probs, weights)
        penalty = l2 * float(np.sum(np.asarray(raw) ** 2))
        return _log_loss(y_true, probs) + penalty

    result = minimize(
        objective,
        x0=np.zeros(len(names), dtype=float),
        method="L-BFGS-B",
        bounds=[(-0.20, 0.45)] * len(names),
    )

    weights = {
        name: round(float(value), 8)
        for name, value in zip(names, result.x)
    }
    unpenalized_loss = _log_loss(
        y_true,
        log_pool_probabilities(market_probs, other_probs, weights),
    )
    return weights, unpenalized_loss


def fit_single_model_shrink(
    y_true: np.ndarray,
    market_probs: np.ndarray,
    candidate_probs: np.ndarray,
) -> tuple[float, float]:
    best_alpha = 0.0
    best_loss = _log_loss(y_true, market_probs)
    other = {"candidate": candidate_probs}

    # Fine but deliberately narrow: the market remains the anchor.
    for alpha in np.linspace(0.0, 0.40, 81):
        probs = log_pool_probabilities(
            market_probs,
            other,
            {"candidate": float(alpha)},
        )
        loss = _log_loss(y_true, probs)
        if loss < best_loss:
            best_loss = loss
            best_alpha = float(alpha)

    return round(best_alpha, 4), float(best_loss)


def bootstrap_log_loss_delta(
    y_true: np.ndarray,
    candidate_probs: np.ndarray,
    market_probs: np.ndarray,
    *,
    samples: int = 2000,
    seed: int = 42,
) -> dict[str, float]:
    candidate = _normalize_probability_rows(candidate_probs)
    market = _normalize_probability_rows(market_probs)

    cand_losses = -np.log(
        np.clip(candidate[np.arange(len(y_true)), y_true], 1e-15, 1.0)
    )
    market_losses = -np.log(
        np.clip(market[np.arange(len(y_true)), y_true], 1e-15, 1.0)
    )
    per_match_delta = cand_losses - market_losses

    rng = np.random.default_rng(seed)
    n = len(y_true)
    means = np.empty(samples, dtype=float)
    for i in range(samples):
        index = rng.integers(0, n, size=n)
        means[i] = float(np.mean(per_match_delta[index]))

    return {
        "observed_delta": round(float(np.mean(per_match_delta)), 6),
        "ci95_low": round(float(np.quantile(means, 0.025)), 6),
        "ci95_high": round(float(np.quantile(means, 0.975)), 6),
        "probability_candidate_better": round(float(np.mean(means < 0.0)), 6),
    }


def _write_predictions(
    path: Path,
    rows: list[dict[str, str]],
    probabilities: dict[str, np.ndarray],
) -> None:
    output_rows = []
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
        for name, probs in probabilities.items():
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


def evaluate_market_residual(
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
        raise ValueError("All Phase 4.1 datasets must have identical schemas")

    y1 = _labels(fold1_val)
    y2 = _labels(fold2_val)

    f1_probs, _ = _fit_fold_models(fold1_train, fold1_val, fields1)
    f2_probs, _ = _fit_fold_models(fold2_train, fold2_val, fields1)

    market1 = f1_probs["bookmaker_open"]
    market2 = f2_probs["bookmaker_open"]

    # Candidate A: class-bias correction of the market itself.
    bias, bias_loss = fit_market_bias(y1, market1)
    f1_bias = market_bias_probabilities(market1, bias)
    f2_bias = market_bias_probabilities(market2, bias)

    # Candidate B: independent models only make bounded log-probability corrections.
    other1 = {name: f1_probs[name] for name in INDEPENDENT_MODELS}
    other2 = {name: f2_probs[name] for name in INDEPENDENT_MODELS}
    residual_weights, residual_loss = fit_residual_log_pool(y1, market1, other1)
    f1_residual = log_pool_probabilities(market1, other1, residual_weights)
    f2_residual = log_pool_probabilities(market2, other2, residual_weights)

    # Candidate C: one-model-at-a-time shrinkage diagnostics.
    single_model = {}
    f1_single = {}
    f2_single = {}
    for name in INDEPENDENT_MODELS:
        alpha, loss = fit_single_model_shrink(y1, market1, f1_probs[name])
        candidate_name = f"market_plus_{name}"
        single_model[candidate_name] = {
            "alpha": alpha,
            "fold1_log_loss": round(loss, 6),
        }
        f1_single[candidate_name] = log_pool_probabilities(
            market1, {"candidate": f1_probs[name]}, {"candidate": alpha}
        )
        f2_single[candidate_name] = log_pool_probabilities(
            market2, {"candidate": f2_probs[name]}, {"candidate": alpha}
        )

    f1_candidates = {
        "bookmaker_open": market1,
        "market_bias": f1_bias,
        "market_residual_log_pool": f1_residual,
        **f1_single,
    }
    f2_candidates = {
        "bookmaker_open": market2,
        "market_bias": f2_bias,
        "market_residual_log_pool": f2_residual,
        **f2_single,
    }

    f1_metrics = {name: _metrics(y1, p) for name, p in f1_candidates.items()}
    f2_metrics = {name: _metrics(y2, p) for name, p in f2_candidates.items()}

    # Select using Fold 1 ONLY. Require at least 0.001 log-loss improvement so
    # tiny numerical gains do not displace the market anchor.
    market_f1_loss = f1_metrics["bookmaker_open"]["log_loss"]
    eligible = []
    for name, metrics in f1_metrics.items():
        if name == "bookmaker_open":
            continue
        gain = market_f1_loss - metrics["log_loss"]
        if gain >= 0.001:
            eligible.append((metrics["log_loss"], name))

    selected = min(eligible)[1] if eligible else "bookmaker_open"

    bootstrap = bootstrap_log_loss_delta(
        y2,
        f2_candidates[selected],
        market2,
    )

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    f1_pred = output.with_suffix(output.suffix + ".fold1.predictions.csv")
    f2_pred = output.with_suffix(output.suffix + ".fold2.predictions.csv")
    _write_predictions(f1_pred, fold1_val, f1_candidates)
    _write_predictions(f2_pred, fold2_val, f2_candidates)

    selected_f2 = f2_metrics[selected]
    market_f2 = f2_metrics["bookmaker_open"]
    comparison = {
        "accuracy_delta": round(
            selected_f2["accuracy"] - market_f2["accuracy"], 6
        ),
        "log_loss_delta": round(
            selected_f2["log_loss"] - market_f2["log_loss"], 6
        ),
        "brier_delta": round(
            selected_f2["brier_multiclass"] - market_f2["brier_multiclass"], 6
        ),
        "beats_market_log_loss": bool(
            selected_f2["log_loss"] < market_f2["log_loss"]
        ),
        "beats_market_brier": bool(
            selected_f2["brier_multiclass"] < market_f2["brier_multiclass"]
        ),
    }

    report = {
        "version": "0.4.1-market-residual-diagnostics",
        "status": "success",
        "method": (
            "Market-anchored residual modelling. Bookmaker opening probabilities "
            "remain the base prediction; independent models may only make bounded "
            "log-probability corrections."
        ),
        "selection_policy": (
            "All correction parameters and candidate selection use Fold 1 (2023/24) only. "
            "Fold 2 (2024/25) is evaluated after selection is frozen. "
            "The 2025/26 final holdout is never accepted or read."
        ),
        "fold1": {
            "train_rows": len(fold1_train),
            "validation_rows": len(fold1_val),
            "metrics": f1_metrics,
        },
        "fold2": {
            "train_rows": len(fold2_train),
            "validation_rows": len(fold2_val),
            "metrics": f2_metrics,
        },
        "market_bias": {
            "bias_H_D_A": [round(float(x), 8) for x in bias],
            "fold1_unpenalized_log_loss": round(float(bias_loss), 6),
            "l2": 0.25,
        },
        "residual_log_pool": {
            "weights": residual_weights,
            "fold1_unpenalized_log_loss": round(float(residual_loss), 6),
            "l2": 0.20,
            "weight_bounds": [-0.20, 0.45],
        },
        "single_model_shrinkage": single_model,
        "selected_on_fold1": selected,
        "fold2_selected_vs_market": comparison,
        "fold2_selected_log_loss_bootstrap": bootstrap,
        "fold1_predictions": str(f1_pred),
        "fold2_predictions": str(f2_pred),
        "final_holdout_touched": False,
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "status": "success",
        "selected_on_fold1": selected,
        "market_bias": [round(float(x), 6) for x in bias],
        "residual_weights": residual_weights,
        "fold1_metrics": f1_metrics,
        "fold2_metrics": f2_metrics,
        "fold2_selected_vs_market": comparison,
        "bootstrap": bootstrap,
        "report": str(output),
        "final_holdout_touched": False,
    }
