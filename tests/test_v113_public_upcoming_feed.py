"""Regression tests for the MDRN SportsQ public upcoming-fixture feed.

Public Match Intelligence must:
- preserve the immutable publication decision;
- exclude historical/already-kicked-off fixtures;
- exclude malformed/missing kickoff timestamps;
- sort eligible fixtures by nearest future kickoff;
- leave the protected/internal intelligence route unchanged.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _item(
    *,
    home: str,
    away: str,
    kickoff,
    publish: bool,
):
    return {
        "fixture": {
            "home_team": home,
            "away_team": away,
            "kickoff_utc": kickoff,
        },
        "prediction_lock": {
            "publish": publish,
        },
        "sportsq_predict": {
            "prediction": "HOME",
            "status": "AVAILABLE",
        },
        "sportsq_score_call": {
            "score": "2-1",
            "status": "AVAILABLE",
        },
        "sportsq_confidence": {
            "percent": 70.0,
            "band": "HIGH",
            "status": "AVAILABLE",
        },
        "sportsq_form_index": {
            "window": 5,
            "home": {
                "team": home,
                "index": 60.0,
                "band": "STRONG",
            },
            "away": {
                "team": away,
                "index": 50.0,
                "band": "MIXED",
            },
        },
        "sportsq_news_impact": {
            "status": "NO_STRUCTURED_TEAM_NEWS_INPUT",
            "direction": "UNVERIFIED",
            "verified": False,
            "note": "No verified structured team-news input.",
        },
    }


def _mock_result(items):
    return {
        "status": "success",
        "items": items,
    }


def test_public_feed_excludes_past_and_unpublished_fixtures():
    now = datetime.now(timezone.utc)

    items = [
        _item(
            home="Past FC",
            away="History FC",
            kickoff=(now - timedelta(days=1)).isoformat(),
            publish=True,
        ),
        _item(
            home="Private FC",
            away="Locked FC",
            kickoff=(now + timedelta(days=1)).isoformat(),
            publish=False,
        ),
        _item(
            home="Future FC",
            away="Upcoming FC",
            kickoff=(now + timedelta(days=2)).isoformat(),
            publish=True,
        ),
    ]

    with patch(
        "app.main.list_sportsq_intelligence",
        return_value=_mock_result(items),
    ):
        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=2&season=2026&limit=40"
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["status"] == "success"
    assert payload["count"] == 1

    assert payload["items"][0]["fixture"]["home_team"] == "Future FC"
    assert payload["items"][0]["fixture"]["away_team"] == "Upcoming FC"


def test_public_feed_orders_upcoming_fixtures_nearest_first():
    now = datetime.now(timezone.utc)

    items = [
        _item(
            home="Third FC",
            away="C FC",
            kickoff=(now + timedelta(days=3)).isoformat(),
            publish=True,
        ),
        _item(
            home="First FC",
            away="A FC",
            kickoff=(now + timedelta(hours=3)).isoformat(),
            publish=True,
        ),
        _item(
            home="Second FC",
            away="B FC",
            kickoff=(now + timedelta(days=1)).isoformat(),
            publish=True,
        ),
    ]

    with patch(
        "app.main.list_sportsq_intelligence",
        return_value=_mock_result(items),
    ):
        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=2&season=2026&limit=40"
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["count"] == 3

    homes = [
        item["fixture"]["home_team"]
        for item in payload["items"]
    ]

    assert homes == [
        "First FC",
        "Second FC",
        "Third FC",
    ]


def test_public_feed_accepts_naive_utc_database_timestamp():
    now = datetime.now(timezone.utc)

    future_naive = (
        now + timedelta(days=1)
    ).replace(tzinfo=None)

    kickoff = future_naive.strftime(
        "%Y-%m-%d %H:%M:%S.%f"
    )

    items = [
        _item(
            home="Naive UTC FC",
            away="Database FC",
            kickoff=kickoff,
            publish=True,
        ),
    ]

    with patch(
        "app.main.list_sportsq_intelligence",
        return_value=_mock_result(items),
    ):
        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=2&season=2026"
        )

    assert response.status_code == 200
    assert response.json()["count"] == 1


def test_public_feed_excludes_missing_or_invalid_kickoff():
    items = [
        _item(
            home="Missing FC",
            away="No Date FC",
            kickoff=None,
            publish=True,
        ),
        _item(
            home="Broken FC",
            away="Bad Date FC",
            kickoff="not-a-date",
            publish=True,
        ),
    ]

    with patch(
        "app.main.list_sportsq_intelligence",
        return_value=_mock_result(items),
    ):
        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=2&season=2026"
        )

    assert response.status_code == 200
    assert response.json()["count"] == 0


def test_public_feed_does_not_expose_prediction_lock():
    now = datetime.now(timezone.utc)

    items = [
        _item(
            home="Safe FC",
            away="Public FC",
            kickoff=(now + timedelta(days=1)).isoformat(),
            publish=True,
        ),
    ]

    with patch(
        "app.main.list_sportsq_intelligence",
        return_value=_mock_result(items),
    ):
        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=2&season=2026"
        )

    payload = response.json()

    assert payload["count"] == 1
    assert "prediction_lock" not in payload["items"][0]
