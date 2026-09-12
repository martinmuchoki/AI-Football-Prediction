
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import inspect, text

from app.db import SessionLocal, engine
from app.main import app
from app.services.sportsq_intelligence import list_sportsq_intelligence
from app.services.sportsq_news_impact import (
    TABLE_NAME,
    _parse_dt,
    compute_news_impact_from_events,
    news_impact_status,
    normalise_api_football_injuries,
    normalise_api_football_lineups,
)

UTC = timezone.utc
NOW = datetime(2026, 9, 11, 12, 0, tzinfo=UTC)
KICKOFF = datetime(2026, 9, 12, 14, 0, tzinfo=UTC)


def _event(
    *,
    team_id,
    team_name,
    player_id,
    player_name,
    status,
    observed_at=None,
    verified=True,
    source_id="api-football",
):
    return {
        "internal_team_id": team_id,
        "team_name": team_name,
        "provider_player_id": player_id,
        "player_name": player_name,
        "availability_status": status,
        "observed_at": observed_at or NOW,
        "valid_until_at": KICKOFF,
        "source_verified": verified,
        "verification_state": "VERIFIED" if verified else "UNVERIFIED",
        "source_id": source_id,
    }


def test_sqlite_text_datetime_is_parsed_to_utc():
    parsed = _parse_dt("2026-09-12 14:00:00")

    assert parsed is not None
    assert parsed.tzinfo is not None
    assert parsed.utcoffset() == timedelta(0)
    assert parsed.year == 2026
    assert parsed.month == 9
    assert parsed.day == 12
    assert parsed.hour == 14


def test_api_football_injury_parser():
    payload = {
        "response": [
            {
                "player": {
                    "id": 1,
                    "name": "Player One",
                    "type": "Missing Fixture",
                    "reason": "Ankle Injury",
                },
                "team": {"id": 33, "name": "Manchester United"},
                "fixture": {
                    "id": 1208021,
                    "date": "2024-08-16T19:00:00+00:00",
                },
                "league": {"id": 39, "season": 2024},
            },
            {
                "player": {
                    "id": 2,
                    "name": "Player Two",
                    "type": "Questionable",
                    "reason": "Injury",
                },
                "team": {"id": 33, "name": "Manchester United"},
                "fixture": {
                    "id": 1208021,
                    "date": "2024-08-16T19:00:00+00:00",
                },
                "league": {"id": 39, "season": 2024},
            },
        ]
    }

    rows = normalise_api_football_injuries(payload, observed_at=NOW)

    assert len(rows) == 2
    assert rows[0]["availability_status"] == "MISSING_FIXTURE"
    assert rows[1]["availability_status"] == "QUESTIONABLE"
    assert rows[0]["provider_fixture_id"] == 1208021


def test_api_football_lineup_parser():
    payload = {
        "response": [
            {
                "team": {"id": 33, "name": "Manchester United"},
                "formation": "4-2-3-1",
                "startXI": [
                    {
                        "player": {
                            "id": 526,
                            "name": "A. Onana",
                            "number": 24,
                            "pos": "G",
                            "grid": "1:1",
                        }
                    }
                ],
                "substitutes": [
                    {
                        "player": {
                            "id": 284324,
                            "name": "A. Garnacho",
                            "number": 17,
                            "pos": "F",
                            "grid": None,
                        }
                    }
                ],
            }
        ]
    }

    rows = normalise_api_football_lineups(
        payload,
        provider_fixture_id=1208021,
        fixture_kickoff_at=KICKOFF,
        observed_at=NOW,
    )

    assert len(rows) == 2
    assert rows[0]["availability_status"] == "STARTING"
    assert rows[0]["lineup_role"] == "STARTING"
    assert rows[1]["availability_status"] == "BENCH"
    assert rows[1]["lineup_role"] == "SUBSTITUTE"


def test_no_events_preserves_no_fabrication():
    result = compute_news_impact_from_events(
        [],
        home_team_id=1,
        away_team_id=2,
        home_team_name="Home",
        away_team_name="Away",
        kickoff_at=KICKOFF,
        cutoff_at=NOW,
    )

    assert result["status"] == "NO_STRUCTURED_TEAM_NEWS_INPUT"
    assert result["impact_score"] is None
    assert result["verified"] is False
    assert result["fabricated"] is False


def test_verified_availability_burden():
    events = [
        _event(
            team_id=1,
            team_name="Home",
            player_id=10,
            player_name="Home Player",
            status="MISSING_FIXTURE",
        ),
        _event(
            team_id=2,
            team_name="Away",
            player_id=20,
            player_name="Away Player",
            status="QUESTIONABLE",
        ),
    ]

    result = compute_news_impact_from_events(
        events,
        home_team_id=1,
        away_team_id=2,
        home_team_name="Home",
        away_team_name="Away",
        kickoff_at=KICKOFF,
        cutoff_at=NOW,
    )

    assert result["verified"] is True
    assert result["status"] == "VERIFIED_TEAM_NEWS"
    assert result["home"]["availability_burden"] == 20.0
    assert result["away"]["availability_burden"] == 7.0
    assert result["impact_score"] == 13.0
    assert result["direction"] == "HOME_NEGATIVE"


