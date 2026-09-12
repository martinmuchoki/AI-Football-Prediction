# Integration Notes

Recommended application namespace for the coding stage:

- `app/services/source_registry.py`
- `app/services/media_rights.py`
- `app/services/media_source_search.py`
- Stage 4 API endpoints only after service tests pass.

Recommended persistent entities:
- SourceRecord
- MediaAsset
- RightsVerification

Do not add network downloading in the first Stage 4 patch.
First implement:
1. storage/model layer;
2. validation;
3. hard reuse gate;
4. seed import;
5. read-only API/status;
6. tests.

Only then add external search/download adapters in later Stage 9 work.

Recommended content-engine call contract:

`assert_media_reusable(asset)`

The function must fail closed. Missing, unknown, expired, or contradictory rights data must block the asset.
