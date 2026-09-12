# Phase 7 — Live Prediction Engine

- Reads Live Score `odds.pre` only.
- Filters by explicit Live Score competition ID and season.
- Converts decimal 1X2 odds to de-vigged fair probabilities.
- Applies frozen Phase 5 threshold 0.65.
- Stores both HIGH_CONFIDENCE and PASS decisions.
- First captured prediction is immutable for a fixture/policy version.
- Stores odds snapshot time and lock time before kickoff.
- Never uses `odds.live`.
- 2025/26 final historical holdout remains untouched.
