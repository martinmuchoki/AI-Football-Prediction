from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from fastapi.testclient import TestClient

from app.main import app


client = TestClient(app)


def _item(home, kickoff, publish=True):
    return {
        "fixture": {
            "home_team": home,
            "away_team": "Away FC",
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
            "score": "1-0",
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
                "team": "Away FC",
                "index": 40.0,
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


def test_public_limit_is_applied_after_upcoming_publication_filter():
    now = datetime.now(timezone.utc)

    old = (now - timedelta(days=10)).isoformat()
    future_1 = (now + timedelta(days=1)).isoformat()
    future_2 = (now + timedelta(days=2)).isoformat()

    source = []

    # Simulate historical rows consuming the beginning of the
    # internal intelligence result set.
    for index in range(50):
        source.append(
            _item(
                f"Historical {index}",
                old,
                publish=(index % 2 == 0),
            )
        )

    source.extend(
        [
            _item(
                "Nearest Upcoming",
                future_1,
                True,
            ),
            _item(
                "Later Upcoming",
                future_2,
                True,
            ),
        ]
    )

    with patch(
        "app.main.list_sportsq_intelligence",
        return_value={
            "status": "success",
            "items": source,
        },
    ) as mocked:

        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=39&season=2026&limit=1"
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["count"] == 1
    assert (
        payload["items"][0]["fixture"]["home_team"]
        == "Nearest Upcoming"
    )

    kwargs = mocked.call_args.kwargs

    # Public endpoint must not pass the caller's small public limit
    # into the historical intelligence candidate retrieval.
    assert kwargs["limit"] == 1000


def test_public_limit_two_returns_two_nearest_qualifying_items():
    now = datetime.now(timezone.utc)

    source = [
        _item(
            "Third",
            (now + timedelta(days=3)).isoformat(),
            True,
        ),
        _item(
            "Rejected",
            (now + timedelta(hours=12)).isoformat(),
            False,
        ),
        _item(
            "Second",
            (now + timedelta(days=2)).isoformat(),
            True,
        ),
        _item(
            "First",
            (now + timedelta(days=1)).isoformat(),
            True,
        ),
    ]

    with patch(
        "app.main.list_sportsq_intelligence",
        return_value={
            "status": "success",
            "items": source,
        },
    ):
        response = client.get(
            "/api/v1/public/sportsq"
            "?competition=39&season=2026&limit=2"
        )

    assert response.status_code == 200

    payload = response.json()

    assert payload["count"] == 2

    homes = [
        item["fixture"]["home_team"]
        for item in payload["items"]
    ]

    assert homes == [
        "First",
        "Second",
    ]


def test_public_limit_validation_remains_bounded():
    low = client.get(
        "/api/v1/public/sportsq?limit=0"
    )
    high = client.get(
        "/api/v1/public/sportsq?limit=101"
    )

    assert low.status_code == 422
    assert high.status_code == 422
