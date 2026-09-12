# Phase 10A — Multi-Source Collector + Our Own API Foundation

## Implemented

- Multi-source registry with explicit priority ranking.
- Default sources seeded: Live Score API, Football-Data.co.uk CSV, API-Football.
- Source health fields: UNKNOWN / OK / FAILED plus timestamps and failure count.
- Provenance observations for entity/field/value/source tracking.
- Conflict resolver chooses the highest-priority enabled source and exposes all conflicting candidates.
- Public-web collector foundation with:
  - explicit domain allowlist,
  - http/https only,
  - DNS/private-network SSRF protection,
  - robots.txt check,
  - no login/CAPTCHA/paywall/anti-bot bypass logic,
  - response-size ceiling,
  - SHA-256 content fingerprint,
  - snapshot deduplication,
  - source health updates.
- Own versioned API under `/api/v1`.
- Optional `X-API-Key` protection controlled by environment settings.

## API v1

- `GET /api/v1/health`
- `GET /api/v1/sources`
- `GET /api/v1/sources/health`
- `GET /api/v1/fixtures/upcoming`
- `GET /api/v1/predictions/high-confidence`
- `GET /api/v1/predictions/performance`
- `GET /api/v1/provenance/resolve`

## CLI

- `seed-data-sources`
- `list-data-sources`
- `add-web-source`
- `collect-public-url`
- `resolve-source-field`

## Safety / data policy

Public-web collection is disabled until `PUBLIC_WEB_ALLOWED_DOMAINS` is explicitly configured. Website-specific parsers should only be added after the source has been reviewed for permitted automated access and the parser has dedicated tests.

The 2025/26 historical holdout is not used by Phase 10A.