def test_unverified_event_blocks_score():
    result = compute_news_impact_from_events(
        [
            _event(
                team_id=1,
                team_name="Home",
                player_id=10,
                player_name="P",
                status="MISSING_FIXTURE",
                verified=False,
            )
        ],
        home_team_id=1,
        away_team_id=2,
        home_team_name="Home",
        away_team_name="Away",
        kickoff_at=KICKOFF,
        cutoff_at=NOW,
    )

    assert result["status"] == "UNVERIFIED_TEAM_NEWS"
    assert result["impact_score"] is None
    assert result["verified"] is False


def test_conflicted_verified_sources_fail_closed():
    events = [
        _event(
            team_id=1,
            team_name="Home",
            player_id=10,
            player_name="P",
            status="MISSING_FIXTURE",
            source_id="official-club",
        ),
        _event(
            team_id=1,
            team_name="Home",
            player_id=10,
            player_name="P",
            status="STARTING",
            source_id="api-football",
        ),
    ]

    result = compute_news_impact_from_events(
        events,
        home_team_id=1,
        away_team_id=2,
        home_team_name="Home",
        away_team_name="Away",
        kickoff_at=KICKOFF,
        cutoff_at=NOW,
    )

    assert result["status"] == "CONFLICTED_TEAM_NEWS"
    assert result["impact_score"] is None
    assert result["verified"] is False


def test_future_observation_is_excluded():
    result = compute_news_impact_from_events(
        [
            _event(
                team_id=1,
                team_name="Home",
                player_id=10,
                player_name="P",
                status="MISSING_FIXTURE",
                observed_at=NOW + timedelta(hours=3),
            )
        ],
        home_team_id=1,
        away_team_id=2,
        home_team_name="Home",
        away_team_name="Away",
        kickoff_at=KICKOFF,
        cutoff_at=NOW,
    )

    assert result["status"] == "NO_STRUCTURED_TEAM_NEWS_INPUT"
    assert result["future_events_excluded"] == 1


def test_stage6_schema_status_and_live_gate():
    with SessionLocal() as session:
        status = news_impact_status(session)

    assert status["table_ready"] is True
    assert status["table"] == TABLE_NAME
    assert status["historical_provider_schema_validated"] is True
    assert status["injury_adapter_schema_validated"] is True
    assert status["lineup_adapter_schema_validated"] is True
    assert status["live_2026_ingestion_enabled"] is False
    assert status["strict_temporal_cutoff"] is True
    assert status["fail_closed_verification"] is True
    assert status["event_rows"] == 0


def test_stage6_event_table_is_append_only():
    assert TABLE_NAME in set(inspect(engine).get_table_names())

    if engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            triggers = {
                row[0]
                for row in conn.execute(
                    text(
                        """
                        SELECT name
                        FROM sqlite_master
                        WHERE type = 'trigger'
                          AND tbl_name = 'sportsq_team_news_events'
                        """
                    )
                ).all()
            }

        assert "trg_sportsq_team_news_events_no_update" in triggers
        assert "trg_sportsq_team_news_events_no_delete" in triggers


def test_current_live_intelligence_still_does_not_fabricate():
    with SessionLocal() as session:
        payload = list_sportsq_intelligence(
            session,
            competition_id=2,
            season=2026,
            limit=5,
        )

    assert payload["items"]

    for item in payload["items"]:
        news = item["sportsq_news_impact"]
        assert news["status"] == "NO_STRUCTURED_TEAM_NEWS_INPUT"
        assert news["impact_score"] is None
        assert news["verified"] is False


def test_stage6_routes_are_read_only():
    required = {
        "/api/v1/sportsq/news-impact/status",
        "/api/v1/sportsq/news-impact/events",
        "/api/v1/sportsq/news-impact/{fixture_id}",
    }

    route_map = {
        route.path: set(getattr(route, "methods", set()) or set())
        for route in app.routes
    }

    assert required.issubset(route_map)

    for path in required:
        methods = route_map[path]
        assert "GET" in methods
        assert not ({"POST", "PUT", "PATCH", "DELETE"} & methods)

    route_order = [
        route.path
        for route in app.routes
    ]

    dynamic_route = "/api/v1/sportsq/news-impact/{fixture_id}"

    assert route_order.index(
        "/api/v1/sportsq/news-impact/status"
    ) < route_order.index(dynamic_route)

    assert route_order.index(
        "/api/v1/sportsq/news-impact/events"
    ) < route_order.index(dynamic_route)
