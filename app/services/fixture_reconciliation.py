from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session, aliased

from app.models import Fixture, FixtureReconciliation, LeagueSeason, Team
from app.services.team_identity import canonical_fixture_key, normalize_team_name

PRIMARY_SOURCE = "live-score-api"
SECONDARY_SOURCE = "openfootball-json"
COMPETITION_KEY = "epl"


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _score_text(home: Any, away: Any) -> str | None:
    if home is None or away is None:
        return None
    try:
        return f"{int(home)}-{int(away)}"
    except (TypeError, ValueError):
        return None


def _openfootball_score(item: dict[str, Any]) -> str | None:
    """Return an OpenFootball full-time score without losing zero values.

    OpenFootball season payloads may represent the score either as the
    historical {"ft": [home, away]} mapping or directly as [home, away].
    Both shapes are valid and a 0-0 result must remain a real score rather
    than being treated as missing provider data.
    """
    score = item.get("score")

    if isinstance(score, dict):
        ft = score.get("ft")
    elif isinstance(score, (list, tuple)):
        ft = score
    else:
        return None

    if not isinstance(ft, (list, tuple)) or len(ft) != 2:
        return None

    return _score_text(ft[0], ft[1])


def _live_rows(
    session: Session,
    *,
    competition_id: int,
    season: int,
    primary_source: str = PRIMARY_SOURCE,
) -> dict[str, dict[str, Any]]:
    Home = aliased(Team)
    Away = aliased(Team)
    stmt = (
        select(Fixture, Home.name, Away.name)
        .join(LeagueSeason, Fixture.league_season_id == LeagueSeason.id)
        .join(Home, Fixture.home_team_id == Home.id)
        .join(Away, Fixture.away_team_id == Away.id)
        .where(
            Fixture.provider == primary_source,
            LeagueSeason.provider == primary_source,
            LeagueSeason.provider_league_id == int(competition_id),
            LeagueSeason.season == int(season),
        )
    )

    out: dict[str, dict[str, Any]] = {}
    for fixture, home, away in session.execute(stmt).all():
        key = canonical_fixture_key(
            competition=COMPETITION_KEY,
            season=season,
            home=str(home),
            away=str(away),
        )
        live_score = None
        if fixture.is_finished:
            live_score = _score_text(
                fixture.fulltime_home if fixture.fulltime_home is not None else fixture.home_goals,
                fixture.fulltime_away if fixture.fulltime_away is not None else fixture.away_goals,
            )
        out[key] = {
            "fixture_id": int(fixture.provider_fixture_id),
            "home": str(home),
            "away": str(away),
            "date": fixture.kickoff_utc.date().isoformat(),
            "finished": bool(fixture.is_finished),
            "score": live_score,
        }
    return out


def _open_rows(payload: dict[str, Any], *, season: int) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for item in payload.get("matches", []):
        if not isinstance(item, dict):
            continue
        home = str(item.get("team1") or "").strip()
        away = str(item.get("team2") or "").strip()
        if not home or not away:
            continue
        key = canonical_fixture_key(
            competition=COMPETITION_KEY,
            season=season,
            home=home,
            away=away,
        )
        out[key] = {
            "entity_key": key,
            "home": home,
            "away": away,
            "date": str(item.get("date") or "") or None,
            "score": _openfootball_score(item),
        }
    return out


