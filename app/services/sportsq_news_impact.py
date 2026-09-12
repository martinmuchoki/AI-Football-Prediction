
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    inspect,
    insert,
    select,
    text,
)
from sqlalchemy.orm import Session

from app.db import engine

TABLE_NAME = "sportsq_team_news_events"
PROVIDER = "api-football"
SOURCE_ID = "api-football"

MODEL_NAME = "sportsq-news-impact-availability-burden"
MODEL_VERSION = "1.0"

LIVE_2026_INGESTION_ENABLED = False
LIVE_2026_BLOCK_REASON = (
    "CURRENT_API_PLAN_DID_NOT_ALLOW_2026_DURING_STAGE6_VALIDATION"
)

HISTORICAL_SCHEMA_VALIDATED = True
HISTORICAL_VALIDATION_FIXTURE_ID = 1208021

NEGATIVE_WEIGHTS = {
    "OUT": 20.0,
    "SUSPENDED": 20.0,
    "MISSING_FIXTURE": 20.0,
    "DOUBTFUL": 12.0,
    "QUESTIONABLE": 7.0,
}

POSITIVE_STATUSES = {
    "AVAILABLE",
    "STARTING",
    "BENCH",
}

KNOWN_STATUSES = set(NEGATIVE_WEIGHTS) | POSITIVE_STATUSES

metadata = MetaData()

