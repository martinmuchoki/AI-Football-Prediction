from __future__ import annotations

import csv
import hashlib
import io
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Fixture, LeagueSeason, SyncRun, Team
from app.providers.football_data import FootballDataClient


PROVIDER = "football-data-csv"
LONDON = ZoneInfo("Europe/London")


class FootballDataValidationError(ValueError):
    pass


def _stable_positive_id(value: str) -> int:
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def _required(row: dict[str, str], key: str) -> str:
    value = (row.get(key) or "").strip()
    if not value:
        raise FootballDataValidationError(f"{key} is required")
    return value


def _int_or_none(value: str | None) -> int | None:
    text = (value or "").strip()
    if text == "":
        return None
    try:
        number = int(float(text))
    except ValueError as exc:
        raise FootballDataValidationError(f"Invalid integer value: {text}") from exc
    if number < 0:
        raise FootballDataValidationError("Score/stat value cannot be negative")
    return number


def _parse_kickoff(row: dict[str, str]) -> datetime:
    date_text = _required(row, "Date")
    time_text = (row.get("Time") or "15:00").strip() or "15:00"

    date_value = None
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%d/%m/%Y %H:%M"):
        try:
            date_value = datetime.strptime(date_text, fmt)
            break
        except ValueError:
            pass
    if date_value is None:
        raise FootballDataValidationError(f"Unsupported Date format: {date_text}")

    try:
        time_value = datetime.strptime(time_text, "%H:%M").time()
    except ValueError as exc:
        raise FootballDataValidationError(f"Unsupported Time format: {time_text}") from exc

    local = datetime.combine(date_value.date(), time_value, tzinfo=LONDON)
    return local.astimezone(timezone.utc)


def _league(session: Session, *, season: int, league_code: str) -> LeagueSeason:
    league_id = _stable_positive_id(f"football-data:{league_code}")
    row = session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.provider == PROVIDER,
            LeagueSeason.provider_league_id == league_id,
            LeagueSeason.season == season,
        )
    )
    if row is None:
        name = "Premier League" if league_code.upper() == "E0" else league_code.upper()
        row = LeagueSeason(
            provider=PROVIDER,
            provider_league_id=league_id,
            season=season,
            name=name,
            country="England" if league_code.upper().startswith("E") else None,
            competition_type="league",
        )
        session.add(row)
        session.flush()
    return row


def _team(session: Session, name: str) -> Team:
    provider_team_id = _stable_positive_id(f"football-data-team:{name.casefold().strip()}")
    row = session.scalar(
        select(Team).where(
            Team.provider == PROVIDER,
            Team.provider_team_id == provider_team_id,
        )
    )
    if row is None:
        row = Team(
            provider=PROVIDER,
            provider_team_id=provider_team_id,
            name=name,
            country="England",
        )
        session.add(row)
        session.flush()
    else:
        row.name = name
    return row


def _result_1x2(row: dict[str, str], home_goals: int, away_goals: int) -> str:
    raw = (row.get("FTR") or "").upper().strip()
    if raw == "H":
        return "HOME"
    if raw == "A":
        return "AWAY"
    if raw == "D":
        return "DRAW"
    if home_goals > away_goals:
        return "HOME"
    if away_goals > home_goals:
        return "AWAY"
    return "DRAW"


def _fixture_id(*, season: int, league_code: str, kickoff: datetime, home: str, away: str) -> int:
    key = f"{season}|{league_code}|{kickoff.isoformat()}|{home.casefold()}|{away.casefold()}"
    return _stable_positive_id(key)


def parse_csv(text: str) -> list[dict[str, str]]:
    # utf-8-sig removes a BOM when present.
    stream = io.StringIO(text.lstrip("\ufeff"))
    reader = csv.DictReader(stream)
    rows = [dict(row) for row in reader if row and any((v or "").strip() for v in row.values())]
    return rows