def _statuses(
    primary: dict[str, Any] | None,
    secondary: dict[str, Any] | None,
) -> tuple[str, str, str]:
    """Classify fixture/date/result agreement between providers.

    v1.0.7:
    A future schedule-only disagreement of no more than two calendar
    days is SOURCE_LAG only when the fixture is unfinished and both
    sources are scoreless.

    Finished/result conflicts, historical date disagreements and
    differences greater than two days remain CONFLICT.
    """
    if primary is None:
        return "MISSING_PRIMARY", "NOT_COMPARABLE", "MISSING_PRIMARY"

    if secondary is None:
        return "MISSING_SECONDARY", "NOT_COMPARABLE", "MISSING_SECONDARY"

    from datetime import date as _date
    from datetime import datetime as _datetime
    from datetime import timezone as _timezone

    def _fixture_date(value: Any) -> _date | None:
        if value is None:
            return None

        if isinstance(value, _datetime):
            return value.date()

        if isinstance(value, _date):
            return value

        text = str(value).strip()

        if not text:
            return None

        try:
            return _datetime.fromisoformat(
                text.replace("Z", "+00:00")
            ).date()
        except (TypeError, ValueError):
            pass

        try:
            return _date.fromisoformat(text[:10])
        except (TypeError, ValueError):
            return None

    primary_date_raw = primary.get("date")
    secondary_date_raw = secondary.get("date")

    if primary_date_raw == secondary_date_raw:
        date_status = "MATCH"

    else:
        primary_date = _fixture_date(primary_date_raw)
        secondary_date = _fixture_date(secondary_date_raw)

        primary_score = primary.get("score")
        secondary_score = secondary.get("score")

        today_utc = _datetime.now(_timezone.utc).date()

        if primary_date is not None and secondary_date is not None:
            variance_days = abs(
                (primary_date - secondary_date).days
            )
        else:
            variance_days = None

        schedule_only_lag = (
            not bool(primary.get("finished"))
            and primary_score is None
            and secondary_score is None
            and primary_date is not None
            and secondary_date is not None
            and primary_date >= today_utc
            and secondary_date >= today_utc
            and variance_days is not None
            and 1 <= variance_days <= 2
        )

        if schedule_only_lag:
            date_status = "SOURCE_LAG"
        else:
            date_status = "CONFLICT"

    primary_score = primary.get("score")
    secondary_score = secondary.get("score")

    if not primary.get("finished"):
        result_status = "PENDING"

    elif primary_score is None and secondary_score is None:
        result_status = "INCOMPLETE"

    elif primary_score is None or secondary_score is None:
        result_status = "SOURCE_LAG"

    elif primary_score == secondary_score:
        result_status = "MATCH"

    else:
        result_status = "CONFLICT"

    if date_status == "CONFLICT" or result_status == "CONFLICT":
        overall = "CONFLICT"

    elif (
        date_status == "SOURCE_LAG"
        or result_status in {"SOURCE_LAG", "INCOMPLETE"}
    ):
        overall = "SOURCE_LAG"

    else:
        overall = "MATCH"

    return date_status, result_status, overall


def reconcile_openfootball_payload(
    session: Session,
    *,
    payload: dict[str, Any],
    competition_id: int,
    season: int,
    checked_at: datetime | None = None,
    primary_source: str = PRIMARY_SOURCE,
) -> dict[str, Any]:
    """Persist a canonical provider vs OpenFootball comparison."""
    when = checked_at or _utcnow()
    live = _live_rows(
        session,
        competition_id=competition_id,
        season=season,
        primary_source=primary_source,
    )
    secondary = _open_rows(payload, season=season)
    all_keys = sorted(set(live) | set(secondary))

    existing = {
        row.canonical_key: row
        for row in session.scalars(
            select(FixtureReconciliation).where(
                FixtureReconciliation.competition_id == int(competition_id),
                FixtureReconciliation.season == int(season),
                FixtureReconciliation.primary_source == primary_source,
                FixtureReconciliation.secondary_source == SECONDARY_SOURCE,
            )
        ).all()
    }

    inserted = 0
    updated = 0
    counts: dict[str, int] = {}
    date_counts: dict[str, int] = {}
    result_counts: dict[str, int] = {}

    for key in all_keys:
        p = live.get(key)
        q = secondary.get(key)
        date_status, result_status, overall = _statuses(p, q)
        row = existing.get(key)
        if row is None:
            row = FixtureReconciliation(
                competition_id=int(competition_id),
                season=int(season),
                canonical_key=key,
                primary_source=primary_source,
                secondary_source=SECONDARY_SOURCE,
            )
            session.add(row)
            inserted += 1
        else:
            updated += 1

        display_home = (p or q or {}).get("home") or normalize_team_name(key.split("|")[-2])
        display_away = (p or q or {}).get("away") or normalize_team_name(key.split("|")[-1])
        row.primary_fixture_id = p.get("fixture_id") if p else None
        row.secondary_entity_key = q.get("entity_key") if q else None
        row.home_team = str(display_home)
        row.away_team = str(display_away)
        row.primary_date = p.get("date") if p else None
        row.secondary_date = q.get("date") if q else None
        row.date_status = date_status
        row.primary_finished = bool(p.get("finished")) if p else False
        row.primary_score = p.get("score") if p else None
        row.secondary_score = q.get("score") if q else None
        row.result_status = result_status
        row.overall_status = overall
        row.checked_at = when

        counts[overall] = counts.get(overall, 0) + 1
        date_counts[date_status] = date_counts.get(date_status, 0) + 1
        result_counts[result_status] = result_counts.get(result_status, 0) + 1

    stale_keys = set(existing) - set(all_keys)
    if stale_keys:
        session.execute(
            delete(FixtureReconciliation).where(
                FixtureReconciliation.competition_id == int(competition_id),
                FixtureReconciliation.season == int(season),
                FixtureReconciliation.primary_source == primary_source,
                FixtureReconciliation.secondary_source == SECONDARY_SOURCE,
                FixtureReconciliation.canonical_key.in_(stale_keys),
            )
        )

    session.commit()
    total = len(all_keys)
    clean = counts.get("MATCH", 0)
    conflict = counts.get("CONFLICT", 0)
    missing = counts.get("MISSING_PRIMARY", 0) + counts.get("MISSING_SECONDARY", 0)
    lag = counts.get("SOURCE_LAG", 0)
    gate = "FAIL" if conflict else ("WARN" if (missing or lag) else "PASS")

    return {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),
        "primary_source": primary_source,
        "secondary_source": SECONDARY_SOURCE,
        "total": total,
        "inserted": inserted,
        "updated": updated,
        "removed_stale": len(stale_keys),
        "overall_counts": counts,
        "date_counts": date_counts,
        "result_counts": result_counts,
        "fixture_agreement_rate": round(clean / total, 6) if total else None,
        "quality_gate": gate,
        "final_holdout_touched": False,
    }


