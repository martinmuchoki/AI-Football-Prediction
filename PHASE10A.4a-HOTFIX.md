# Phase 10A.4a — Regression Hotfix

This hotfix changes tests only.

Reason:
- Phase 10A.4 intentionally added a fourth default source: `openfootball-json`.
- Two older regression tests still hard-coded the previous count of three default sources.

Fix:
- Source API test now validates the exact four expected source slugs.
- Source registry idempotency test now expects four defaults and verifies `total_defaults == 4`.

No production model, database, collector, prediction, odds, or holdout logic is changed.