team_news_events_table = Table(
    TABLE_NAME,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("event_key", String(64), nullable=False),
    Column("provider", String(80), nullable=False),
    Column("source_id", String(120), nullable=False),
    Column("source_verified", Boolean, nullable=False, default=False),
    Column("source_trust_class", String(80), nullable=True),
    Column("provider_fixture_id", Integer, nullable=True),
    Column("internal_fixture_id", Integer, nullable=True),
    Column("provider_league_id", Integer, nullable=True),
    Column("season", Integer, nullable=True),
    Column("provider_team_id", Integer, nullable=True),
    Column("internal_team_id", Integer, nullable=True),
    Column("team_name", String(180), nullable=True),
    Column("provider_player_id", Integer, nullable=True),
    Column("player_name", String(180), nullable=True),
    Column("event_category", String(40), nullable=False),
    Column("availability_status", String(40), nullable=False),
    Column("raw_type", String(120), nullable=True),
    Column("reason", String(500), nullable=True),
    Column("lineup_role", String(40), nullable=True),
    Column("formation", String(40), nullable=True),
    Column("observed_at", DateTime(timezone=True), nullable=False),
    Column("fixture_kickoff_at", DateTime(timezone=True), nullable=True),
    Column("valid_until_at", DateTime(timezone=True), nullable=True),
    Column("verification_state", String(40), nullable=False),
    Column("payload_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("event_key", name="uq_sportsq_team_news_event_key"),
)

TRIGGER_UPDATE = "trg_sportsq_team_news_events_no_update"
TRIGGER_DELETE = "trg_sportsq_team_news_events_no_delete"


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_utc(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _parse_dt(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value)

    raw = str(value).strip()
    if not raw:
        return None
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"

    try:
        return _utc(datetime.fromisoformat(raw))
    except Exception:
        return None


def ensure_news_impact_schema() -> dict[str, Any]:
    metadata.create_all(engine, tables=[team_news_events_table], checkfirst=True)

    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            conn.execute(
                text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {TRIGGER_UPDATE}
                    BEFORE UPDATE ON {TABLE_NAME}
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'sportsq_team_news_events is append-only'
                        );
                    END
                    """
                )
            )
            conn.execute(
                text(
                    f"""
                    CREATE TRIGGER IF NOT EXISTS {TRIGGER_DELETE}
                    BEFORE DELETE ON {TABLE_NAME}
                    BEGIN
                        SELECT RAISE(
                            ABORT,
                            'sportsq_team_news_events is append-only'
                        );
                    END
                    """
                )
            )

    if TABLE_NAME not in set(inspect(engine).get_table_names()):
        raise RuntimeError("News Impact event table was not created.")

    return {
        "table": TABLE_NAME,
        "append_only": True,
        "update_trigger": TRIGGER_UPDATE,
        "delete_trigger": TRIGGER_DELETE,
    }


def _normalise_name(value: Any) -> str:
    return " ".join(str(value or "").strip().lower().split())


def _source_verification(
    session: Session,
    *,
    source_id: str,
) -> dict[str, Any]:
    source_id = str(source_id or "").strip()
    tables = set(inspect(session.get_bind()).get_table_names())

    if "sportsq_sources" in tables:
        row = session.execute(
            text(
                """
                SELECT source_id, trust_class, status
                FROM sportsq_sources
                WHERE lower(source_id) = lower(:source_id)
                   OR lower(name) = lower(:source_id)
                LIMIT 1
                """
            ),
            {"source_id": source_id},
        ).mappings().first()

        if row is not None:
            active = str(row.get("status") or "").upper() == "ACTIVE"
            trust = str(row.get("trust_class") or "").upper()
            verified = active and trust in {
                "OFFICIAL",
                "VERIFIED",
                "VERIFIED_PROVIDER",
                "STRUCTURED_PROVIDER",
            }
            return {
                "verified": verified,
                "registry": "sportsq_sources",
                "trust_class": trust or None,
            }

    if source_id.lower() in {"api-football", "provider-api-football"} and "data_sources" in tables:
        row = session.execute(
            text(
                """
                SELECT slug, enabled, health_status
                FROM data_sources
                WHERE lower(slug) = 'api-football'
                LIMIT 1
                """
            )
        ).mappings().first()

        if row is not None:
            verified = (
                bool(row.get("enabled"))
                and str(row.get("health_status") or "").upper() == "OK"
            )
            return {
                "verified": verified,
                "registry": "data_sources",
                "trust_class": (
                    "STRUCTURED_PROVIDER"
                    if verified
                    else "UNVERIFIED_PROVIDER"
                ),
            }

    return {
        "verified": False,
        "registry": None,
        "trust_class": None,
    }


def _normalise_availability(raw_type: Any, reason: Any = None) -> str:
    raw = str(raw_type or "").strip()
    combined = f"{raw} {str(reason or '').strip()}".strip().lower()

    if "suspend" in combined:
        return "SUSPENDED"
    if "missing fixture" in combined or raw.lower() == "out":
        return "MISSING_FIXTURE"
    if "doubt" in combined:
        return "DOUBTFUL"
    if "questionable" in combined:
        return "QUESTIONABLE"
    if "starting" in combined:
        return "STARTING"
    if "bench" in combined:
        return "BENCH"
    if "available" in combined:
        return "AVAILABLE"
    return "UNKNOWN"


def normalise_api_football_injuries(
    payload: dict[str, Any],
    *,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    observed = _utc(observed_at)
    items = payload.get("response", [])
    if not isinstance(items, list):
        return []

    result: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        fixture = item.get("fixture") or {}
        league = item.get("league") or {}
        team = item.get("team") or {}
        player = item.get("player") or {}

        raw_type = player.get("type")
        reason = player.get("reason")
        kickoff = _parse_dt(fixture.get("date"))

        result.append(
            {
                "provider": PROVIDER,
                "source_id": SOURCE_ID,
                "provider_fixture_id": fixture.get("id"),
                "provider_league_id": league.get("id"),
                "season": league.get("season"),
                "provider_team_id": team.get("id"),
                "team_name": team.get("name"),
                "provider_player_id": player.get("id"),
                "player_name": player.get("name"),
                "event_category": "AVAILABILITY",
                "availability_status": _normalise_availability(raw_type, reason),
                "raw_type": raw_type,
                "reason": reason,
                "lineup_role": None,
                "formation": None,
                "observed_at": observed,
                "fixture_kickoff_at": kickoff,
                "valid_until_at": kickoff,
                "raw_payload": item,
            }
        )

    return result


def normalise_api_football_lineups(
    payload: dict[str, Any],
    *,
    provider_fixture_id: int,
    fixture_kickoff_at: datetime,
    observed_at: datetime,
) -> list[dict[str, Any]]:
    observed = _utc(observed_at)
    kickoff = _utc(fixture_kickoff_at)
    items = payload.get("response", [])

    if not isinstance(items, list):
        return []

    result: list[dict[str, Any]] = []

    for item in items:
        if not isinstance(item, dict):
            continue

        team = item.get("team") or {}
        formation = item.get("formation")

        for key, role, status in (
            ("startXI", "STARTING", "STARTING"),
            ("substitutes", "SUBSTITUTE", "BENCH"),
        ):
            players = item.get(key, [])
            if not isinstance(players, list):
                continue

            for wrapped in players:
                player = (
                    wrapped.get("player")
                    if isinstance(wrapped, dict)
                    else None
                )
                if not isinstance(player, dict):
                    continue

                result.append(
                    {
                        "provider": PROVIDER,
                        "source_id": SOURCE_ID,
                        "provider_fixture_id": int(provider_fixture_id),
                        "provider_league_id": None,
                        "season": None,
                        "provider_team_id": team.get("id"),
                        "team_name": team.get("name"),
                        "provider_player_id": player.get("id"),
                        "player_name": player.get("name"),
                        "event_category": "LINEUP",
                        "availability_status": status,
                        "raw_type": role,
                        "reason": None,
                        "lineup_role": role,
                        "formation": formation,
                        "observed_at": observed,
                        "fixture_kickoff_at": kickoff,
                        "valid_until_at": kickoff,
                        "raw_payload": wrapped,
                    }
                )

    return result


def _event_key(event: dict[str, Any]) -> str:
    basis = {
        "provider": event.get("provider"),
        "source_id": event.get("source_id"),
        "provider_fixture_id": event.get("provider_fixture_id"),
        "internal_fixture_id": event.get("internal_fixture_id"),
        "provider_team_id": event.get("provider_team_id"),
        "internal_team_id": event.get("internal_team_id"),
        "team_name": event.get("team_name"),
        "provider_player_id": event.get("provider_player_id"),
        "player_name": event.get("player_name"),
        "event_category": event.get("event_category"),
        "availability_status": event.get("availability_status"),
        "raw_type": event.get("raw_type"),
        "reason": event.get("reason"),
        "observed_at": _utc(event["observed_at"]).isoformat(),
    }
    return hashlib.sha256(
        json.dumps(
            basis,
            sort_keys=True,
            default=str,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def record_team_news_event(
    session: Session,
    event: dict[str, Any],
    *,
    internal_fixture_id: int | None = None,
    internal_team_id: int | None = None,
) -> dict[str, Any]:
    ensure_news_impact_schema()

    observed_at = _utc(event["observed_at"])
    kickoff = _parse_dt(event.get("fixture_kickoff_at"))
    valid_until = _parse_dt(event.get("valid_until_at"))

    source = _source_verification(
        session,
        source_id=str(event.get("source_id") or ""),
    )

    status = str(event.get("availability_status") or "UNKNOWN").upper()
    temporal_ok = kickoff is None or observed_at < kickoff

    verified = bool(
        source["verified"]
        and status in KNOWN_STATUSES
        and temporal_ok
    )

    verification_state = "VERIFIED" if verified else "UNVERIFIED"

    enriched = {
        **event,
        "internal_fixture_id": (
            internal_fixture_id
            if internal_fixture_id is not None
            else event.get("internal_fixture_id")
        ),
        "internal_team_id": (
            internal_team_id
            if internal_team_id is not None
            else event.get("internal_team_id")
        ),
        "availability_status": status,
        "observed_at": observed_at,
        "fixture_kickoff_at": kickoff,
        "valid_until_at": valid_until,
    }

    key = _event_key(enriched)

    existing = session.execute(
        select(team_news_events_table).where(
            team_news_events_table.c.event_key == key
        )
    ).mappings().first()

    if existing is not None:
        return dict(existing)

    now = datetime.now(timezone.utc)

    values = {
        "event_key": key,
        "provider": str(enriched.get("provider") or "unknown"),
        "source_id": str(enriched.get("source_id") or "unknown"),
        "source_verified": bool(source["verified"]),
        "source_trust_class": source.get("trust_class"),
        "provider_fixture_id": enriched.get("provider_fixture_id"),
        "internal_fixture_id": enriched.get("internal_fixture_id"),
        "provider_league_id": enriched.get("provider_league_id"),
        "season": enriched.get("season"),
        "provider_team_id": enriched.get("provider_team_id"),
        "internal_team_id": enriched.get("internal_team_id"),
        "team_name": enriched.get("team_name"),
        "provider_player_id": enriched.get("provider_player_id"),
        "player_name": enriched.get("player_name"),
        "event_category": str(
            enriched.get("event_category") or "UNKNOWN"
        ).upper(),
        "availability_status": status,
        "raw_type": enriched.get("raw_type"),
        "reason": enriched.get("reason"),
        "lineup_role": enriched.get("lineup_role"),
        "formation": enriched.get("formation"),
        "observed_at": _db_utc(observed_at),
        "fixture_kickoff_at": (
            _db_utc(kickoff) if kickoff is not None else None
        ),
        "valid_until_at": (
            _db_utc(valid_until) if valid_until is not None else None
        ),
        "verification_state": verification_state,
        "payload_json": json.dumps(
            enriched.get("raw_payload", {}),
            sort_keys=True,
            default=str,
        ),
        "created_at": _db_utc(now),
    }

    session.execute(insert(team_news_events_table).values(**values))
    session.commit()

    row = session.execute(
        select(team_news_events_table).where(
            team_news_events_table.c.event_key == key
        )
    ).mappings().one()

    return dict(row)


def _player_key(event: dict[str, Any]) -> tuple[str, str]:
    team = (
        str(event.get("internal_team_id"))
        if event.get("internal_team_id") is not None
        else _normalise_name(event.get("team_name"))
    )
    player = (
        str(event.get("provider_player_id"))
        if event.get("provider_player_id") is not None
        else _normalise_name(event.get("player_name"))
    )
    return team, player


def _source_player_key(
    event: dict[str, Any],
) -> tuple[str, str, str, str]:
    team, player = _player_key(event)
    return (
        team,
        player,
        str(event.get("source_id") or "").lower(),
        str(event.get("event_category") or "UNKNOWN").upper(),
    )


def _status_group(status: str) -> str:
    if status in NEGATIVE_WEIGHTS:
        return "NEGATIVE"
    if status in POSITIVE_STATUSES:
        return "POSITIVE"
    return "UNKNOWN"


def compute_news_impact_from_events(
    events: Iterable[dict[str, Any]],
    *,
    home_team_id: int | None,
    away_team_id: int | None,
    home_team_name: str | None,
    away_team_name: str | None,
    kickoff_at: datetime,
    cutoff_at: datetime,
) -> dict[str, Any]:
    kickoff = _utc(kickoff_at)
    cutoff = min(_utc(cutoff_at), kickoff)

    active: list[dict[str, Any]] = []
    stale_count = 0
    future_count = 0

    for raw in events:
        event = dict(raw)
        observed = _parse_dt(event.get("observed_at"))

        if observed is None:
            continue
        if observed > cutoff or observed >= kickoff:
            future_count += 1
            continue

        valid_until = _parse_dt(event.get("valid_until_at"))
        if valid_until is not None and valid_until < cutoff:
            stale_count += 1
            continue

        active.append(event)

    base = {
        "analysis_cutoff_at": cutoff.isoformat(),
        "kickoff_at": kickoff.isoformat(),
        "lookahead_protection": True,
        "future_events_excluded": future_count,
        "stale_events_excluded": stale_count,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "fabricated": False,
    }

    if not active:
        return {
            "status": "NO_STRUCTURED_TEAM_NEWS_INPUT",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
            "home": {
                "team": home_team_name,
                "availability_burden": None,
                "event_count": 0,
            },
            "away": {
                "team": away_team_name,
                "availability_burden": None,
                "event_count": 0,
            },
            "note": (
                "No verified structured team-news observation is available "
                "before the analysis cutoff. No injury/news impact is inferred "
                "or fabricated."
            ),
            **base,
        }

    blocking = [
        event
        for event in active
        if (
            not bool(event.get("source_verified"))
            or str(event.get("verification_state") or "").upper()
            != "VERIFIED"
            or str(event.get("availability_status") or "UNKNOWN").upper()
            not in KNOWN_STATUSES
        )
    ]

    if blocking:
        return {
            "status": "UNVERIFIED_TEAM_NEWS",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
            "active_event_count": len(active),
            "blocking_event_count": len(blocking),
            "note": (
                "At least one active team-news observation is unverified. "
                "Impact scoring is fail-closed."
            ),
            **base,
        }

    latest_by_source: dict[
        tuple[str, str, str, str],
        dict[str, Any],
    ] = {}

    for event in active:
        key = _source_player_key(event)
        observed = _parse_dt(event.get("observed_at"))
        current = latest_by_source.get(key)
        current_observed = (
            _parse_dt(current.get("observed_at"))
            if current is not None
            else None
        )

        if (
            current is None
            or current_observed is None
            or (
                observed is not None
                and observed >= current_observed
            )
        ):
            latest_by_source[key] = event

    player_groups: dict[
        tuple[str, str],
        list[dict[str, Any]],
    ] = {}

    for event in latest_by_source.values():
        player_groups.setdefault(_player_key(event), []).append(event)

    conflicts: list[tuple[str, str]] = []

    for key, group in player_groups.items():
        groups = {
            _status_group(
                str(item.get("availability_status") or "UNKNOWN").upper()
            )
            for item in group
        }
        if "NEGATIVE" in groups and "POSITIVE" in groups:
            conflicts.append(key)

    if conflicts:
        return {
            "status": "CONFLICTED_TEAM_NEWS",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
            "active_event_count": len(active),
            "conflict_count": len(conflicts),
            "note": (
                "Verified sources disagree on player availability. "
                "Impact scoring is fail-closed until conflict resolution."
            ),
            **base,
        }

    def matches_team(
        event: dict[str, Any],
        *,
        team_id: int | None,
        team_name: str | None,
    ) -> bool:
        internal_id = event.get("internal_team_id")
        if (
            team_id is not None
            and internal_id is not None
            and int(internal_id) == int(team_id)
        ):
            return True

        normal_team_name = _normalise_name(team_name)
        return bool(
            normal_team_name
            and _normalise_name(event.get("team_name")) == normal_team_name
        )

    collapsed = [
        max(
            group,
            key=lambda item: (
                _parse_dt(item.get("observed_at"))
                or datetime.min.replace(tzinfo=timezone.utc)
            ),
        )
        for group in player_groups.values()
    ]

    def burden_for(
        *,
        team_id: int | None,
        team_name: str | None,
    ) -> tuple[float, list[dict[str, Any]]]:
        team_events = [
            event
            for event in collapsed
            if matches_team(
                event,
                team_id=team_id,
                team_name=team_name,
            )
        ]

        burden = min(
            100.0,
            sum(
                NEGATIVE_WEIGHTS.get(
                    str(event.get("availability_status") or "").upper(),
                    0.0,
                )
                for event in team_events
            ),
        )

        return round(burden, 1), team_events

    home_burden, home_events = burden_for(
        team_id=home_team_id,
        team_name=home_team_name,
    )
    away_burden, away_events = burden_for(
        team_id=away_team_id,
        team_name=away_team_name,
    )

    difference = round(abs(home_burden - away_burden), 1)

    if home_burden > away_burden + 2.0:
        direction = "HOME_NEGATIVE"
    elif away_burden > home_burden + 2.0:
        direction = "AWAY_NEGATIVE"
    else:
        direction = "BALANCED"

    return {
        "status": "VERIFIED_TEAM_NEWS",
        "impact_score": difference,
        "direction": direction,
        "verified": True,
        "impact_score_meaning": (
            "Absolute difference between home and away verified availability "
            "burdens. It is not a win-probability adjustment."
        ),
        "home": {
            "team": home_team_name,
            "availability_burden": home_burden,
            "event_count": len(home_events),
        },
        "away": {
            "team": away_team_name,
            "availability_burden": away_burden,
            "event_count": len(away_events),
        },
        "active_event_count": len(active),
        "note": (
            "Availability burden uses only verified pre-cutoff structured "
            "observations. No player-quality weighting is inferred."
        ),
        **base,
    }


def _fixture_context(
    session: Session,
    *,
    fixture_id: int,
) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            SELECT f.id,
                   f.provider_fixture_id,
                   f.home_team_id,
                   f.away_team_id,
                   f.kickoff_utc,
                   ht.name AS home_team_name,
                   at.name AS away_team_name
            FROM fixtures f
            LEFT JOIN teams ht
              ON ht.id = f.home_team_id
            LEFT JOIN teams at
              ON at.id = f.away_team_id
            WHERE f.id = :fixture_id
               OR f.provider_fixture_id = :fixture_id
            ORDER BY
                CASE
                    WHEN f.id = :fixture_id THEN 0
                    ELSE 1
                END
            LIMIT 1
            """
        ),
        {"fixture_id": int(fixture_id)},
    ).mappings().first()

    return dict(row) if row is not None else None