def reconciliation_summary(
    session: Session,
    *,
    competition_id: int,
    season: int,
    primary_source: str = PRIMARY_SOURCE,
) -> dict[str, Any]:
    rows = list(
        session.scalars(
            select(FixtureReconciliation).where(
                FixtureReconciliation.competition_id == int(competition_id),
                FixtureReconciliation.season == int(season),
                FixtureReconciliation.primary_source == primary_source,
                FixtureReconciliation.secondary_source == SECONDARY_SOURCE,
            )
        ).all()
    )
    if not rows:
        return {
            "status": "not_initialized",
            "competition_id": int(competition_id),
            "season": int(season),
            "total": 0,
            "quality_gate": "UNKNOWN",
            "final_holdout_touched": False,
        }

    counts: dict[str, int] = {}
    for row in rows:
        counts[row.overall_status] = counts.get(row.overall_status, 0) + 1
    total = len(rows)
    clean = counts.get("MATCH", 0)
    conflict = counts.get("CONFLICT", 0)
    missing = counts.get("MISSING_PRIMARY", 0) + counts.get("MISSING_SECONDARY", 0)
    lag = counts.get("SOURCE_LAG", 0)
    gate = "FAIL" if conflict else ("WARN" if (missing or lag) else "PASS")
    latest = max(row.checked_at for row in rows)
    return {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),
        "primary_source": primary_source,
        "secondary_source": SECONDARY_SOURCE,
        "total": total,
        "overall_counts": counts,
        "fixture_agreement_rate": round(clean / total, 6),
        "quality_gate": gate,
        "last_checked_at": latest.isoformat(),
        "final_holdout_touched": False,
    }


def reconciliation_issues(
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int = 100,
    primary_source: str = PRIMARY_SOURCE,
) -> list[dict[str, Any]]:
    stmt = (
        select(FixtureReconciliation)
        .where(
            FixtureReconciliation.competition_id == int(competition_id),
            FixtureReconciliation.season == int(season),
            FixtureReconciliation.primary_source == primary_source,
            FixtureReconciliation.secondary_source == SECONDARY_SOURCE,
            FixtureReconciliation.overall_status != "MATCH",
        )
        .order_by(FixtureReconciliation.overall_status, FixtureReconciliation.canonical_key)
        .limit(max(1, min(int(limit), 1000)))
    )
    rows = list(session.scalars(stmt).all())
    return [
        {
            "canonical_key": row.canonical_key,
            "fixture_id": row.primary_fixture_id,
            "home": row.home_team,
            "away": row.away_team,
            "overall_status": row.overall_status,
            "date_status": row.date_status,
            "primary_date": row.primary_date,
            "secondary_date": row.secondary_date,
            "result_status": row.result_status,
            "primary_score": row.primary_score,
            "secondary_score": row.secondary_score,
            "checked_at": row.checked_at.isoformat(),
        }
        for row in rows
    ]
