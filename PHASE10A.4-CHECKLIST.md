# Phase 10A.4 — Source Compliance Gate + OpenFootball CC0 Cross-Check

- Add source_policies compliance/licensing table.
- Default unreviewed web sources are not approved for automated collection.
- Seed OpenFootball football.json as an explicitly approved CC0 source.
- Fetch OpenFootball via the public GitHub Contents API; no API key required.
- Store raw JSON snapshot hash and field-level provenance.
- Do not overwrite Live Score fixtures from OpenFootball.
- Cross-check OpenFootball against Live Score as an independent source.
- Expose source policies at GET /api/v1/sources/policies.
- Keep locked live predictions immutable.
- Keep 2025/26 historical final holdout sealed.
