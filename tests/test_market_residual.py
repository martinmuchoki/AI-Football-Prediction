import csv
import json
from pathlib import Path

import numpy as np

from app.services.market_residual import (
    bootstrap_log_loss_delta,
    fit_residual_log_pool,
    log_pool_probabilities,
    market_bias_probabilities,
)


def test_log_pool_zero_weights_equals_market():
    market = np.asarray([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    other = {"x": np.asarray([[0.7, 0.2, 0.1], [0.1, 0.2, 0.7]])}
    result = log_pool_probabilities(market, other, {"x": 0.0})
    assert np.allclose(result, market)


def test_market_bias_probabilities_are_normalized():
    market = np.asarray([[0.5, 0.3, 0.2], [0.2, 0.3, 0.5]])
    result = market_bias_probabilities(market, np.asarray([0.1, -0.1, 0.0]))
    assert np.allclose(result.sum(axis=1), 1.0)
    assert np.all(result > 0)


def test_residual_fit_prefers_helpful_model():
    y = np.asarray([0, 1, 2] * 30, dtype=int)
    market = np.full((len(y), 3), 1 / 3)
    helpful = np.full((len(y), 3), 0.10)
    helpful[np.arange(len(y)), y] = 0.80
    weights, loss = fit_residual_log_pool(
        y,
        market,
        {"helpful": helpful},
        l2=0.01,
    )
    assert weights["helpful"] > 0
    assert loss < 1.0


def test_bootstrap_reports_negative_delta_for_better_candidate():
    y = np.asarray([0, 1, 2] * 20, dtype=int)
    market = np.full((len(y), 3), 1 / 3)
    candidate = np.full((len(y), 3), 0.10)
    candidate[np.arange(len(y)), y] = 0.80
    result = bootstrap_log_loss_delta(
        y,
        candidate,
        market,
        samples=300,
        seed=1,
    )
    assert result["observed_delta"] < 0
    assert result["probability_candidate_better"] > 0.95
