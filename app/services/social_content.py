from __future__ import annotations

import json
import shutil
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.services.prediction_grading import live_prediction_rows


CANVAS_SIZE = (1080, 1080)


def _resolve_ffmpeg() -> str | None:
    system = shutil.which("ffmpeg")
    if system:
        return system

    try:
        import imageio_ffmpeg
        bundled = imageio_ffmpeg.get_ffmpeg_exe()
        if bundled and Path(bundled).exists():
            return str(bundled)
    except Exception:
        pass

    return None


def _font(size: int, *, bold: bool = False):
    candidates = []
    if bold:
        candidates += [
            "C:/Windows/Fonts/arialbd.ttf",
            "C:/Windows/Fonts/calibrib.ttf",
            "DejaVuSans-Bold.ttf",
        ]
    else:
        candidates += [
            "C:/Windows/Fonts/arial.ttf",
            "C:/Windows/Fonts/calibri.ttf",
            "DejaVuSans.ttf",
        ]
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size=size)
        except Exception:
            continue
    return ImageFont.load_default()


def _utc(value: str) -> datetime:
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _prediction_name(code: str, home: str, away: str) -> str:
    return {"H": home, "D": "DRAW", "A": away}[code]


def _safe_name(text: str) -> str:
    keep = []
    for ch in text:
        if ch.isalnum():
            keep.append(ch.lower())
        elif ch in (" ", "-", "_"):
            keep.append("-")
    value = "".join(keep)
    while "--" in value:
        value = value.replace("--", "-")
    return value.strip("-") or "prediction"


def render_prediction_card(row: dict[str, Any], output_path: str | Path) -> Path:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)

    home = row["home"]
    away = row["away"]
    confidence = float(row["confidence"])
    prediction = _prediction_name(row["prediction"], home, away)
    kickoff = _utc(row["kickoff_utc"])

    image = Image.new("RGB", CANVAS_SIZE, (247, 241, 218))
    draw = ImageDraw.Draw(image)

    # Header and footer panels.
    draw.rounded_rectangle((60, 55, 1020, 200), radius=30, fill=(22, 93, 67))
    draw.rounded_rectangle((60, 890, 1020, 1025), radius=30, fill=(22, 93, 67))

    title_font = _font(54, bold=True)
    label_font = _font(34, bold=True)
    team_font = _font(58, bold=True)
    body_font = _font(36)
    big_font = _font(92, bold=True)
    small_font = _font(27)

    draw.text((95, 92), "AI FOOTBALL PREDICTION", font=title_font, fill=(255, 255, 255))

    draw.text((90, 260), home, font=team_font, fill=(25, 25, 25))
    draw.text((90, 340), "VS", font=body_font, fill=(90, 90, 90))
    draw.text((90, 400), away, font=team_font, fill=(25, 25, 25))

    draw.text((90, 535), "HIGH CONFIDENCE", font=label_font, fill=(22, 93, 67))
    draw.text((90, 600), prediction.upper(), font=big_font, fill=(25, 25, 25))
    draw.text((90, 720), f"{confidence * 100:.1f}% confidence", font=body_font, fill=(65, 65, 65))
    draw.text(
        (90, 790),
        kickoff.strftime("%d %b %Y • %H:%M UTC"),
        font=small_font,
        fill=(65, 65, 65),
    )

    draw.text(
        (95, 920),
        "Pre-match probability • Locked before kickoff",
        font=small_font,
        fill=(255, 255, 255),
    )
    draw.text(
        (95, 965),
        "Historical performance is not a guarantee of future results.",
        font=_font(21),
        fill=(255, 255, 255),
    )

    image.save(output, quality=95)
    return output


def _caption(row: dict[str, Any]) -> str:
    home = row["home"]
    away = row["away"]
    prediction = _prediction_name(row["prediction"], home, away)
    confidence = float(row["confidence"]) * 100
    return (
        f"{home} vs {away}\n\n"
        f"High-confidence prediction: {prediction}\n"
        f"Model confidence: {confidence:.1f}%\n\n"
        "Prediction locked before kickoff using pre-match probabilities. "
        "Historical performance does not guarantee future results.\n\n"
        "#Football #FootballPrediction #PremierLeague #AIFootball"
    )


def _make_static_video(card_paths: list[Path], output_path: Path, seconds_per_card: int = 6) -> dict[str, Any]:
    ffmpeg = _resolve_ffmpeg()
    if not ffmpeg:
        return {"created": False, "reason": "ffmpeg_not_found", "output": None}

    if not card_paths:
        return {"created": False, "reason": "no_cards", "output": None}

    concat = output_path.parent / "_phase9_concat.txt"
    lines = []
    for path in card_paths:
        normalized = str(path.resolve()).replace("\\", "/").replace("'", r"'\''")
        lines.append(f"file '{normalized}'")
        lines.append(f"duration {int(seconds_per_card)}")
    normalized = str(card_paths[-1].resolve()).replace("\\", "/").replace("'", r"'\''")
    lines.append(f"file '{normalized}'")
    concat.write_text("\n".join(lines), encoding="utf-8")

    cmd = [
        ffmpeg,
        "-y",
        "-f", "concat",
        "-safe", "0",
        "-i", str(concat),
        "-vf", "fps=30,format=yuv420p",
        "-c:v", "libx264",
        "-movflags", "+faststart",
        str(output_path),
    ]
    run = subprocess.run(cmd, capture_output=True, text=True)
    try:
        concat.unlink()
    except OSError:
        pass

    if run.returncode != 0:
        return {
            "created": False,
            "reason": "ffmpeg_failed",
            "output": None,
            "error_tail": run.stderr[-1200:],
        }

    return {"created": True, "reason": None, "output": str(output_path)}


def generate_social_package(
    session: Session,
    *,
    competition_id: int,
    season: int,
    output_dir: str | Path,
    high_confidence_only: bool = True,
    make_video: bool = True,
) -> dict[str, Any]:
    output = Path(output_dir).expanduser().resolve()
    cards_dir = output / "cards"
    captions_dir = output / "captions"
    cards_dir.mkdir(parents=True, exist_ok=True)
    captions_dir.mkdir(parents=True, exist_ok=True)

    rows = live_prediction_rows(
        session,
        competition_id=competition_id,
        season=season,
        high_confidence_only=high_confidence_only,
    )

    generated = []
    card_paths = []

    for row in rows:
        if high_confidence_only and not row.get("publish"):
            continue

        stem = f"{row['fixture_id']}-{_safe_name(row['home'])}-vs-{_safe_name(row['away'])}"
        card_path = cards_dir / f"{stem}.png"
        caption_path = captions_dir / f"{stem}.txt"

        render_prediction_card(row, card_path)
        caption_path.write_text(_caption(row), encoding="utf-8")
        card_paths.append(card_path)

        generated.append({
            "fixture_id": row["fixture_id"],
            "home": row["home"],
            "away": row["away"],
            "prediction": row["prediction"],
            "confidence": row["confidence"],
            "card": str(card_path),
            "caption": str(caption_path),
        })

    video = {"created": False, "reason": "disabled", "output": None}
    if make_video:
        video = _make_static_video(card_paths, output / "phase9-high-confidence.mp4")

    manifest = {
        "status": "success",
        "competition_id": int(competition_id),
        "season": int(season),
        "high_confidence_only": bool(high_confidence_only),
        "items": len(generated),
        "generated": generated,
        "video": video,
        "output_dir": str(output),
        "final_holdout_touched": False,
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    manifest["manifest"] = str(manifest_path)
    return manifest
