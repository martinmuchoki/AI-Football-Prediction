def fixture_payload(
    fixture_id=1001,
    *,
    status_short="NS",
    home_goals=None,
    away_goals=None,
    kickoff="2026-09-08T19:00:00+00:00",
):
    return {
        "fixture": {
            "id": fixture_id,
            "referee": None,
            "timezone": "UTC",
            "date": kickoff,
            "timestamp": 1788894000,
            "periods": {"first": None, "second": None},
            "venue": {"id": 10, "name": "Test Stadium", "city": "London"},
            "status": {"long": "Not Started" if status_short == "NS" else "Match Finished", "short": status_short, "elapsed": None},
        },
        "league": {
            "id": 39,
            "name": "Premier League",
            "country": "England",
            "logo": "https://example.test/league.png",
            "flag": "https://example.test/flag.svg",
            "season": 2026,
            "round": "Regular Season - 1",
        },
        "teams": {
            "home": {"id": 33, "name": "Home FC", "logo": "https://example.test/home.png", "winner": None},
            "away": {"id": 40, "name": "Away FC", "logo": "https://example.test/away.png", "winner": None},
        },
        "goals": {"home": home_goals, "away": away_goals},
        "score": {
            "halftime": {"home": None, "away": None},
            "fulltime": {"home": home_goals, "away": away_goals},
            "extratime": {"home": None, "away": None},
            "penalty": {"home": None, "away": None},
        },
    }
