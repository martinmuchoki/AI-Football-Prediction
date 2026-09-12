# AI Football Prediction — Phase 1 Source

Version: **v0.1.4-football-data-history**

Phase 1 delivers the data foundation for the AI Football Prediction platform:

- SQLAlchemy database schema
- API-Football v3 connector (secondary/historical)
- Live Score API connector (primary current/live)
- Fixture collector
- Result refresher
- Validation / normalization layer
- Idempotent upserts
- FastAPI health/status API
- CLI commands
- Scheduler entry point
- PostgreSQL Docker configuration
- Pytest test suite

## Important

This phase **does not make predictions yet**. It creates the verified data pipeline that later model phases will depend on.

The API key is never stored in source code. Put it only in `.env`.

## 1. Windows setup

Open PowerShell in this folder:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
Copy-Item .env.example .env
notepad .env
```

Set:

```env
AF_API_KEY=YOUR_API_FOOTBALL_KEY
```

For quick local development, leave the SQLite `DATABASE_URL` in `.env`.

## 2. Initialize the database

```powershell
python -m app.cli init-db
```

## 3. Sync fixtures

Example: Premier League, 2026/27 season:

```powershell
python -m app.cli sync-fixtures --league 39 --season 2026 --from 2026-09-01 --to 2026-09-30
```

API-Football represents a season by its starting year.

## 4. Refresh results

```powershell
python -m app.cli refresh-results
```

Only unfinished fixtures that have already kicked off (within the configured lookback) are selected. Provider fixture IDs are batched to reduce API use.

## 5. Run tests

```powershell
pytest -q
```

## 6. Run the FastAPI service

```powershell
uvicorn app.main:app --reload
```

Then open:

- `http://127.0.0.1:8000/health`
- `http://127.0.0.1:8000/status`
- `http://127.0.0.1:8000/fixtures?limit=50`

## 7. Optional PostgreSQL

```powershell
docker compose up -d postgres
```

Then set:

```env
DATABASE_URL=postgresql+psycopg://football:football_dev_password@localhost:5432/football_ai
```

and run:

```powershell
python -m app.cli init-db
```

## 8. Scheduler

Set tracked competitions in `.env`, for example:

```env
TRACKED_LEAGUES=39:2026,140:2026,78:2026,135:2026,61:2026
```

Then:

```powershell
python -m app.scheduler
```

Default behavior:
- fixture windows sync every 6 hours
- unfinished results refresh every 60 minutes
- calls are batched and scoped to conserve quota

For the API-Football free plan, keep tracked leagues and intervals conservative.

## Database entities

- `league_seasons`
- `teams`
- `fixtures`
- `sync_runs`

All provider fixture IDs are unique. Re-running the same collection updates the existing row instead of duplicating it.

## Phase 1 acceptance criteria

- [x] Source project created
- [x] Database schema created
- [x] API-Football connector implemented
- [x] Fixture collection implemented
- [x] Result refresh implemented
- [x] Validation layer implemented
- [x] Idempotent persistence implemented
- [x] Tests written and passing locally
- [x] API key isolated from source
- [x] PostgreSQL-ready configuration included
- [x] Scheduler included

## Live Score API — current 2026/27 feed

Put the **newly generated** Live Score API credentials in `.env`:

```env
LS_API_KEY=...
LS_API_SECRET=...
LS_BASE_URL=https://livescore-api.com/api-client
```

Never commit or share `.env`.

English Premier League current competition ID is `2`. Import currently available 2026/27 fixtures:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-live-fixtures --competition 2 --season 2026
```

Import finished EPL matches for a date window:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-live-history `
  --competition 2 `
  --season 2026 `
  --from 2026-08-01 `
  --to 2026-09-09
```

The Live Score API documents fixture and history times in UTC. Fixture IDs are preserved so a later history result updates the same database fixture when the provider supplies `fixture_id`.

Pre-match/live odds remain preserved inside `raw_json` in Phase 1. Phase 2 will promote the required odds/statistical signals into dedicated feature tables.


## v0.1.2 — resilient Live Score sync

The Live Score commands now auto-paginate by default and preserve already committed pages if a later page fails.
The HTTP client separates connect/read/write/pool timeouts, retries 429 and 5xx responses, honors `Retry-After` when possible, and never includes credential-bearing request URLs in raised error messages.