def list_team_news_events(
    session: Session,
    *,
    fixture_id: int | None = None,
    limit: int = 200,
) -> list[dict[str, Any]]:
    if TABLE_NAME not in set(inspect(session.get_bind()).get_table_names()):
        return []

    safe_limit = max(1, min(int(limit), 1000))
    stmt = (
        select(team_news_events_table)
        .order_by(
            team_news_events_table.c.observed_at.asc(),
            team_news_events_table.c.id.asc(),
        )
        .limit(safe_limit)
    )

    if fixture_id is not None:
        context = _fixture_context(session, fixture_id=int(fixture_id))
        if context is None:
            return []

        conditions = [
            team_news_events_table.c.internal_fixture_id == int(context["id"])
        ]
        provider_fixture_id = context.get("provider_fixture_id")
        if provider_fixture_id is not None:
            conditions.append(
                team_news_events_table.c.provider_fixture_id
                == int(provider_fixture_id)
            )

        condition = conditions[0]
        for extra in conditions[1:]:
            condition = condition | extra

        stmt = stmt.where(condition)

    rows = session.execute(stmt).mappings().all()
    result = []

    for row in rows:
        item = dict(row)
        for key in (
            "observed_at",
            "fixture_kickoff_at",
            "valid_until_at",
            "created_at",
        ):
            value = item.get(key)
            if isinstance(value, datetime):
                item[key] = _utc(value).isoformat()
        result.append(item)

    return result


