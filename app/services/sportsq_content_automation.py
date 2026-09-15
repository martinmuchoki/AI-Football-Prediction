from __future__ import annotations

from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.services.sportsq_fixture_lifecycle import fixture_lifecycle_rows
from app.services.sportsq_stage3 import (
    enqueue_package,
    generate_fixture_content_package,
)


def automate_ready_fixture_content(
    session: Session,
    *,
    competition_id: int,
    season: int,
    content_root: str | Path | None = None,
    provider: str = "api-football",
    limit: int = 500,
) -> dict[str, Any]:
    """Generate and DRAFT-enqueue content for Stage-7-ready fixtures.

    Safety contract:
    - Stage 7 remains the single readiness authority.
    - Existing package and queue idempotency are preserved.
    - Queue entries are manual_export + DRAFT only.
    - No approval, processing, network posting, prediction mutation,
      lock mutation, or holdout mutation is performed.
    - A failure for one fixture is isolated from the remaining fixtures.
    """

    rows = fixture_lifecycle_rows(
        session,
        competition_id=int(competition_id),
        season=int(season),
        limit=int(limit),
        provider=provider,
    )

    ready_rows = [
        row
        for row in rows
        if row.get("content_handoff_ready") is True
    ]

    results: list[dict[str, Any]] = []
    generated = 0
    reused = 0
    queued = 0
    failed = 0

    for row in ready_rows:
        fixture_id = int(row["fixture_id"])

        try:
            package = generate_fixture_content_package(
                session,
                fixture_id=fixture_id,
                competition_id=int(competition_id),
                season=int(season),
                content_root=content_root,
            )

            if package.get("status") != "success":
                failed += 1
                results.append({
                    "fixture_id": fixture_id,
                    "status": "package_not_created",
                    "reason": package.get("reason") or package.get("status"),
                })
                continue

            if package.get("idempotent_reuse") is True:
                reused += 1
            else:
                generated += 1

            queue_item = enqueue_package(
                str(package["package_id"]),
                platforms=["manual_export"],
                scheduled_for=None,
                content_root=content_root,
            )

            if queue_item.get("status") != "DRAFT":
                raise RuntimeError("content automation may create DRAFT queue items only")

            if queue_item.get("approved_at") is not None:
                raise RuntimeError("content automation must not approve queue items")

            queued += 1

            results.append({
                "fixture_id": fixture_id,
                "status": "ready",
                "package_id": package["package_id"],
                "content_fingerprint": package.get("content_fingerprint"),
                "idempotent_reuse": bool(package.get("idempotent_reuse")),
                "queue_id": queue_item.get("queue_id"),
                "queue_status": queue_item.get("status"),
                "platforms": queue_item.get("platforms"),
            })

        except Exception as exc:
            failed += 1
            results.append({
                "fixture_id": fixture_id,
                "status": "isolated_failure",
                "detail": type(exc).__name__,
            })

    return {
        "status": "success" if failed == 0 else "partial",
        "competition_id": int(competition_id),
        "season": int(season),
        "ready_count": len(ready_rows),
        "generated_count": generated,
        "reused_count": reused,
        "draft_queue_count": queued,
        "failed_count": failed,
        "results": results,
        "safety": {
            "approval_performed": False,
            "queue_processing_performed": False,
            "social_auto_posting_performed": False,
            "network_posting_performed": False,
            "prediction_engine_rewritten": False,
            "prediction_rows_modified": False,
            "prediction_locks_modified": False,
            "historical_holdout_touched": False,
            "post_kickoff_features_used": False,
        },
    }