Recommended `.env` values:

```env
LS_CONNECT_TIMEOUT_SECONDS=10
LS_READ_TIMEOUT_SECONDS=30
LS_WRITE_TIMEOUT_SECONDS=10
LS_POOL_TIMEOUT_SECONDS=10
LS_MAX_RETRIES=4
LS_RETRY_BASE_SECONDS=1
LS_RETRY_MAX_SECONDS=8
LS_MAX_PAGES=50
```

Auto-paginated current EPL fixtures:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-live-fixtures --competition 2 --season 2026
```

Auto-paginated finished EPL history:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-live-history `
  --competition 2 `
  --season 2026 `
  --from 2026-08-01 `
  --to 2026-09-09
```

If a persistent page failure remains after automatic retries, the summary includes `resume_page`. Resume from it with `--start-page N`.
Use `--page N` only when you intentionally want to fetch one exact page.


## v0.1.3 — History pagination fix

Live Score's fixture feed exposes `next_page`, while the history feed may return a full
30-match page without a `next_page` field. v0.1.3 therefore:

- keeps fixtures `next_page` driven;
- treats a full 30-match history page as a reason to probe the next page;
- stops history pagination on a short or empty page;
- guards against repeated-page loops;
- retains timeout/retry/rate-limit recovery from v0.1.2.

Example:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-live-history `
  --competition 2 `
  --season 2025 `
  --from 2025-08-01 `
  --to 2026-05-31 `
  --max-pages 20
```


## v0.1.4 — Complete historical provider

Live Score remains the primary current/live provider. Historical training seasons now use
Football-Data.co.uk CSV files because they provide complete archived Premier League seasons
plus match statistics and odds.

Import EPL 2025/26:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-football-data-history --season 2025 --league E0
```

Then repeat for older seasons:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-football-data-history --season 2024 --league E0
.\.venv\Scripts\python.exe -m app.cli sync-football-data-history --season 2023 --league E0
.\.venv\Scripts\python.exe -m app.cli sync-football-data-history --season 2022 --league E0
```

Historical rows use provider `football-data-csv`. The original CSV row is preserved in
`raw_json`, including available match stats and betting odds, for later Phase 2 feature
engineering. Live Score records are not deleted.


## v0.1.4.2 — Local CSV fallback

If Football-Data.co.uk returns 429/503, do not repeatedly retry the endpoint. Save the
season CSV locally, then import it without network access:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-football-data-history `
  --season 2025 `
  --league E0 `
  --file "D:\Mprojet\data\E0-2025-26.csv"
```

The local-file path uses the same validation, deterministic IDs and idempotent database
upsert logic as the network importer.

## v0.2.0 — Phase 2 Feature Engine

The Phase 2 builder creates a leakage-safe pre-match modelling dataset directly from the
validated `football-data-csv` historical database rows.

Audited stable columns across EPL 2022/23 through 2025/26 are used for bookmaker features.
Post-match match statistics (shots, shots on target, fouls, corners, cards and goals) are used
only through rolling history from matches that happened before the fixture being predicted.

Features include:

- rolling form over 3, 5 and 10 matches;
- rolling goals, goal difference, shots, shots on target, corners, fouls and cards;
- home-only / away-only five-match strength;
- rest days and matches played;
- season progress;
- leakage-safe pre-match Elo ratings;
- opening and closing 1X2 market probabilities (de-vigged), overround and movement;
- over/under 2.5 market probabilities;
- Asian-handicap line and de-vigged probabilities;
- 1X2 classification target and full-time goals.

Team rolling state and Elo reset at each season boundary. The current match is emitted before
its result/statistics are allowed to update any rolling state.

Build the four-season EPL dataset:

```powershell
New-Item -ItemType Directory -Force "D:\Mprojet\data\features" | Out-Null

.\.venv\Scripts\python.exe -m app.cli build-features `
  --provider football-data-csv `
  --from-season 2022 `
  --to-season 2025 `
  --output "D:\Mprojet\data\features\EPL-2022-2025-features.csv"
```

A JSON metadata sidecar is written next to the CSV and records the leakage policy, feature
count, Elo settings and season range.


## v0.3.0 — Phase 3 baseline models

Install/update dependencies:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Evaluate on the chronological validation season:

```powershell
.\.venv\Scripts\python.exe -m app.cli evaluate-baselines `
  --train "D:\Mprojet\data\features\EPL-train-2022-2023.csv" `
  --validation "D:\Mprojet\data\features\EPL-validation-2024.csv" `
  --output "D:\Mprojet\data\features\EPL-phase3-baselines-2024.json"
```

The 2025/26 holdout is intentionally not read by this command.


## v0.4.0 — Phase 4 ML ensemble + calibration

Phase 4 uses two chronological folds. Ensemble weights and temperature are fit only on
the first validation fold (2023/24) and then frozen before the honest second-fold
evaluation (2024/25).

```powershell
.\.venv\Scripts\python.exe -m app.cli evaluate-ensemble `
  --fold1-train "D:\Mprojet\data\features\EPL-train-2022.csv" `
  --fold1-validation "D:\Mprojet\data\features\EPL-validation-2023.csv" `
  --fold2-train "D:\Mprojet\data\features\EPL-train-2022-2023.csv" `
  --fold2-validation "D:\Mprojet\data\features\EPL-validation-2024.csv" `
  --output "D:\Mprojet\data\features\EPL-phase4-ensemble-2024.json"
```

The 2025/26 holdout remains untouched.


## v0.4.1 — Market-residual diagnostics

```powershell
.\.venv\Scripts\python.exe -m app.cli evaluate-market-residual `
  --fold1-train "D:\Mprojet\data\features\EPL-train-2022.csv" `
  --fold1-validation "D:\Mprojet\data\features\EPL-validation-2023.csv" `
  --fold2-train "D:\Mprojet\data\features\EPL-train-2022-2023.csv" `
  --fold2-validation "D:\Mprojet\data\features\EPL-validation-2024.csv" `
  --output "D:\Mprojet\data\features\EPL-phase4.1-market-residual-2024.json"
```

The 2025/26 final holdout is not accepted by the command and remains untouched.


## v0.7.0 — Phase 7 live prediction engine

Live Score upcoming fixtures can now be converted directly from `odds.pre` into de-vigged H/D/A probabilities and passed through the frozen 65% high-confidence selector. Predictions are stored once and locked before kickoff for later grading.

```powershell
.\.venv\Scripts\python.exe -m app.cli run-live-predictions --competition 2 --season 2026
.\.venv\Scripts\python.exe -m app.cli list-live-predictions --competition 2 --season 2026 --high-confidence-only
```


## v0.8.0 — Phase 8 results & accuracy

Grade finished predictions:

```powershell
.\.venv\Scripts\python.exe -m app.cli grade-live-predictions --competition 2 --season 2026
```

Show high-confidence live performance:

```powershell
.\.venv\Scripts\python.exe -m app.cli live-performance --competition 2 --season 2026 --high-confidence-only
```

API endpoints:
- `/predictions/live`
- `/predictions/performance`


## v0.9.0 — Phase 9 social graphics & video

Generate current HIGH_CONFIDENCE social content:

```powershell
.\.venv\Scripts\python.exe -m app.cli generate-social-content `
  --competition 2 `
  --season 2026 `
  --output-dir "D:\Mprojet\data\social\EPL-2026"
```

Output:
- 1080x1080 PNG cards
- caption TXT files
- manifest JSON
- static MP4 compilation when FFmpeg is available

## v0.10.0 — Phase 10A multi-source collector + MDRN SportsQ API

Phase 10A introduces a source registry, provenance/conflict resolution, a respectful public-web collector foundation, and a versioned API owned by this project.

Initialize and seed sources:

```powershell
.\.venv\Scripts\python.exe -m app.cli init-db
.\.venv\Scripts\python.exe -m app.cli seed-data-sources
.\.venv\Scripts\python.exe -m app.cli list-data-sources
```

Register a reviewed website source:

```powershell
.\.venv\Scripts\python.exe -m app.cli add-web-source `
  --slug official-example `
  --name "Official Example" `
  --base-url "https://example.com" `
  --priority 5 `
  --official
```

Public collection is opt-in. Add approved domains to `.env`:

```text
PUBLIC_WEB_ALLOWED_DOMAINS=example.com
```

Then collect a page:

```powershell
.\.venv\Scripts\python.exe -m app.cli collect-public-url `
  --source official-example `
  --url "https://example.com/fixtures"