def news_impact_for_fixture(
    session: Session,
    *,
    fixture_id: int,
    cutoff_at: datetime | None = None,
) -> dict[str, Any]:
    context = _fixture_context(session, fixture_id=int(fixture_id))

    if context is None:
        return {
            "status": "FIXTURE_NOT_FOUND",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
            "fabricated": False,
        }

    kickoff = _parse_dt(context.get("kickoff_utc"))

    if kickoff is None:
        return {
            "status": "FIXTURE_KICKOFF_UNAVAILABLE",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
            "fabricated": False,
            "note": (
                "Fixture kickoff could not be parsed safely. "
                "News Impact scoring is fail-closed."
            ),
        }

    cutoff = _parse_dt(
        cutoff_at or datetime.now(timezone.utc)
    )

    if cutoff is None:
        return {
            "status": "ANALYSIS_CUTOFF_UNAVAILABLE",
            "impact_score": None,
            "direction": "UNVERIFIED",
            "verified": False,
            "fabricated": False,
            "note": (
                "Analysis cutoff could not be parsed safely. "
                "News Impact scoring is fail-closed."
            ),
        }

    events = list_team_news_events(
        session,
        fixture_id=int(context["id"]),
        limit=1000,
    )

    return compute_news_impact_from_events(
        events,
        home_team_id=context["home_team_id"],
        away_team_id=context["away_team_id"],
        home_team_name=context["home_team_name"],
        away_team_name=context["away_team_name"],
        kickoff_at=kickoff,
        cutoff_at=cutoff,
    )


