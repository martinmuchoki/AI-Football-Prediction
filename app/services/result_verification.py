from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import FixtureReconciliation
from app.services.fixture_reconciliation import (
    _live_rows,
    _statuses,
    reconciliation_summary,
)


API_FOOTBALL_SOURCE = "api-football"
OPENFOOTBALL_SOURCE = "openfootball-json"
LIVE_SCORE_SOURCE = "live-score-api"

LIVE_SCORE_EPL_COMPETITION_ID = 2


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _quality_gate(counts: dict[str, int]) -> str:
    conflict = int(counts.get("CONFLICT", 0))

    warning = (
        int(counts.get("MISSING_PRIMARY", 0))
        + int(counts.get("MISSING_SECONDARY", 0))
        + int(counts.get("SOURCE_LAG", 0))
    )

    if conflict:
        return "FAIL"

    if warning:
        return "WARN"

    return "PASS"


def _effective_status(
    base: Any,
    verifier: Any | None,
) -> tuple[str, bool, bool]:

    base_status = str(
        getattr(base, "overall_status", "")
        or "UNKNOWN"
    ).upper()

    if base_status != "SOURCE_LAG":
        return base_status, False, False

    if str(
        getattr(base, "date_status", "")
        or ""
    ).upper() != "MATCH":
        return base_status, False, False

    if str(
        getattr(base, "result_status", "")
        or ""
    ).upper() != "SOURCE_LAG":
        return base_status, False, False

    if not bool(
        getattr(base, "primary_finished", False)
    ):
        return base_status, False, False

    if getattr(base, "primary_score", None) is None:
        return base_status, False, False

    if getattr(base, "secondary_score", None) is not None:
        return base_status, False, False

    if verifier is None:
        return "SOURCE_LAG", False, False

    verifier_date = str(
        getattr(verifier, "date_status", "")
        or ""
    ).upper()

    verifier_result = str(
        getattr(verifier, "result_status", "")
        or ""
    ).upper()

    verifier_overall = str(
        getattr(verifier, "overall_status", "")
        or ""
    ).upper()

    if (
        verifier_date == "CONFLICT"
        or verifier_result == "CONFLICT"
        or verifier_overall == "CONFLICT"
    ):
        return "CONFLICT", False, True

    if (
        verifier_date == "MATCH"
        and verifier_result == "MATCH"
        and verifier_overall == "MATCH"
    ):
        return "MATCH", True, False

    return "SOURCE_LAG", False, False


def reconcile_stored_provider_pair(
    session: Session,
    *,
    competition_id: int,
    season: int,
    primary_source: str = API_FOOTBALL_SOURCE,
    secondary_source: str = LIVE_SCORE_SOURCE,
    secondary_competition_id: int = LIVE_SCORE_EPL_COMPETITION_ID,
    checked_at: datetime | None = None,
) -> dict[str, Any]:

    when = checked_at or _utcnow()

    primary = _live_rows(
        session,
        competition_id=int(competition_id),
        season=int(season),
        primary_source=primary_source,
    )

    secondary = _live_rows(
        session,
        competition_id=int(secondary_competition_id),
        season=int(season),
        primary_source=secondary_source,
    )

    all_keys = sorted(
        set(primary) | set(secondary)
    )

    existing = {
        row.canonical_key: row
        for row in session.scalars(
            select(FixtureReconciliation).where(
                FixtureReconciliation.competition_id
                == int(competition_id),

                FixtureReconciliation.season
                == int(season),

                FixtureReconciliation.primary_source
                == primary_source,

                FixtureReconciliation.secondary_source
                == secondary_source,
            )
        ).all()
    }

    inserted = 0
    updated = 0
    counts: dict[str, int] = {}

    for key in all_keys:

        p = primary.get(key)
        q = secondary.get(key)

        (
            date_status,
            result_status,
            overall,
        ) = _statuses(
            p,
            q,
        )

        row = existing.get(key)

        if row is None:

            row = FixtureReconciliation(
                competition_id=int(
                    competition_id
                ),
                season=int(season),
                canonical_key=key,
                primary_source=primary_source,
                secondary_source=secondary_source,
            )

            session.add(row)
            inserted += 1

        else:
            updated += 1

        display = p or q or {}

        row.primary_fixture_id = (
            p.get("fixture_id")
            if p
            else None
        )

        row.secondary_entity_key = (
            str(q.get("fixture_id"))
            if q
            and q.get("fixture_id") is not None
            else None
        )

        row.home_team = str(
            display.get("home")
            or key.split("|")[-2]
        )

        row.away_team = str(
            display.get("away")
            or key.split("|")[-1]
        )

        row.primary_date = (
            p.get("date")
            if p
            else None
        )

        row.secondary_date = (
            q.get("date")
            if q
            else None
        )

        row.date_status = date_status

        row.primary_finished = bool(
            p.get("finished")
        ) if p else False

        row.primary_score = (
            p.get("score")
            if p
            else None
        )

        row.secondary_score = (
            q.get("score")
            if q
            else None
        )

        row.result_status = result_status
        row.overall_status = overall
        row.checked_at = when

        counts[overall] = (
            counts.get(overall, 0) + 1
        )

    stale_keys = (
        set(existing)
        - set(all_keys)
    )

    if stale_keys:

        session.execute(
            delete(
                FixtureReconciliation
            ).where(
                FixtureReconciliation.competition_id
                == int(competition_id),

                FixtureReconciliation.season
                == int(season),

                FixtureReconciliation.primary_source
                == primary_source,

                FixtureReconciliation.secondary_source
                == secondary_source,

                FixtureReconciliation.canonical_key.in_(
                    stale_keys
                ),
            )
        )

    session.commit()

    total = len(all_keys)
    clean = int(counts.get("MATCH", 0))

    return {
        "status": "success",
        "competition_id": int(
            competition_id
        ),
        "season": int(season),
        "primary_source": primary_source,
        "secondary_source": secondary_source,
        "secondary_competition_id": int(
            secondary_competition_id
        ),
        "total": total,
        "inserted": inserted,
        "updated": updated,
        "removed_stale": len(
            stale_keys
        ),
        "overall_counts": counts,
        "fixture_agreement_rate": (
            round(clean / total, 6)
            if total
            else None
        ),
        "quality_gate": _quality_gate(
            counts
        ),
        "final_holdout_touched": False,
    }


