# Phase 10A.3 — Web Target Queue + Resilient Source Health

- Public/official web targets are stored in a scheduler queue.
- Collection remains opt-in through `PUBLIC_WEB_ALLOWED_DOMAINS`.
- Target host must match its registered web source.
- `robots.txt`, public-IP/SSRF protections and body-size limits remain enforced.
- No login, CAPTCHA, paywall or anti-bot bypass exists.
- Automatic scheduler now checks due web targets.
- Transient 429/5xx/network provider errors become `DEGRADED` before `FAILED`.
- Default hard-failure threshold is 3 consecutive transient failures.
- Provider success resets the failure counter.
- New own-API endpoint: `GET /api/v1/web/targets`.
- Locked predictions remain immutable.
- 2025/26 final historical holdout remains untouched.
