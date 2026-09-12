# Phase 3 — Baseline Models

Status: implementation ready for chronological validation.

Models:
- Majority/class-prior baseline
- Average bookmaker opening-price baseline
- Average bookmaker closing-price benchmark
- Three-way Elo baseline
- Poisson goals baseline
- Multinomial logistic regression

Evaluation:
- Accuracy
- Multiclass log loss
- Multiclass Brier score

Leakage / holdout policy:
- Train and validation files are provided explicitly.
- The command never discovers or opens the 2025/26 holdout automatically.
- Production logistic features exclude closing-market and market-movement fields.
- Closing bookmaker prices are reported only as a benchmark.
- Poisson excludes all bookmaker fields.

Recommended first run:
Train 2022/23 + 2023/24, validate 2024/25.
Do not score 2025/26 until model selection/calibration decisions are frozen.
