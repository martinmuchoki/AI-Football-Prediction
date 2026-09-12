# Phase 10A.1 — Odds History + Provenance

- Capture distinct pre-match 1X2 odds snapshots for upcoming Live Score fixtures.
- Deduplicate unchanged prices by SHA-256 market hash.
- Preserve captured_at and kickoff_utc timestamps.
- Store de-vigged H/D/A probabilities and bookmaker overround.
- Record source observations for raw odds and fair probabilities.
- Provide opening-to-latest probability movement summary.
- Add /api/v1/odds/history and /api/v1/odds/movement.
- Locked predictions remain immutable.
- 2025/26 historical holdout is not read.