```

Run our API locally:

```powershell
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Open `/docs` for the interactive FastAPI documentation. Versioned application endpoints live under `/api/v1`.


## v0.10.1 — Phase 10A.1 odds history + provenance

Capture the current pre-match 1X2 market state:

```powershell
.\.venv\Scripts\python.exe -m app.cli capture-odds-snapshots `
  --competition 2 `
  --season 2026
```

List stored odds history:

```powershell
.\.venv\Scripts\python.exe -m app.cli list-odds-history `
  --competition 2 `
  --season 2026 `
  --limit 50
```

Compare opening vs latest stored state for one fixture:

```powershell
.\.venv\Scripts\python.exe -m app.cli odds-movement --fixture-id 1877286
```

Own API:
- `GET /api/v1/odds/history`
- `GET /api/v1/odds/movement`


## v0.10.2 — Phase 10A.2 automatic market refresh + source health

One-shot source health check:

```powershell
.\.venv\Scripts\python.exe -m app.cli check-source-health
```

Refresh current Live Score fixture payloads, capture changed odds, and lock only new predictions:

```powershell
.\.venv\Scripts\python.exe -m app.cli refresh-live-market --competition 2 --season 2026
```

Run the continuous local scheduler (default 60-minute market and health cadence):

```powershell
.\.venv\Scripts\python.exe -m app.market_scheduler
```

Own API adds:
- `GET /api/v1/market/status`

The scheduler does not modify existing locked predictions and does not read the 2025/26 final historical holdout.


## v0.10.3 — Phase 10A.3 web queue + resilient health

A public/official website is still never collected until it is explicitly registered **and**
its domain appears in `PUBLIC_WEB_ALLOWED_DOMAINS`.

Register a source:

```powershell
.\.venv\Scripts\python.exe -m app.cli add-web-source `
  --slug official-source `
  --name "Official Source" `
  --base-url "https://example.com/" `
  --priority 40 `
  --official
```

Add a scheduled URL:

```powershell
.\.venv\Scripts\python.exe -m app.cli add-web-target `
  --source official-source `
  --url "https://example.com/fixtures" `
  --interval-minutes 60
```

Then, only after the domain has been reviewed and approved, add it to `.env`:

```text
PUBLIC_WEB_ALLOWED_DOMAINS=example.com
```

Collect due targets:

```powershell
.\.venv\Scripts\python.exe -m app.cli collect-web-targets
```

The long-running scheduler now also runs due public-web targets:

```powershell
.\.venv\Scripts\python.exe -m app.market_scheduler
```

Transient HTTP 429/5xx and network errors are `DEGRADED` until the configured consecutive
failure threshold is reached. This prevents one temporary 503 from falsely declaring a source
dead.


## v0.10.4 — Phase 10A.4 source compliance + OpenFootball CC0

Policy commands:

```powershell
.\.venv\Scripts\python.exe -m app.cli seed-source-policies
.\.venv\Scripts\python.exe -m app.cli list-source-policies
```

Independent EPL cross-check:

```powershell
.\.venv\Scripts\python.exe -m app.cli sync-openfootball-epl `
  --season-label 2026-27 `
  --competition 2 `
  --season 2026
```

OpenFootball is used as a cross-check/provenance source, not as an automatic
replacement for the existing Live Score fixture authority.

API:
- `GET /api/v1/sources/policies`


## v0.10.5 — Phase 10A.5 canonical reconciliation + quality gate

Fetch OpenFootball and persist the independent comparison:

```powershell
.\.venv\Scripts\python.exe -m app.cli reconcile-openfootball-epl `
  --season-label 2026-27 `
  --competition 2 `
  --season 2026
```

Read persisted quality state without hitting the network:

```powershell
.\.venv\Scripts\python.exe -m app.cli reconciliation-summary --competition 2 --season 2026
.\.venv\Scripts\python.exe -m app.cli reconciliation-issues --competition 2 --season 2026
```

Own API:
- `GET /api/v1/reconciliation/summary`
- `GET /api/v1/reconciliation/issues`

Quality gate semantics:
- `PASS`: complete cross-source agreement, with no conflicts or lag.
- `WARN`: missing fixture or finished-result source lag, but no direct contradiction.
- `FAIL`: direct date or final-score conflict between the two sources.
