from datetime import datetime, timezone

from sqlalchemy import select

from app.models import Fixture, FixtureReconciliation, LeagueSeason, Team
from app.services.fixture_reconciliation import (
    reconcile_openfootball_payload,
    reconciliation_issues,
    reconciliation_summary,
)


def add_live_fixture(
    session,
    *,
    fixture_id: int,
    home: str,
    away: str,
    date: str,
    finished: bool = False,
    score: tuple[int, int] | None = None,
):
    league = session.scalar(
        select(LeagueSeason).where(
            LeagueSeason.provider == "live-score-api",
            LeagueSeason.provider_league_id == 2,
            LeagueSeason.season == 2026,
        )
    )
    if league is None:
        league = LeagueSeason(
            provider="live-score-api",
            provider_league_id=2,
            season=2026,
            name="Premier League",
            country="England",
        )
        session.add(league)
        session.flush()

    def team(name: str, pid: int):
        row = session.scalar(
            select(Team).where(Team.provider == "live-score-api", Team.provider_team_id == pid)
        )
        if row is None:
            row = Team(provider="live-score-api", provider_team_id=pid, name=name)
            session.add(row)
            session.flush()
        return row

    h = team(home, fixture_id * 10 + 1)
    a = team(away, fixture_id * 10 + 2)
    dt = datetime.fromisoformat(date + "T14:00:00+00:00")
    row = Fixture(
        provider="live-score-api",
        provider_fixture_id=fixture_id,
        league_season_id=league.id,
        home_team_id=h.id,
        away_team_id=a.id,
        kickoff_utc=dt,
        status_short="FT" if finished else "NS",
        is_finished=finished,
        home_goals=score[0] if score else None,
        away_goals=score[1] if score else None,
        fulltime_home=score[0] if score else None,
        fulltime_away=score[1] if score else None,
        raw_json="{}",
    )
    session.add(row)
    session.commit()


def payload(matches):
    return {"name": "Premier League 2026/27", "matches": matches}


def test_reconciliation_exact_fixture_passes_quality_gate(session):
    add_live_fixture(session, fixture_id=1, home="Chelsea", away="Arsenal", date="2026-09-12")
    result = reconcile_openfootball_payload(
        session,
        payload=payload([{"date": "2026-09-12", "team1": "Chelsea", "team2": "Arsenal"}]),
        competition_id=2,
        season=2026,
    )
    assert result["quality_gate"] == "PASS"
    assert result["overall_counts"] == {"MATCH": 1}
    assert result["fixture_agreement_rate"] == 1.0
    assert reconciliation_issues(session, competition_id=2, season=2026) == []


def test_reconciliation_detects_date_conflict(session):
    add_live_fixture(session, fixture_id=2, home="Chelsea", away="Arsenal", date="2026-09-12")
    result = reconcile_openfootball_payload(
        session,
        payload=payload([{"date": "2026-09-13", "team1": "Chelsea", "team2": "Arsenal"}]),
        competition_id=2,
        season=2026,
    )
    assert result["quality_gate"] == "FAIL"
    issues = reconciliation_issues(session, competition_id=2, season=2026)
    assert issues[0]["date_status"] == "CONFLICT"
    assert issues[0]["overall_status"] == "CONFLICT"


def test_reconciliation_detects_finished_result_conflict(session):
    add_live_fixture(
        session,
        fixture_id=3,
        home="Chelsea",
        away="Arsenal",
        date="2026-09-12",
        finished=True,
        score=(2, 1),
    )
    result = reconcile_openfootball_payload(
        session,
        payload=payload([{
            "date": "2026-09-12",
            "team1": "Chelsea",
            "team2": "Arsenal",
            "score": {"ft": [1, 2]},
        }]),
        competition_id=2,
        season=2026,
    )
    assert result["quality_gate"] == "FAIL"
    issue = reconciliation_issues(session, competition_id=2, season=2026)[0]
    assert issue["result_status"] == "CONFLICT"
    assert issue["primary_score"] == "2-1"
    assert issue["secondary_score"] == "1-2"


def test_reconciliation_missing_secondary_is_warning(session):
    add_live_fixture(session, fixture_id=4, home="Chelsea", away="Arsenal", date="2026-09-12")
    result = reconcile_openfootball_payload(
        session,
        payload=payload([]),
        competition_id=2,
        season=2026,
    )
    assert result["quality_gate"] == "WARN"
    assert result["overall_counts"]["MISSING_SECONDARY"] == 1


def test_reconciliation_is_idempotent_and_summary_reads_persisted_state(session):
    add_live_fixture(session, fixture_id=5, home="Manchester Utd", away="Manchester City", date="2026-09-12")
    p = payload([{"date": "2026-09-12", "team1": "Manchester United", "team2": "Manchester City"}])
    first = reconcile_openfootball_payload(session, payload=p, competition_id=2, season=2026)
    second = reconcile_openfootball_payload(session, payload=p, competition_id=2, season=2026)
    assert first["inserted"] == 1
    assert second["inserted"] == 0
    assert second["updated"] == 1
    summary = reconciliation_summary(session, competition_id=2, season=2026)
    assert summary["quality_gate"] == "PASS"
    assert summary["total"] == 1
    assert len(session.scalars(select(FixtureReconciliation)).all()) == 1
