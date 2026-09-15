from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw, ImageFont
from sqlalchemy.orm import Session

from app.services.social_content import _make_static_video, _resolve_ffmpeg
from app.services.sportsq_intelligence import (
    get_sportsq_intelligence,
    list_sportsq_intelligence,
    sportsq_accuracy_summary,
    sportsq_capabilities,
)

BRAND = "MDRN SportsQ"
THEME = {
    "navy": "#07111F",
    "navy_2": "#0D1C2E",
    "lime": "#C7FF2E",
    "white": "#FFFFFF",
    "muted": "#91A1B7",
}
ALLOWED_PLATFORMS = (
    "instagram",
    "facebook",
    "x",
    "youtube",
    "tiktok",
    "manual_export",
)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONTENT_ROOT = PROJECT_ROOT / "data" / "sportsq_stage3"


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime | None = None) -> str:
    return (value or _utc_now()).isoformat()


def _safe_stem(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned[:80] or "sportsq"


def _font(size: int, bold: bool = False) -> ImageFont.ImageFont:
    candidates = []
    if os.name == "nt":
        windir = Path(os.environ.get("WINDIR", r"C:\Windows"))
        candidates.extend([
            windir / "Fonts" / ("segoeuib.ttf" if bold else "segoeui.ttf"),
            windir / "Fonts" / ("arialbd.ttf" if bold else "arial.ttf"),
        ])

    candidates.extend([
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if bold else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf"),
    ])

    for path in candidates:
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except Exception:
                pass

    return ImageFont.load_default()


def _draw_text(
    draw: ImageDraw.ImageDraw,
    xy: tuple[int, int],
    text: str,
    *,
    font: ImageFont.ImageFont,
    fill: str,
    max_width: int,
    line_gap: int = 8,
) -> int:
    words = str(text).split()
    if not words:
        return xy[1]

    lines = []
    current = ""

    for word in words:
        candidate = word if not current else current + " " + word
        bbox = draw.textbbox((0, 0), candidate, font=font)
        width = bbox[2] - bbox[0]
        if width <= max_width or not current:
            current = candidate
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    x, y = xy
    for line in lines:
        draw.text((x, y), line, font=font, fill=fill)
        bbox = draw.textbbox((x, y), line, font=font)
        y = bbox[3] + line_gap

    return y


def _base_canvas(width: int, height: int) -> tuple[Image.Image, ImageDraw.ImageDraw]:
    image = Image.new("RGB", (width, height), THEME["navy"])
    draw = ImageDraw.Draw(image)

    draw.rectangle((0, 0, width, 18), fill=THEME["lime"])
    draw.rectangle((0, height - 14, width, height), fill=THEME["lime"])

    draw.text(
        (60, 56),
        BRAND,
        font=_font(34, bold=True),
        fill=THEME["white"],
    )
    draw.text(
        (60, 103),
        "FOOTBALL INTELLIGENCE",
        font=_font(18, bold=True),
        fill=THEME["lime"],
    )
    return image, draw


def _prediction_copy(item: dict[str, Any]) -> str:
    fixture = item.get("fixture") or {}
    predict = item.get("sportsq_predict") or {}
    confidence = item.get("sportsq_confidence") or {}
    score = item.get("sportsq_score_call") or {}

    home = fixture.get("home_team") or "Home"
    away = fixture.get("away_team") or "Away"
    selection = predict.get("prediction") or "UNAVAILABLE"
    confidence_pct = confidence.get("percent")
    score_call = score.get("score")

    parts = [
        f"{BRAND} prediction: {home} vs {away}.",
        f"SportsQ Predict: {selection}.",
    ]

    if confidence_pct is not None:
        parts.append(f"SportsQ Confidence: {confidence_pct:.1f}%.")

    if score_call:
        parts.append(f"SportsQ ScoreCall: {score_call}.")

    parts.append(
        "Prediction is based on the existing immutable pre-match lock; "
        "no post-kickoff information is used."
    )

    return " ".join(parts)


def render_prediction_card(
    item: dict[str, Any],
    output_path: str | Path,
    *,
    vertical: bool = False,
) -> Path:
    width, height = ((1080, 1920) if vertical else (1080, 1080))
    image, draw = _base_canvas(width, height)

    fixture = item.get("fixture") or {}
    predict = item.get("sportsq_predict") or {}
    confidence = item.get("sportsq_confidence") or {}
    score = item.get("sportsq_score_call") or {}
    form = item.get("sportsq_form_index") or {}

    home = fixture.get("home_team") or "HOME"
    away = fixture.get("away_team") or "AWAY"

    y = 190
    draw.text(
        (60, y),
        "MATCH PREDICTION",
        font=_font(24, bold=True),
        fill=THEME["lime"],
    )
    y += 70

    y = _draw_text(
        draw,
        (60, y),
        home,
        font=_font(54 if not vertical else 64, bold=True),
        fill=THEME["white"],
        max_width=width - 120,
    )
    draw.text(
        (60, y + 18),
        "VS",
        font=_font(22, bold=True),
        fill=THEME["muted"],
    )
    y += 70
    y = _draw_text(
        draw,
        (60, y),
        away,
        font=_font(54 if not vertical else 64, bold=True),
        fill=THEME["white"],
        max_width=width - 120,
    )

    y += 70
    draw.rounded_rectangle(
        (60, y, width - 60, y + 160),
        radius=30,
        fill=THEME["navy_2"],
        outline=THEME["lime"],
        width=3,
    )

    selection = predict.get("prediction") or "UNAVAILABLE"
    draw.text(
        (95, y + 28),
        "SPORTSQ PREDICT",
        font=_font(22, bold=True),
        fill=THEME["lime"],
    )
    draw.text(
        (95, y + 72),
        str(selection),
        font=_font(48, bold=True),
        fill=THEME["white"],
    )

    y += 210

    confidence_pct = confidence.get("percent")
    confidence_text = (
        f"{confidence_pct:.1f}%"
        if isinstance(confidence_pct, (int, float))
        else "UNAVAILABLE"
    )

    score_call = score.get("score") or "—"

    draw.text(
        (60, y),
        "SPORTSQ CONFIDENCE",
        font=_font(20, bold=True),
        fill=THEME["muted"],
    )
    draw.text(
        (60, y + 40),
        confidence_text,
        font=_font(42, bold=True),
        fill=THEME["white"],
    )

    draw.text(
        (width // 2 + 20, y),
        "SPORTSQ SCORECALL",
        font=_font(20, bold=True),
        fill=THEME["muted"],
    )
    draw.text(
        (width // 2 + 20, y + 40),
        str(score_call),
        font=_font(42, bold=True),
        fill=THEME["white"],
    )

    y += 150
    home_form = ((form.get("home") or {}).get("index"))
    away_form = ((form.get("away") or {}).get("index"))

    draw.text(
        (60, y),
        "SPORTSQ FORM INDEX",
        font=_font(20, bold=True),
        fill=THEME["lime"],
    )

    draw.text(
        (60, y + 45),
        f"{home}: {home_form if home_form is not None else '—'}",
        font=_font(26, bold=True),
        fill=THEME["white"],
    )
    draw.text(
        (60, y + 88),
        f"{away}: {away_form if away_form is not None else '—'}",
        font=_font(26, bold=True),
        fill=THEME["white"],
    )

    footer_y = height - 120
    draw.text(
        (60, footer_y),
        "PRE-MATCH • IMMUTABLE LOCK • NO LOOKAHEAD",
        font=_font(18, bold=True),
        fill=THEME["muted"],
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)
    return output


def render_accuracy_card(
    accuracy: dict[str, Any],
    output_path: str | Path,
) -> Path:
    width, height = 1080, 1080
    image, draw = _base_canvas(width, height)

    performance = accuracy.get("performance")
    if not isinstance(performance, dict):
        performance = {}

    y = 210
    draw.text(
        (60, y),
        "RESULTS & ACCURACY",
        font=_font(30, bold=True),
        fill=THEME["lime"],
    )
    y += 95

    candidate_accuracy = None
    for key in (
        "accuracy",
        "accuracy_pct",
        "accuracy_percent",
        "hit_rate",
        "correct_rate",
    ):
        value = performance.get(key)
        if isinstance(value, (int, float)):
            candidate_accuracy = float(value)
            if candidate_accuracy <= 1.0:
                candidate_accuracy *= 100.0
            break

    graded = None
    for key in ("graded", "graded_count", "total_graded", "sample_size"):
        value = performance.get(key)
        if isinstance(value, (int, float)):
            graded = int(value)
            break

    accuracy_text = (
        f"{candidate_accuracy:.1f}%"
        if candidate_accuracy is not None
        else "AWAITING GRADED SAMPLE"
    )

    draw.text(
        (60, y),
        "SPORTSQ ACCURACY",
        font=_font(22, bold=True),
        fill=THEME["muted"],
    )
    y += 50
    y = _draw_text(
        draw,
        (60, y),
        accuracy_text,
        font=_font(66, bold=True),
        fill=THEME["white"],
        max_width=960,
    )

    y += 70
    draw.text(
        (60, y),
        f"GRADED SAMPLE: {graded if graded is not None else 'N/A'}",
        font=_font(28, bold=True),
        fill=THEME["lime"],
    )

    y += 90
    draw.rounded_rectangle(
        (60, y, 1020, y + 230),
        radius=30,
        fill=THEME["navy_2"],
    )
    _draw_text(
        draw,
        (95, y + 42),
        (
            "Accuracy is calculated from completed fixtures against predictions "
            "that were locked before kickoff. Existing locked predictions are not "
            "rewritten after the result."
        ),
        font=_font(27),
        fill=THEME["white"],
        max_width=890,
        line_gap=12,
    )

    draw.text(
        (60, height - 120),
        "TRANSPARENT TRACK RECORD • LOCKED BEFORE KICKOFF",
        font=_font(18, bold=True),
        fill=THEME["muted"],
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)
    return output


def render_news_card(
    item: dict[str, Any],
    output_path: str | Path,
) -> dict[str, Any]:
    news = item.get("sportsq_news_impact") or {}
    if not news.get("verified"):
        return {
            "created": False,
            "reason": "NO_VERIFIED_STRUCTURED_TEAM_NEWS",
            "output": None,
        }

    width, height = 1080, 1080
    image, draw = _base_canvas(width, height)

    fixture = item.get("fixture") or {}
    home = fixture.get("home_team") or "Home"
    away = fixture.get("away_team") or "Away"

    draw.text(
        (60, 220),
        "BREAKING TEAM NEWS",
        font=_font(30, bold=True),
        fill=THEME["lime"],
    )

    _draw_text(
        draw,
        (60, 310),
        f"{home} vs {away}",
        font=_font(48, bold=True),
        fill=THEME["white"],
        max_width=960,
    )

    direction = news.get("direction") or "VERIFIED"
    impact = news.get("impact_score")

    draw.text(
        (60, 500),
        f"SPORTSQ NEWS IMPACT: {direction}",
        font=_font(30, bold=True),
        fill=THEME["white"],
    )
    draw.text(
        (60, 555),
        f"IMPACT SCORE: {impact if impact is not None else 'N/A'}",
        font=_font(28, bold=True),
        fill=THEME["lime"],
    )

    draw.text(
        (60, height - 120),
        "VERIFIED STRUCTURED TEAM-NEWS INPUT ONLY",
        font=_font(18, bold=True),
        fill=THEME["muted"],
    )

    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output, "PNG", optimize=True)

    return {
        "created": True,
        "reason": "VERIFIED",
        "output": str(output),
    }


def _content_root(value: str | Path | None = None) -> Path:
    return Path(value).resolve() if value else DEFAULT_CONTENT_ROOT.resolve()


def platform_readiness() -> dict[str, Any]:
    requirements = {
        "instagram": (
            "SPORTSQ_META_ACCESS_TOKEN",
            "SPORTSQ_META_INSTAGRAM_ACCOUNT_ID",
        ),
        "facebook": (
            "SPORTSQ_META_ACCESS_TOKEN",
            "SPORTSQ_META_FACEBOOK_PAGE_ID",
        ),
        "x": (
            "SPORTSQ_X_API_KEY",
            "SPORTSQ_X_API_SECRET",
            "SPORTSQ_X_ACCESS_TOKEN",
            "SPORTSQ_X_ACCESS_TOKEN_SECRET",
        ),
        "youtube": (
            "SPORTSQ_YOUTUBE_CLIENT_ID",
            "SPORTSQ_YOUTUBE_CLIENT_SECRET",
            "SPORTSQ_YOUTUBE_REFRESH_TOKEN",
        ),
        "tiktok": (
            "SPORTSQ_TIKTOK_ACCESS_TOKEN",
            "SPORTSQ_TIKTOK_OPEN_ID",
        ),
    }

    result = {}

    for platform, names in requirements.items():
        configured = all(bool(os.getenv(name, "").strip()) for name in names)
        result[platform] = {
            "config_present": configured,
            "live_posting_verified": False,
            "status": (
                "CONFIG_PRESENT_CONNECTOR_REQUIRES_VERIFICATION"
                if configured
                else "NOT_CONFIGURED"
            ),
            "required_variable_names": list(names),
            "secret_values_exposed": False,
        }

    result["manual_export"] = {
        "config_present": True,
        "live_posting_verified": True,
        "status": "READY",
        "required_variable_names": [],
        "secret_values_exposed": False,
    }

    return result


def stage3_status(session: Session) -> dict[str, Any]:
    capabilities = sportsq_capabilities(session)
    return {
        "status": "success",
        "brand": BRAND,
        "release": "1.0.0-candidate-stage3",
        "theme": THEME,
        "content_engine": {
            "prediction_card": True,
            "results_accuracy_card": True,
            "vertical_story_reel": True,
            "static_video": _resolve_ffmpeg() is not None,
            "breaking_team_news": (
                capabilities.get("sportsq_news_impact", {}).get("status")
                != "NO_STRUCTURED_TEAM_NEWS_INPUT"
            ),
            "third_party_goal_clips": False,
            "clip_policy": (
                "No third-party clip is inserted unless rights/licence metadata "
                "is explicitly verified. Attribution alone is not treated as a licence."
            ),
        },
        "gui": {
            "dashboard": True,
            "content_studio": True,
            "publishing_queue": True,
            "accuracy_view": True,
        },
        "publishing": {
            "queue": True,
            "approval_required": True,
            "scheduling_metadata": True,
            "platforms": platform_readiness(),
            "live_external_posting_claimed": False,
        },
        "prediction_safety": capabilities.get("safety"),
    }


def content_preview(
    session: Session,
    *,
    competition_id: int = 2,
    season: int = 2026,
    limit: int = 5,
) -> dict[str, Any]:
    intelligence = list_sportsq_intelligence(
        session,
        competition_id=competition_id,
        season=season,
        limit=max(1, min(int(limit), 20)),
    )
    accuracy = sportsq_accuracy_summary(
        session,
        competition_id=competition_id,
        season=season,
    )

    return {
        "status": "success",
        "brand": BRAND,
        "templates": [
            "MATCH_PREDICTION",
            "RESULTS_ACCURACY",
            "BREAKING_TEAM_NEWS_VERIFIED_ONLY",
            "VERTICAL_STORY_REEL",
        ],
        "predictions": intelligence.get("items", []),
        "accuracy": accuracy,
        "news_policy": "VERIFIED_STRUCTURED_INPUT_ONLY",
        "prediction_engine_rewritten": False,
        "historical_holdout_touched": False,
    }


def generate_content_package(
    session: Session,
    *,
    competition_id: int = 2,
    season: int = 2026,
    limit: int = 3,
    content_root: str | Path | None = None,
) -> dict[str, Any]:
    root = _content_root(content_root)
    packages = root / "packages"
    packages.mkdir(parents=True, exist_ok=True)

    package_id = (
        _utc_now().strftime("%Y%m%dT%H%M%SZ")
        + "-"
        + uuid.uuid4().hex[:8]
    )
    package_dir = packages / package_id
    package_dir.mkdir(parents=True, exist_ok=False)

    preview = content_preview(
        session,
        competition_id=competition_id,
        season=season,
        limit=limit,
    )

    assets = []
    vertical_cards: list[Path] = []
    news_skips = []

    for index, item in enumerate(preview["predictions"], start=1):
        fixture = item.get("fixture") or {}
        stem = _safe_stem(
            f"{fixture.get('fixture_id') or index}-"
            f"{fixture.get('home_team') or 'home'}-vs-"
            f"{fixture.get('away_team') or 'away'}"
        )

        square = package_dir / f"{stem}-prediction-square.png"
        vertical = package_dir / f"{stem}-story.png"
        caption = package_dir / f"{stem}-caption.txt"
        article = package_dir / f"{stem}-article.txt"

        render_prediction_card(item, square, vertical=False)
        render_prediction_card(item, vertical, vertical=True)

        copy = _prediction_copy(item)
        caption.write_text(
            copy + "\n\n#MDRNSportsQ #SportsQPredict #Football",
            encoding="utf-8",
        )
        article.write_text(copy, encoding="utf-8")

        vertical_cards.append(vertical)

        assets.extend([
            {
                "kind": "MATCH_PREDICTION",
                "format": "square",
                "filename": square.name,
                "media_type": "image/png",
            },
            {
                "kind": "VERTICAL_STORY_REEL",
                "format": "vertical",
                "filename": vertical.name,
                "media_type": "image/png",
            },
            {
                "kind": "CAPTION",
                "filename": caption.name,
                "media_type": "text/plain",
            },
            {
                "kind": "ARTICLE",
                "filename": article.name,
                "media_type": "text/plain",
            },
        ])

        news_path = package_dir / f"{stem}-breaking-news.png"
        news_result = render_news_card(item, news_path)

        if news_result["created"]:
            assets.append({
                "kind": "BREAKING_TEAM_NEWS",
                "filename": news_path.name,
                "media_type": "image/png",
            })
        else:
            news_skips.append({
                "fixture_id": fixture.get("fixture_id"),
                "reason": news_result["reason"],
            })

    accuracy_path = package_dir / "results-accuracy.png"
    render_accuracy_card(preview["accuracy"], accuracy_path)
    assets.append({
        "kind": "RESULTS_ACCURACY",
        "filename": accuracy_path.name,
        "media_type": "image/png",
    })

    video_result = {
        "created": False,
        "reason": "no_vertical_prediction_cards",
        "output": None,
    }

    if vertical_cards:
        video_path = package_dir / "sportsq-static-story.mp4"
        video_result = _make_static_video(
            vertical_cards,
            video_path,
            seconds_per_card=6,
        )
        if video_result.get("created"):
            assets.append({
                "kind": "STATIC_STORY_VIDEO",
                "filename": video_path.name,
                "media_type": "video/mp4",
                "motion_policy": "STATIC_CARDS_ONLY",
            })

    manifest = {
        "package_id": package_id,
        "brand": BRAND,
        "created_at": _iso(),
        "competition_id": int(competition_id),
        "season": int(season),
        "prediction_count": len(preview["predictions"]),
        "assets": assets,
        "static_video": video_result,
        "breaking_news_skips": news_skips,
        "rights": {
            "third_party_clips_used": False,
            "licensed_clip_metadata_required": True,
            "attribution_is_not_license": True,
            "unverified_clip_insertion_allowed": False,
        },
        "safety": {
            "prediction_engine_rewritten": False,
            "prediction_rows_modified": False,
            "prediction_locks_modified": False,
            "historical_holdout_touched": False,
            "post_kickoff_features_used": False,
        },
    }

    manifest_path = package_dir / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    manifest["manifest_filename"] = manifest_path.name
    return manifest


def _queue_file(root: Path) -> Path:
    return root / "publishing_queue.json"


def _read_queue(root: Path) -> list[dict[str, Any]]:
    queue_file = _queue_file(root)
    if not queue_file.exists():
        return []

    try:
        data = json.loads(queue_file.read_text(encoding="utf-8"))
    except Exception:
        return []

    return data if isinstance(data, list) else []


def _write_queue(root: Path, rows: list[dict[str, Any]]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    queue_file = _queue_file(root)
    temp = queue_file.with_suffix(".tmp")
    temp.write_text(
        json.dumps(rows, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    temp.replace(queue_file)


def list_queue(
    *,
    content_root: str | Path | None = None,
) -> list[dict[str, Any]]:
    return _read_queue(_content_root(content_root))


def _package_manifest(root: Path, package_id: str) -> dict[str, Any]:
    safe_id = _safe_stem(package_id)
    if safe_id != package_id:
        raise ValueError("Invalid package_id")

    manifest_path = root / "packages" / safe_id / "manifest.json"
    if not manifest_path.exists():
        raise FileNotFoundError(f"Package not found: {package_id}")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("package_id") != package_id:
        raise ValueError("Package manifest identity mismatch")

    return manifest


def enqueue_package(
    package_id: str,
    *,
    platforms: list[str],
    scheduled_for: str | None = None,
    content_root: str | Path | None = None,
) -> dict[str, Any]:
    root = _content_root(content_root)
    manifest = _package_manifest(root, package_id)

    requested = []
    for platform in platforms:
        value = str(platform).strip().lower()
        if value not in ALLOWED_PLATFORMS:
            raise ValueError(f"Unsupported platform: {value}")
        if value not in requested:
            requested.append(value)

    if not requested:
        requested = ["manual_export"]

    queue = _read_queue(root)

    # Idempotency guard:
    # Re-enqueuing the same immutable package for the same platform set and
    # schedule returns the existing queue item instead of creating duplicates.
    requested_key = sorted(requested)
    for existing in queue:
        existing_platforms = sorted(
            str(value).strip().lower()
            for value in (existing.get("platforms") or [])
        )
        if (
            existing.get("package_id") == package_id
            and existing_platforms == requested_key
            and existing.get("scheduled_for") == scheduled_for
        ):
            return existing

    item = {
        "queue_id": uuid.uuid4().hex,
        "package_id": package_id,
        "created_at": _iso(),
        "scheduled_for": scheduled_for,
        "platforms": requested,
        "status": "DRAFT",
        "approved_at": None,
        "completed_at": None,
        "last_action": "ENQUEUED",
        "platform_results": {},
        "rights": manifest.get("rights"),
    }

    queue.append(item)
    _write_queue(root, queue)
    return item


def _mutate_queue_item(
    queue_id: str,
    *,
    content_root: str | Path | None,
    mutate,
) -> dict[str, Any]:
    root = _content_root(content_root)
    queue = _read_queue(root)

    for index, row in enumerate(queue):
        if row.get("queue_id") != queue_id:
            continue

        updated = mutate(dict(row))
        queue[index] = updated
        _write_queue(root, queue)
        return updated

    raise KeyError(f"Queue item not found: {queue_id}")


def approve_queue_item(
    queue_id: str,
    *,
    content_root: str | Path | None = None,
) -> dict[str, Any]:
    def mutate(row):
        if row.get("status") != "DRAFT":
            raise ValueError("Only DRAFT queue items can be approved")
        row["status"] = "APPROVED"
        row["approved_at"] = _iso()
        row["last_action"] = "APPROVED"
        return row

    return _mutate_queue_item(
        queue_id,
        content_root=content_root,
        mutate=mutate,
    )


def cancel_queue_item(
    queue_id: str,
    *,
    content_root: str | Path | None = None,
) -> dict[str, Any]:
    def mutate(row):
        if row.get("status") in {"PUBLISHED", "EXPORTED", "CANCELLED"}:
            raise ValueError("Queue item is already terminal")
        row["status"] = "CANCELLED"
        row["completed_at"] = _iso()
        row["last_action"] = "CANCELLED"
        return row

    return _mutate_queue_item(
        queue_id,
        content_root=content_root,
        mutate=mutate,
    )


def process_queue_item(
    queue_id: str,
    *,
    content_root: str | Path | None = None,
) -> dict[str, Any]:
    readiness = platform_readiness()

    def mutate(row):
        if row.get("status") != "APPROVED":
            raise ValueError("Publishing requires APPROVED status")

        results = {}
        all_terminal = True
        any_external = False

        for platform in row.get("platforms", []):
            if platform == "manual_export":
                results[platform] = {
                    "status": "EXPORTED",
                    "network_request_sent": False,
                }
                continue

            any_external = True
            state = readiness.get(platform) or {}

            if not state.get("config_present"):
                results[platform] = {
                    "status": "NOT_CONFIGURED",
                    "network_request_sent": False,
                }
                all_terminal = False
                continue

            results[platform] = {
                "status": "CONNECTOR_REQUIRES_LIVE_VERIFICATION",
                "network_request_sent": False,
            }
            all_terminal = False

        row["platform_results"] = results
        row["last_action"] = "PROCESS_ATTEMPTED"

        if not any_external and all_terminal:
            row["status"] = "EXPORTED"
            row["completed_at"] = _iso()
        else:
            row["status"] = "APPROVED"

        return row

    return _mutate_queue_item(
        queue_id,
        content_root=content_root,
        mutate=mutate,
    )


def _fixture_content_fingerprint(
    item: dict[str, Any],
    *,
    fixture_id: int,
    competition_id: int,
    season: int,
) -> str:
    """Return a stable fingerprint of immutable pre-match content."""
    lock = item.get("prediction_lock") or {}
    predict = item.get("sportsq_predict") or {}
    score_call = item.get("sportsq_score_call") or {}
    confidence = item.get("sportsq_confidence") or {}
    form = item.get("sportsq_form_index") or {}
    news = item.get("sportsq_news_impact") or {}

    home_form = form.get("home") or {}
    away_form = form.get("away") or {}

    identity = {
        "fixture_id": int(fixture_id),
        "competition_id": int(competition_id),
        "season": int(season),
        "locked_at": lock.get("locked_at"),
        "publish": lock.get("publish"),
        "prediction": predict.get("prediction"),
        "prediction_source": predict.get("source"),
        "score_call": score_call.get("score"),
        "score_call_source": score_call.get("source"),
        "confidence_percent": confidence.get("percent"),
        "confidence_band": confidence.get("band"),
        "confidence_source": confidence.get("source"),
        "home_form_index": home_form.get("index"),
        "away_form_index": away_form.get("index"),
        "news_verified": bool(news.get("verified")),
        "news_direction": news.get("direction"),
        "news_impact_score": news.get("impact_score"),
    }

    canonical = json.dumps(
        identity,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _find_fixture_content_package(
    packages: Path,
    *,
    fixture_id: int,
    competition_id: int,
    season: int,
    content_fingerprint: str,
) -> dict[str, Any] | None:
    """Return an existing package with exactly the same content identity."""
    for manifest_path in packages.glob("*/manifest.json"):
        try:
            manifest = json.loads(
                manifest_path.read_text(encoding="utf-8")
            )
        except (OSError, json.JSONDecodeError, UnicodeDecodeError):
            continue

        if (
            manifest.get("status") == "success"
            and manifest.get("fixture_id") == int(fixture_id)
            and manifest.get("competition_id") == int(competition_id)
            and manifest.get("season") == int(season)
            and manifest.get("content_fingerprint") == content_fingerprint
        ):
            result = dict(manifest)
            result["idempotent_reuse"] = True
            return result

    return None


def generate_fixture_content_package(
    session: Session,
    *,
    fixture_id: int,
    competition_id: int = 2,
    season: int = 2026,
    content_root: str | Path | None = None,
) -> dict[str, Any]:
    root = _content_root(content_root)
    packages = root / "packages"
    packages.mkdir(parents=True, exist_ok=True)

    payload = get_sportsq_intelligence(
        session,
        fixture_id=int(fixture_id),
        competition_id=int(competition_id),
        season=int(season),
    )

    if payload.get("status") != "success":
        return {
            "status": "not_found",
            "fixture_id": int(fixture_id),
            "package_id": None,
            "assets": [],
            "reason": payload.get("status") or "fixture_not_found",
        }

    item = payload.get("item")
    if not isinstance(item, dict):
        return {
            "status": "not_found",
            "fixture_id": int(fixture_id),
            "package_id": None,
            "assets": [],
            "reason": "fixture_intelligence_not_available",
        }

    content_fingerprint = _fixture_content_fingerprint(
        item,
        fixture_id=int(fixture_id),
        competition_id=int(competition_id),
        season=int(season),
    )

    existing = _find_fixture_content_package(
        packages,
        fixture_id=int(fixture_id),
        competition_id=int(competition_id),
        season=int(season),
        content_fingerprint=content_fingerprint,
    )
    if existing is not None:
        return existing

    package_id = (
        _utc_now().strftime("%Y%m%dT%H%M%SZ")
        + "-fixture-"
        + str(int(fixture_id))
        + "-"
        + uuid.uuid4().hex[:8]
    )
    package_dir = packages / package_id
    package_dir.mkdir(parents=True, exist_ok=False)

    fixture = item.get("fixture") or {}
    stem = _safe_stem(
        f"{fixture.get('fixture_id') or fixture_id}-"
        f"{fixture.get('home_team') or 'home'}-vs-"
        f"{fixture.get('away_team') or 'away'}"
    )

    square = package_dir / f"{stem}-prediction-square.png"
    vertical = package_dir / f"{stem}-story.png"
    caption = package_dir / f"{stem}-caption.txt"
    article = package_dir / f"{stem}-article.txt"
    accuracy_path = package_dir / "results-accuracy.png"

    render_prediction_card(item, square, vertical=False)
    render_prediction_card(item, vertical, vertical=True)

    copy = _prediction_copy(item)
    caption.write_text(
        copy + "\n\n#MDRNSportsQ #SportsQPredict #Football",
        encoding="utf-8",
    )
    article.write_text(copy, encoding="utf-8")

    accuracy = sportsq_accuracy_summary(
        session,
        competition_id=int(competition_id),
        season=int(season),
    )
    render_accuracy_card(accuracy, accuracy_path)

    assets = [
        {
            "kind": "MATCH_PREDICTION",
            "format": "square",
            "filename": square.name,
            "media_type": "image/png",
        },
        {
            "kind": "VERTICAL_STORY_REEL",
            "format": "vertical",
            "filename": vertical.name,
            "media_type": "image/png",
        },
        {
            "kind": "CAPTION",
            "filename": caption.name,
            "media_type": "text/plain",
        },
        {
            "kind": "ARTICLE",
            "filename": article.name,
            "media_type": "text/plain",
        },
        {
            "kind": "RESULTS_ACCURACY",
            "filename": accuracy_path.name,
            "media_type": "image/png",
        },
    ]

    news_path = package_dir / f"{stem}-breaking-news.png"
    news_result = render_news_card(item, news_path)

    if news_result.get("created"):
        assets.append({
            "kind": "BREAKING_TEAM_NEWS",
            "filename": news_path.name,
            "media_type": "image/png",
        })

    video_path = package_dir / "sportsq-static-story.mp4"
    video_result = _make_static_video(
        [vertical],
        video_path,
        seconds_per_card=6,
    )

    if video_result.get("created"):
        assets.append({
            "kind": "STATIC_STORY_VIDEO",
            "filename": video_path.name,
            "media_type": "video/mp4",
            "motion_policy": "STATIC_CARDS_ONLY",
        })

    manifest = {
        "status": "success",
        "package_id": package_id,
        "brand": BRAND,
        "created_at": _iso(),
        "fixture_id": int(fixture_id),
        "competition_id": int(competition_id),
        "season": int(season),
        "content_fingerprint": content_fingerprint,
        "idempotent_reuse": False,
        "prediction_count": 1,
        "assets": assets,
        "static_video": video_result,
        "breaking_news": news_result,
        "rights": {
            "third_party_clips_used": False,
            "licensed_clip_metadata_required": True,
            "attribution_is_not_license": True,
            "unverified_clip_insertion_allowed": False,
        },
        "safety": {
            "prediction_engine_rewritten": False,
            "prediction_rows_modified": False,
            "prediction_locks_modified": False,
            "historical_holdout_touched": False,
            "post_kickoff_features_used": False,
        },
    }

    (package_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True),
        encoding="utf-8",
    )

    return manifest

