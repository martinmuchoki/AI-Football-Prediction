# Phase 2 — Historical Dataset & Feature Engineering

## Completed in v0.2.0
- [x] 4 complete EPL seasons validated (2022/23-2025/26; 1,520 matches)
- [x] Cross-season schema audit completed
- [x] Stable match/stat columns selected
- [x] Stable bookmaker columns selected
- [x] Leakage-safe rolling feature engine
- [x] Rolling windows: 3 / 5 / 10
- [x] Home/away venue-specific rolling strength
- [x] Rest-day features
- [x] Pre-match Elo
- [x] De-vigged 1X2 probabilities and overround
- [x] Opening-to-closing market movement
- [x] O/U 2.5 and Asian-handicap market features
- [x] CSV + metadata export
- [x] Automated leakage regression tests

## Next
- [ ] Generate and audit the 1,520-row feature dataset on the user's database
- [ ] Missing-value / distribution audit
- [ ] Time-based train/validation/test split
- [ ] Baseline models (Phase 3)