def ingest_rows(
    session: Session,
    rows: list[dict[str, str]],
    *,
    season: int,
    league_code: str = "E0",
) -> dict:
    run = SyncRun(
        run_type="football-data-history",
        provider=PROVIDER,
        status="running",
        received=len(rows),
    )
    session.add(run)
    session.flush()

    league = _league(session, season=season, league_code=league_code)
    inserted = updated = rejected = 0
    errors: list[str] = []

    for raw in rows:
        try:
            home_name = _required(raw, "HomeTeam")
            away_name = _required(raw, "AwayTeam")
            if home_name.casefold() == away_name.casefold():
                raise FootballDataValidationError("home and away team cannot be the same")

            kickoff = _parse_kickoff(raw)
            home_goals = _int_or_none(raw.get("FTHG"))
            away_goals = _int_or_none(raw.get("FTAG"))
            if home_goals is None or away_goals is None:
                raise FootballDataValidationError("finished match requires FTHG and FTAG")

            home = _team(session, home_name)
            away = _team(session, away_name)
            provider_fixture_id = _fixture_id(
                season=season,
                league_code=league_code,
                kickoff=kickoff,
                home=home_name,
                away=away_name,
            )

            fixture = session.scalar(
                select(Fixture).where(
                    Fixture.provider == PROVIDER,
                    Fixture.provider_fixture_id == provider_fixture_id,
                )
            )
            was_inserted = fixture is None
            if fixture is None:
                fixture = Fixture(
                    provider=PROVIDER,
                    provider_fixture_id=provider_fixture_id,
                    league_season_id=league.id,
                    home_team_id=home.id,
                    away_team_id=away.id,
                    kickoff_utc=kickoff,
                    timezone="UTC",
                    status_short="FT",
                    raw_json="{}",
                )
                session.add(fixture)

            fixture.league_season_id = league.id
            fixture.home_team_id = home.id
            fixture.away_team_id = away.id
            fixture.kickoff_utc = kickoff
            fixture.timezone = "UTC"
            fixture.round_name = None
            fixture.referee = (raw.get("Referee") or "").strip() or None
            fixture.status_short = "FT"
            fixture.status_long = "Finished"
            fixture.home_goals = home_goals
            fixture.away_goals = away_goals
            fixture.halftime_home = _int_or_none(raw.get("HTHG"))
            fixture.halftime_away = _int_or_none(raw.get("HTAG"))
            fixture.fulltime_home = home_goals
            fixture.fulltime_away = away_goals
            fixture.is_finished = True
            fixture.result_1x2 = _result_1x2(raw, home_goals, away_goals)
            fixture.raw_json = json.dumps(raw, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
            fixture.provider_updated_at = datetime.now(timezone.utc)

            inserted += int(was_inserted)
            updated += int(not was_inserted)
        except FootballDataValidationError as exc:
            rejected += 1
            errors.append(str(exc))

    run.inserted = inserted
    run.updated = updated
    run.rejected = rejected
    run.status = "success" if not errors else "partial"
    run.finished_at = datetime.now(timezone.utc)
    run.error_message = " | ".join(errors[:10]) if errors else None
    session.commit()

    return {
        "received": len(rows),
        "inserted": inserted,
        "updated": updated,
        "rejected": rejected,
        "errors": errors,
        "season": season,
        "league_code": league_code,
        "provider": PROVIDER,
        "status": run.status,
    }


async def sync_football_data_history(
    session: Session,
    client: FootballDataClient,
    *,
    season: int,
    league_code: str = "E0",
) -> dict:
    response = await client.get_csv(season=season, league_code=league_code)
    rows = parse_csv(response.text)
    result = ingest_rows(session, rows, season=season, league_code=league_code)
    result["source_url"] = response.url
    return result

def sync_football_data_history_file(
    session: Session,
    *,
    file_path: str,
    season: int,
    league_code: str = "E0",
) -> dict:
    from pathlib import Path

    path = Path(file_path).expanduser().resolve()
    if not path.exists() or not path.is_file():
        raise FileNotFoundError(f"Historical CSV file not found: {path}")

    raw = path.read_bytes()

    # Football-Data historical CSVs can be Windows-1252. Prefer utf-8-sig,
    # then fall back to cp1252 so older seasons and accented text remain usable.
    try:
        text = raw.decode("utf-8-sig")
        encoding = "utf-8-sig"
    except UnicodeDecodeError:
        text = raw.decode("cp1252")
        encoding = "cp1252"

    rows = parse_csv(text)
    result = ingest_rows(session, rows, season=season, league_code=league_code)
    result["source_url"] = path.as_uri()
    result["source_file"] = str(path)
    result["source_encoding"] = encoding
    return result

