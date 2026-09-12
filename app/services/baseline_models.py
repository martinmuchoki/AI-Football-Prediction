from __future__ import annotations

import csv
import json
import math
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

import numpy as np
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression, PoissonRegressor
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


CLASS_NAMES = ("H", "D", "A")
ID_COLUMNS = {
    "fixture_id",
    "provider_fixture_id",
    "season",
    "kickoff_utc",
    "home_team",
    "away_team",
}
TARGET_COLUMNS = {
    "target_result",
    "target_class",
    "target_home_goals",
    "target_away_goals",
}


def _read_csv(path: str | Path) -> tuple[list[dict[str, str]], list[str]]:
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists():
        raise FileNotFoundError(f"Dataset not found: {file_path}")
    with file_path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        rows = list(reader)
        fields = list(reader.fieldnames or [])
    if not rows:
        raise ValueError(f"Dataset is empty: {file_path}")
    return rows, fields


def _float(value: Any) -> float:
    text = "" if value is None else str(value).strip()
    if not text:
        return float("nan")
    try:
        number = float(text)
    except (TypeError, ValueError):
        return float("nan")
    return number if math.isfinite(number) else float("nan")


def _labels(rows: list[dict[str, str]]) -> np.ndarray:
    labels = np.asarray([int(row["target_class"]) for row in rows], dtype=int)
    unknown = sorted(set(labels.tolist()) - {0, 1, 2})
    if unknown:
        raise ValueError(f"Unexpected target_class values: {unknown}")
    return labels


def _goal_target(rows: list[dict[str, str]], key: str) -> np.ndarray:
    values = np.asarray([_float(row.get(key)) for row in rows], dtype=float)
    if np.isnan(values).any():
        raise ValueError(f"{key} contains missing values")
    if (values < 0).any():
        raise ValueError(f"{key} contains negative values")
    return values


def _normalize_probability_rows(probs: np.ndarray) -> np.ndarray:
    values = np.asarray(probs, dtype=float)
    if values.ndim != 2 or values.shape[1] != 3:
        raise ValueError("Probability array must have shape (n, 3)")
    values = np.clip(values, 1e-12, None)
    totals = values.sum(axis=1, keepdims=True)
    return values / totals


def accuracy_score_3way(y_true: np.ndarray, probs: np.ndarray) -> float:
    pred = np.asarray(probs).argmax(axis=1)
    return float(np.mean(pred == y_true))


def log_loss_3way(y_true: np.ndarray, probs: np.ndarray) -> float:
    p = _normalize_probability_rows(probs)
    selected = p[np.arange(len(y_true)), y_true]
    return float(-np.mean(np.log(np.clip(selected, 1e-15, 1.0))))


def brier_score_3way(y_true: np.ndarray, probs: np.ndarray) -> float:
    """Multiclass Brier: mean sum of squared class-probability errors."""
    p = _normalize_probability_rows(probs)
    target = np.zeros_like(p)
    target[np.arange(len(y_true)), y_true] = 1.0
    return float(np.mean(np.sum((p - target) ** 2, axis=1)))


def _metrics(y_true: np.ndarray, probs: np.ndarray) -> dict[str, float]:
    p = _normalize_probability_rows(probs)
    return {
        "accuracy": round(accuracy_score_3way(y_true, p), 6),
        "log_loss": round(log_loss_3way(y_true, p), 6),
        "brier_multiclass": round(brier_score_3way(y_true, p), 6),
    }


def _class_prior(y_train: np.ndarray) -> np.ndarray:
    counts = np.bincount(y_train, minlength=3).astype(float)
    if counts.sum() <= 0:
        raise ValueError("Training labels are empty")
    return counts / counts.sum()


def _majority_probs(y_train: np.ndarray, count: int) -> np.ndarray:
    prior = _class_prior(y_train)
    return np.tile(prior, (count, 1))


def _market_probs(
    rows: list[dict[str, str]],
    *,
    prefix: str,
    fallback: np.ndarray,
) -> np.ndarray:
    keys = (
        f"market_{prefix}_home_prob",
        f"market_{prefix}_draw_prob",
        f"market_{prefix}_away_prob",
    )
    output = []
    for row in rows:
        values = np.asarray([_float(row.get(k)) for k in keys], dtype=float)
        if np.isnan(values).any() or (values <= 0).any():
            values = fallback.copy()
        output.append(values)
    return _normalize_probability_rows(np.asarray(output, dtype=float))


def _elo_probs(rows: list[dict[str, str]], draw_rate: float) -> np.ndarray:
    output = []
    for row in rows:
        home_expected = _float(row.get("elo_home_expected"))
        elo_diff = _float(row.get("elo_diff_pre"))
        if math.isnan(home_expected):
            home_expected = 0.5
        if math.isnan(elo_diff):
            elo_diff = 0.0

        # Draw probability falls as the teams become more mismatched.
        p_draw = draw_rate * math.exp(-abs(elo_diff) / 400.0)
        p_draw = min(max(p_draw, 0.08), 0.38)
        remaining = 1.0 - p_draw
        p_home = remaining * min(max(home_expected, 0.02), 0.98)
        p_away = remaining - p_home
        output.append((p_home, p_draw, p_away))
    return _normalize_probability_rows(np.asarray(output, dtype=float))


