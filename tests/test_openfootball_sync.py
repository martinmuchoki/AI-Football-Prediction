import json
from datetime import datetime, timezone

from app.providers.openfootball_json import OpenFootballDocument
from app.services.openfootball_sync import store_openfootball_document
from app.services.source_policy import seed_known_source_policies


def test_openfootball_snapshot_and_observations_are_idempotent(session):
    seed_known_source_policies(session)
    payload = {
        "name": "Premier League 2026/27",
        "matches": [
            {
                "round": "Matchday 1",
                "date": "2026-08-15",
                "team1": "Chelsea",
                "team2": "Arsenal",
                "score": {"ft": [2, 1]},
            }
        ],
    }
    raw = json.dumps(payload, sort_keys=True)
    doc = OpenFootballDocument(
        api_url="https://api.github.com/repos/openfootball/football.json/contents/2026-27/en.1.json?ref=master",
        payload=payload,
        raw_text=raw,
        response_etag='"abc"',
    )

    first = store_openfootball_document(
        session,
        document=doc,
        season_label="2026-27",
    )
    second = store_openfootball_document(
        session,
        document=doc,
        season_label="2026-27",
    )

    assert first["snapshot_inserted"] is True
    assert first["matches"] == 1
    assert first["observations_inserted"] == 5
    assert second["snapshot_inserted"] is False
    assert second["observations_inserted"] == 0
