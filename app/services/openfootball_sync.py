from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, aliased

from app.models import (
    DataSource,
    Fixture,
    LeagueSeason,
    SourceSnapshot,
    Team,
)
from app.providers.openfootball_json import OpenFootballDocument
from app.services.source_policy import require_collection_approved
from app.services.team_identity import normalize_team_name
from app.services.source_registry import (
    get_source,
    mark_source_success,
    record_observation,
    seed_default_sources,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _norm_team(value: str) -> str:
    return normalize_team_name(value)


def _match_entity_key(season_label: str, item: dict[str, Any]) -> str:
    return "|".join(
        [
            "epl",
            season_label,
            str(item.get("date") or ""),
            _norm_team(str(item.get("team1") or "")),
            _norm_team(str(item.get("team2") or "")),
        ]
    )


def store_openfootball_document(
    session: Session,
    *,
    document: OpenFootballDocument,
    season_label: str,
) -> dict[str, Any]:
    seed_default_sources(session)
    require_collection_approved(session, "openfootball-json")
    source = get_source(session, "openfootball-json")

    raw_bytes = document.raw_text.encode("utf-8")
    content_hash = hashlib.sha256(raw_bytes).hexdigest()
    existing = session.scalar(
        select(SourceSnapshot).where(
            SourceSnapshot.source_id == source.id,
            SourceSnapshot.canonical_url == document.api_url,
            SourceSnapshot.content_hash == content_hash,
        )
    )
    if existing is not None:
        mark_source_success(session, source)
        session.commit()
        return {
            "status": "success",
            "source": source.slug,
            "snapshot_inserted": False,
            "content_hash": content_hash,
            "matches": len(document.payload.get("matches", [])),
            "observations_inserted": 0,
        }

    collected_at = _utcnow()
    snapshot = SourceSnapshot(
        source_id=source.id,
        source_url=document.api_url,
        canonical_url=document.api_url,
        status_code=200,
        content_type="application/json",
        content_hash=content_hash,
        body_text=document.raw_text,
        body_bytes=len(raw_bytes),
        robots_allowed=True,
        parser_version=source.parser_version,
        collected_at=collected_at,
    )
    session.add(snapshot)

    observations = 0
    for item in document.payload.get("matches", []):
        if not isinstance(item, dict):
            continue
        entity_key = _match_entity_key(season_label, item)
        fields = {
            "date": item.get("date"),
            "round": item.get("round"),
            "home_team": item.get("team1"),
            "away_team": item.get("team2"),
        }
        score = item.get("score")
        if isinstance(score, dict) and score.get("ft") is not None:
            fields["score_ft"] = score.get("ft")

        for field_name, value in fields.items():
            if value is None:
                continue
            record_observation(
                session,
                source_slug=source.slug,
                entity_type="fixture",
                entity_key=entity_key,
                field_name=field_name,
                value=value,
                source_url=document.api_url,
                observed_at=collected_at,
                raw_hash=content_hash,
                parser_version=source.parser_version,
            )
            observations += 1

    mark_source_success(session, source, checked_at=collected_at)
    session.commit()
    return {
        "status": "success",
        "source": source.slug,
        "snapshot_inserted": True,
        "content_hash": content_hash,
        "matches": len(document.payload.get("matches", [])),
        "observations_inserted": observations,
    }


def crosscheck_openfootball_vs_live(
    session: Session,
    *,
    payload: dict[str, Any],
    competition_id: int,
    season: int,
) -> dict[str, Any]:
    Home = aliased(Team)
    Away = aliased(Team)
    stmt = (
        select(Fixture, Home.name, Away.name)
        .join(LeagueSeason, Fixture.league_season_id == LeagueSeason.id)
        .join(Home, Fixture.home_team_id == Home.id)
        .join(Away, Fixture.away_team_id == Away.id)
        .where(
            Fixture.provider == "live-score-api",
            LeagueSeason.provider == "live-score-api",
            LeagueSeason.provider_league_id == int(competition_id),
            LeagueSeason.season == int(season),
        )
    )

    live_keys: dict[tuple[str, str, str], int] = {}
    for fixture, home, away in session.execute(stmt).all():
        key = (
            fixture.kickoff_utc.date().isoformat(),
            _norm_team(str(home)),
            _norm_team(str(away)),
        )
        live_keys[key] = int(fixture.provider_fixture_id)

    open_keys: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in payload.get("matches", []):
        if not isinstance(item, dict):
            continue
        date = str(item.get("date") or "")
        home = _norm_team(str(item.get("team1") or ""))
        away = _norm_team(str(item.get("team2") or ""))
        if date and home and away:
            open_keys[(date, home, away)] = item

    matched = sorted(set(live_keys) & set(open_keys))
    only_live = sorted(set(live_keys) - set(open_keys))
    only_open = sorted(set(open_keys) - set(live_keys))

    return {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),
        "live_score_fixtures": len(live_keys),
        "openfootball_matches": len(open_keys),
        "matched": len(matched),
        "live_only": len(only_live),
        "openfootball_only": len(only_open),
        "sample_live_only": [
            {"date": d, "home": h, "away": a, "fixture_id": live_keys[(d, h, a)]}
            for d, h, a in only_live[:10]
        ],
        "sample_openfootball_only": [
            {"date": d, "home": h, "away": a}
            for d, h, a in only_open[:10]
        ],
        "final_holdout_touched": False,
    }