def production_feature_columns(fields: Iterable[str]) -> list[str]:
    """
    Deployment-oriented feature set.

    Closing odds and odds-movement fields are intentionally excluded because
    the live prediction may be published before a true closing market exists.
    Targets and identifiers are always excluded.
    """
    result: list[str] = []
    for name in fields:
        if name in ID_COLUMNS or name in TARGET_COLUMNS:
            continue
        lower = name.lower()
        if "close" in lower or lower.endswith("_prob_move") or "_prob_move" in lower:
            continue
        result.append(name)
    return result


def poisson_feature_columns(fields: Iterable[str]) -> list[str]:
    """
    Team/statistical inputs only. All bookmaker fields are excluded so the
    Poisson baseline remains a football-strength model rather than a market model.
    """
    result = []
    for name in production_feature_columns(fields):
        if name.startswith("market_"):
            continue
        result.append(name)
    return result


def _matrix(rows: list[dict[str, str]], columns: list[str]) -> np.ndarray:
    return np.asarray(
        [[_float(row.get(column)) for column in columns] for row in rows],
        dtype=float,
    )


def _poisson_outcome_probs(home_lambda: np.ndarray, away_lambda: np.ndarray) -> np.ndarray:
    output: list[tuple[float, float, float]] = []
    max_goals = 10

    for h_lam, a_lam in zip(home_lambda, away_lambda):
        h_lam = float(np.clip(h_lam, 0.08, 6.0))
        a_lam = float(np.clip(a_lam, 0.08, 6.0))

        hp = [
            math.exp(-h_lam) * (h_lam ** k) / math.factorial(k)
            for k in range(max_goals + 1)
        ]
        ap = [
            math.exp(-a_lam) * (a_lam ** k) / math.factorial(k)
            for k in range(max_goals + 1)
        ]

        home = draw = away = 0.0
        for hg, p_h in enumerate(hp):
            for ag, p_a in enumerate(ap):
                value = p_h * p_a
                if hg > ag:
                    home += value
                elif hg == ag:
                    draw += value
                else:
                    away += value

        total = home + draw + away
        if total <= 0:
            output.append((1 / 3, 1 / 3, 1 / 3))
        else:
            output.append((home / total, draw / total, away / total))

    return _normalize_probability_rows(np.asarray(output, dtype=float))


def _fit_poisson(
    train_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    fields: list[str],
) -> tuple[np.ndarray, dict[str, Any]]:
    columns = poisson_feature_columns(fields)
    x_train = _matrix(train_rows, columns)
    x_val = _matrix(validation_rows, columns)
    y_home = _goal_target(train_rows, "target_home_goals")
    y_away = _goal_target(train_rows, "target_away_goals")

    def make_model() -> Pipeline:
        return Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
                ("scaler", StandardScaler()),
                ("model", PoissonRegressor(alpha=1.0, max_iter=1000)),
            ]
        )

    home_model = make_model()
    away_model = make_model()
    home_model.fit(x_train, y_home)
    away_model.fit(x_train, y_away)

    home_lambda = home_model.predict(x_val)
    away_lambda = away_model.predict(x_val)
    probs = _poisson_outcome_probs(home_lambda, away_lambda)
    return probs, {
        "feature_count": len(columns),
        "features": columns,
        "mean_validation_home_lambda": round(float(np.mean(home_lambda)), 6),
        "mean_validation_away_lambda": round(float(np.mean(away_lambda)), 6),
    }


def _fit_logistic(
    train_rows: list[dict[str, str]],
    validation_rows: list[dict[str, str]],
    fields: list[str],
    y_train: np.ndarray,
) -> tuple[np.ndarray, dict[str, Any]]:
    columns = production_feature_columns(fields)
    x_train = _matrix(train_rows, columns)
    x_val = _matrix(validation_rows, columns)

    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median", keep_empty_features=True)),
            ("scaler", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    C=1.0,
                    solver="lbfgs",
                    max_iter=2000,
                    random_state=42,
                ),
            ),
        ]
    )
    model.fit(x_train, y_train)
    probs = model.predict_proba(x_val)

    # sklearn orders probability columns by estimator.classes_.
    classes = list(model.named_steps["model"].classes_)
    ordered = np.zeros((len(validation_rows), 3), dtype=float)
    for source_index, class_value in enumerate(classes):
        ordered[:, int(class_value)] = probs[:, source_index]

    return _normalize_probability_rows(ordered), {
        "feature_count": len(columns),
        "features": columns,
        "excludes_closing_market": True,
    }


