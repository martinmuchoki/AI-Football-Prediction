# Phase 8 — Results & Accuracy Dashboard

- Grade locked predictions only after fixtures are finished.
- Preserve immutable pre-match probabilities and lock timestamp.
- Metrics: accuracy, multiclass log loss, multiclass Brier, average confidence.
- Separate all-locked and HIGH_CONFIDENCE performance.
- API: `/predictions/live` and `/predictions/performance`.
- 2025/26 historical holdout is not read.
