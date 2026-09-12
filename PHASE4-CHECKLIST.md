# Phase 4 — ML Ensemble + Calibration

## Design

Base production candidates:
- Average bookmaker opening probabilities (market anchor)
- Elo
- Poisson goals model
- Strongly regularized reduced-feature multinomial logistic regression
- Conservative histogram gradient boosting

Closing odds are NOT used in the Phase 4 production ensemble.

## Leakage-safe meta-validation

Fold 1:
- Train base models: 2022/23
- Predict: 2023/24
- Fit non-negative blend weights on 2023/24 predictions
- Fit one temperature-calibration scalar on 2023/24 predictions

Fold 2:
- Train base models: 2022/23 + 2023/24
- Predict: 2024/25
- Apply the already-frozen Fold 1 ensemble weights and temperature
- Measure honest Phase 4 meta-validation performance

Final holdout:
- 2025/26 remains sealed and is not accepted as a command input.

## Pass criteria

Primary:
- Lower log loss than bookmaker opening probabilities on Fold 2.

Secondary:
- Lower multiclass Brier score than bookmaker opening probabilities.
- Accuracy should not materially collapse.

If the ensemble fails to beat the opening market, keep the market anchor and use Phase 4
results to revise features/model complexity rather than touching the 2025/26 holdout.
