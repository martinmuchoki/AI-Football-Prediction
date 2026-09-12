# MDRN SportsQ — Stage 4 Resource Folder
## Source + Rights Infrastructure

Purpose: provide the non-production resource layer for Stage 4 before application code is changed.

Current protected application baseline:
`1.0.1-stage3-gui-polish`

This folder is designed to be copied into the project as:

`resources/stage4/`

It contains:
- official football source seed register;
- reusable media source seed register;
- rights/licence policy;
- source trust policy;
- JSON schemas for sources, media assets, and rights verification;
- Stage 4 completion gate;
- integration notes.

## Core rule

No third-party media may enter MDRN SportsQ content generation unless:

`REUSE_VERIFIED = YES`

A public URL or an old match is **not** by itself evidence of permission to republish.

## Media fallback order

1. Historical Team A vs Team B reusable footage.
2. Reusable Team A + reusable Team B footage.
3. Generic reusable football footage.

## Trust classes

- OFFICIAL: competition, governing body, or club-owned source.
- TRUSTED: established provider with clear provenance.
- OPEN_MEDIA: repository or provider containing content with reusable licences.
- UNVERIFIED: source not yet validated for production use.

## Important

This is a resource/configuration pack only. It does not modify the prediction engine,
LivePrediction locks, grading, holdout data, or existing Stage 3 services.
