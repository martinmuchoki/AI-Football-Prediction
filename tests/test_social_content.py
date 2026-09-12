from pathlib import Path
from app.services.social_content import render_prediction_card


def test_render_prediction_card(tmp_path: Path):
    output = tmp_path / "card.png"
    row = {
        "fixture_id": 1,
        "home": "Chelsea",
        "away": "Hull City",
        "kickoff_utc": "2026-09-12T14:00:00+00:00",
        "prediction": "H",
        "confidence": 0.751067,
        "publish": True,
    }
    result = render_prediction_card(row, output)
    assert result.exists()
    assert result.stat().st_size > 1000


def test_ffmpeg_resolver_returns_string_or_none():
    from app.services.social_content import _resolve_ffmpeg
    value = _resolve_ffmpeg()
    assert value is None or isinstance(value, str)
