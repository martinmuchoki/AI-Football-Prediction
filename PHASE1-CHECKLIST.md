# Phase 1 Acceptance Checklist

## Source / security
- [x] API key comes from environment only.
- [x] `.env` ignored by Git.
- [x] No live credentials included.
- [x] Provider calls have timeout and retry handling.
- [x] Provider errors are surfaced.

## Data collection
- [x] Fixture range collection.
- [x] Provider fixture ID retained as immutable external key.
- [x] Result refresh for unfinished matches.
- [x] Result calls use up to 20 fixture IDs per request.
- [x] UTC normalization.

## Validation
- [x] Positive provider IDs.
- [x] Valid timezone-aware kickoff.
- [x] Distinct home/away teams.
- [x] Non-negative goals.
- [x] Finished games require final score.
- [x] 1X2 result derived internally from goals.

## Database
- [x] League-season uniqueness.
- [x] Team uniqueness.
- [x] Fixture uniqueness.
- [x] Idempotent upsert.
- [x] Raw provider snapshot preserved.
- [x] Sync-run audit table.
- [x] PostgreSQL DDL supplied.
- [x] SQLite development mode supported.

## Tests
- [x] Validation tests.
- [x] Connector test.
- [x] Idempotency test.
- [x] Result-update test.
- [x] No test requires a live API key.

## Deferred to later phases
- [ ] Historical dataset import.
- [ ] Feature engineering.
- [ ] Elo/Poisson/ML models.
- [ ] Prediction locking.
- [ ] Accuracy dashboard.
- [ ] Social publishing.

## Live Score API validation
- [x] Current 2026/27 EPL competition available.
- [x] Current fixture feed returns real fixtures.
- [x] Pre-match 1X2 odds present where available.
- [x] Live Score provider connector added.
- [x] Fixture/history normalization added.
- [x] Fixture-to-history ID linking supported.

## v0.1.2 resilience hardening
- [x] Automatic Live Score pagination.
- [x] Separate connect/read/write/pool timeouts.
- [x] Exponential retry/backoff for network errors, 429, and 5xx.
- [x] Retry-After support.
- [x] Credential-safe error messages.
- [x] Resume-page reporting after a persistent page failure.
- [x] Maximum-page safety ceiling.

## v0.1.3 history pagination
- [x] Full 30-record history page probes the next page even without next_page metadata.
- [x] Short/empty history page stops pagination.
- [x] Repeated-page guard prevents infinite loops.
