# Phase 10A.5 — Canonical Reconciliation + Data Quality Gate

- Persist Live Score vs OpenFootball fixture agreement.
- Match fixtures by conservative normalized home/away identity, not by date.
- Compare fixture date independently so postponements/reschedules surface as conflicts.
- Compare full-time score after Live Score marks the match finished.
- Track MATCH, CONFLICT, SOURCE_LAG, MISSING_PRIMARY, and MISSING_SECONDARY.
- Expose PASS / WARN / FAIL data-quality gate.
- Add reconciliation summary and issue API endpoints.
- Never overwrite provider fixtures from reconciliation.
- Never rewrite locked predictions.
- Keep 2025/26 final holdout sealed.