def composite_reconciliation_summary(
    session: Session,
    *,
    competition_id: int,
    season: int,
    primary_source: str = API_FOOTBALL_SOURCE,
    schedule_source: str = OPENFOOTBALL_SOURCE,
    result_verifier_source: str = LIVE_SCORE_SOURCE,
) -> dict[str, Any]:

    base_rows = list(
        session.scalars(
            select(FixtureReconciliation).where(
                FixtureReconciliation.competition_id
                == int(competition_id),

                FixtureReconciliation.season
                == int(season),

                FixtureReconciliation.primary_source
                == primary_source,

                FixtureReconciliation.secondary_source
                == schedule_source,
            )
        ).all()
    )

    if not base_rows:

        result = reconciliation_summary(
            session,
            competition_id=int(
                competition_id
            ),
            season=int(season),
            primary_source=primary_source,
        )

        result.update({
            "verification_mode":
                "openfootball-with-livescore-result-fallback",
            "schedule_source":
                schedule_source,
            "result_verifier_source":
                result_verifier_source,
            "fallback_verified_count":
                0,
            "verifier_conflict_count":
                0,
            "unverified_result_lag_count":
                0,
            "result_verifier_pair_count":
                0,
        })

        return result

    verifier_rows = list(
        session.scalars(
            select(FixtureReconciliation).where(
                FixtureReconciliation.competition_id
                == int(competition_id),

                FixtureReconciliation.season
                == int(season),

                FixtureReconciliation.primary_source
                == primary_source,

                FixtureReconciliation.secondary_source
                == result_verifier_source,
            )
        ).all()
    )

    verifier_by_key = {
        row.canonical_key: row
        for row in verifier_rows
    }

    counts: dict[str, int] = {}

    fallback_verified = 0
    verifier_conflicts = 0
    unverified_lag = 0

    for base in base_rows:

        (
            effective,
            used_fallback,
            verifier_conflict,
        ) = _effective_status(
            base,
            verifier_by_key.get(
                base.canonical_key
            ),
        )

        counts[effective] = (
            counts.get(effective, 0) + 1
        )

        if used_fallback:
            fallback_verified += 1

        if verifier_conflict:
            verifier_conflicts += 1

        if effective == "SOURCE_LAG":
            unverified_lag += 1

    total = len(base_rows)
    clean = int(counts.get("MATCH", 0))

    timestamps = [
        row.checked_at
        for row in (
            base_rows
            + verifier_rows
        )
        if row.checked_at is not None
    ]

    latest = (
        max(timestamps)
        if timestamps
        else None
    )

    return {
        "status": "success",
        "competition_id":
            int(competition_id),
        "season":
            int(season),

        "primary_source":
            primary_source,

        "secondary_source":
            schedule_source,

        "schedule_source":
            schedule_source,

        "result_verifier_source":
            result_verifier_source,

        "verification_mode":
            "openfootball-with-livescore-result-fallback",

        "total":
            total,

        "overall_counts":
            counts,

        "fixture_agreement_rate": (
            round(clean / total, 6)
            if total
            else None
        ),

        "quality_gate":
            _quality_gate(counts),

        "last_checked_at": (
            latest.isoformat()
            if latest is not None
            else None
        ),

        "fallback_verified_count":
            fallback_verified,

        "verifier_conflict_count":
            verifier_conflicts,

        "unverified_result_lag_count":
            unverified_lag,

        "result_verifier_pair_count":
            len(verifier_rows),

        "final_holdout_touched":
            False,
    }
