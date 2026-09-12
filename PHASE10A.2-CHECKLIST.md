# Phase 10A.2 — Automatic Market Refresh + Source Health

- [x] Credential-safe health checks for registered providers.
- [x] CONFIG_REQUIRED is distinct from FAILED.
- [x] Dynamic STALE state for old successful checks.
- [x] Live Score fixture refresh before odds snapshot capture.
- [x] Changed odds create new snapshots; unchanged odds remain deduplicated.
- [x] New predictions may be locked; existing predictions are never rewritten.
- [x] Standalone APScheduler market scheduler.
- [x] `/api/v1/market/status` operational endpoint.
- [x] FastAPI startup migrated from deprecated `on_event` to lifespan.
- [x] Public website collection remains opt-in by allowlisted domain.
- [x] 2025/26 historical holdout is not read.
