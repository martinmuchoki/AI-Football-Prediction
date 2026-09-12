# Phase 4.1 — Market Residual Diagnostics

Why this exists:
Phase 4 found that a free ensemble selected 100% bookmaker opening probabilities.
Temperature calibration improved Fold 1 but worsened Fold 2.

Phase 4.1 therefore treats the opening market as the anchor and only allows small,
regularized corrections.

Candidates:
- Market opening baseline
- Market class-bias correction
- Regularized residual log-probability pool using Elo, Poisson, reduced logistic,
  and gradient boosting
- One-model-at-a-time shrinkage diagnostics

Selection:
- Parameters and candidate choice are fitted on Fold 1 (2023/24) only.
- A correction must improve Fold 1 log loss by at least 0.001 to displace the market.
- The selected candidate is then frozen and evaluated on Fold 2 (2024/25).
- A bootstrap interval is reported for Fold 2 log-loss difference vs the market.

Holdout:
- 2025/26 remains sealed.