def _prediction_rows(
    validation_rows: list[dict[str, str]],
    model_probs: dict[str, np.ndarray],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for index, source in enumerate(validation_rows):
        item: dict[str, Any] = {
            "fixture_id": source.get("fixture_id", ""),
            "season": source.get("season", ""),
            "kickoff_utc": source.get("kickoff_utc", ""),
            "home_team": source.get("home_team", ""),
            "away_team": source.get("away_team", ""),
            "target_result": source.get("target_result", ""),
            "target_class": source.get("target_class", ""),
        }
        for model_name, probs in model_probs.items():
            p = probs[index]
            item[f"{model_name}_p_home"] = round(float(p[0]), 8)
            item[f"{model_name}_p_draw"] = round(float(p[1]), 8)
            item[f"{model_name}_p_away"] = round(float(p[2]), 8)
            item[f"{model_name}_prediction"] = CLASS_NAMES[int(np.argmax(p))]
        rows.append(item)
    return rows


def evaluate_baselines(
    *,
    train_path: str,
    validation_path: str,
    output_path: str,
) -> dict[str, Any]:
    train_rows, train_fields = _read_csv(train_path)
    validation_rows, validation_fields = _read_csv(validation_path)

    if train_fields != validation_fields:
        raise ValueError("Training and validation CSV schemas do not match")

    y_train = _labels(train_rows)
    y_val = _labels(validation_rows)
    if len(set(y_train.tolist())) < 3:
        raise ValueError("Training data must contain all three result classes")

    prior = _class_prior(y_train)
    draw_rate = float(prior[1])

    model_probs: dict[str, np.ndarray] = {}
    model_details: dict[str, Any] = {}

    model_probs["majority"] = _majority_probs(y_train, len(validation_rows))
    model_details["majority"] = {
        "training_class_prior": {
            "H": round(float(prior[0]), 6),
            "D": round(float(prior[1]), 6),
            "A": round(float(prior[2]), 6),
        }
    }

    model_probs["bookmaker_open"] = _market_probs(
        validation_rows,
        prefix="avg_open",
        fallback=prior,
    )
    model_details["bookmaker_open"] = {
        "source": "market_avg_open_*_prob",
        "deployment_role": "pre-match market benchmark",
    }

    model_probs["bookmaker_close"] = _market_probs(
        validation_rows,
        prefix="avg_close",
        fallback=prior,
    )
    model_details["bookmaker_close"] = {
        "source": "market_avg_close_*_prob",
        "deployment_role": "closing-market benchmark only; not used by production logistic model",
    }

    model_probs["elo"] = _elo_probs(validation_rows, draw_rate)
    model_details["elo"] = {
        "training_draw_rate": round(draw_rate, 6),
        "draw_model": "training draw rate exponentially reduced by absolute pre-match Elo gap",
    }

    poisson_probs, poisson_details = _fit_poisson(
        train_rows,
        validation_rows,
        train_fields,
    )
    model_probs["poisson"] = poisson_probs
    model_details["poisson"] = poisson_details

    logistic_probs, logistic_details = _fit_logistic(
        train_rows,
        validation_rows,
        train_fields,
        y_train,
    )
    model_probs["logistic"] = logistic_probs
    model_details["logistic"] = logistic_details

    metrics = {
        name: _metrics(y_val, probs)
        for name, probs in model_probs.items()
    }

    ranked = sorted(
        metrics,
        key=lambda name: (
            metrics[name]["log_loss"],
            metrics[name]["brier_multiclass"],
            -metrics[name]["accuracy"],
        ),
    )

    output = Path(output_path).expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    predictions_path = output.with_suffix(output.suffix + ".predictions.csv")

    pred_rows = _prediction_rows(validation_rows, model_probs)
    pred_fields = list(pred_rows[0].keys())
    with predictions_path.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=pred_fields)
        writer.writeheader()
        writer.writerows(pred_rows)

    report = {
        "version": "0.3.0-phase3-baseline-models",
        "status": "success",
        "evaluation_policy": (
            "Chronological validation only. The 2025/26 final holdout is intentionally not read or scored by this command."
        ),
        "train": {
            "path": str(Path(train_path).expanduser().resolve()),
            "rows": len(train_rows),
            "seasons": dict(sorted(Counter(row["season"] for row in train_rows).items())),
        },
        "validation": {
            "path": str(Path(validation_path).expanduser().resolve()),
            "rows": len(validation_rows),
            "seasons": dict(sorted(Counter(row["season"] for row in validation_rows).items())),
            "target_distribution": {
                CLASS_NAMES[index]: int(np.sum(y_val == index))
                for index in range(3)
            },
        },
        "metrics": metrics,
        "ranking_by_log_loss": ranked,
        "model_details": model_details,
        "predictions": str(predictions_path),
    }
    output.write_text(json.dumps(report, indent=2, sort_keys=True), encoding="utf-8")

    return {
        "status": "success",
        "train_rows": len(train_rows),
        "validation_rows": len(validation_rows),
        "models": list(model_probs),
        "best_by_log_loss": ranked[0],
        "metrics": metrics,
        "report": str(output),
        "predictions": str(predictions_path),
        "final_holdout_touched": False,
    }