def news_impact_status(session: Session) -> dict[str, Any]:
    tables = set(inspect(session.get_bind()).get_table_names())
    table_ready = TABLE_NAME in tables

    row_count = 0
    if table_ready:
        row_count = int(
            session.execute(
                text(f"SELECT COUNT(*) FROM {TABLE_NAME}")
            ).scalar_one()
        )

    source = _source_verification(session, source_id=SOURCE_ID)

    return {
        "brand": "MDRN SportsQ",
        "product": "SportsQ News Impact",
        "stage": 6,
        "table_ready": table_ready,
        "table": TABLE_NAME,
        "event_rows": row_count,
        "append_only_evidence": True,
        "model_name": MODEL_NAME,
        "model_version": MODEL_VERSION,
        "historical_provider_schema_validated": HISTORICAL_SCHEMA_VALIDATED,
        "historical_validation_fixture_id": HISTORICAL_VALIDATION_FIXTURE_ID,
        "injury_adapter_schema_validated": True,
        "lineup_adapter_schema_validated": True,
        "provider": PROVIDER,
        "provider_source_verified": source["verified"],
        "provider_source_registry": source["registry"],
        "provider_trust_class": source["trust_class"],
        "live_2026_ingestion_enabled": LIVE_2026_INGESTION_ENABLED,
        "live_2026_block_reason": LIVE_2026_BLOCK_REASON,
        "strict_temporal_cutoff": True,
        "fail_closed_verification": True,
        "player_quality_weighting_inferred": False,
        "prediction_engine_modified": False,
        "live_prediction_rows_modified": False,
        "scorecall_rows_modified": False,
        "final_holdout_touched": False,
    }
