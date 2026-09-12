# Stage 4 Completion Gate

Do not advance to Stage 5 until all conditions below are verified.

## Source register
- [ ] Source records can be stored and retrieved.
- [ ] Source trust class is explicit.
- [ ] Official football sources are separated from reusable-media sources.
- [ ] Verification timestamp/status is stored.

## Rights register
- [ ] Every external media asset has a source URL.
- [ ] Licence / rights basis is recorded.
- [ ] Commercial-use status is explicit.
- [ ] Modification permission is explicit.
- [ ] Attribution requirement/text can be stored.
- [ ] Verification evidence URL can be stored.
- [ ] REUSE_VERIFIED is explicit and defaults to false.

## Enforcement
- [ ] Unverified media is blocked.
- [ ] NC media is blocked for commercial publishing.
- [ ] ND media is blocked when editing is required.
- [ ] Standard YouTube uploads are not treated as reusable.
- [ ] Official league/club videos are information sources only unless separately licensed.
- [ ] Exact historical matchup -> team-specific -> generic fallback is supported.

## Safety
- [ ] Prediction engine unchanged.
- [ ] LivePrediction rows/locks unchanged.
- [ ] Historical holdout unchanged.
- [ ] Existing Stage 2 intelligence unchanged.
- [ ] Existing Stage 3 content engine remains compatible.

## Verification
- [ ] Focused Stage 4 tests pass.
- [ ] Complete regression passes.
- [ ] Before/after immutable-state hash comparison passes.
