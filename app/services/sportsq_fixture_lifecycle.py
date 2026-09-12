from __future__ import annotations

import inspect as pyinspect
import json
import uuid
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import (
    Boolean, Column, DateTime, Integer, MetaData, String, Table, Text,
    UniqueConstraint, and_, inspect as sa_inspect, select, text,
)
from sqlalchemy.orm import Session

from app.config import Settings
from app.db import engine
from app.models import Fixture, LeagueSeason, LivePrediction
from app.providers.live_score_api import LiveScoreApiClient
from app.providers.api_football import ApiFootballClient
from app.services.high_confidence_selector import POLICY_VERSION
from app.services.live_score_ingestor import sync_live_score_history
from app.services.fixture_ingestor import refresh_unfinished_results
from app.services.prediction_grading import grade_live_predictions
from app.services.sportsq_scorecall import lock_scorecalls_for_existing_predictions

STAGE = 7
PRODUCT = "SportsQ Automated Fixture Lifecycle"
TABLE_NAME = "sportsq_fixture_lifecycle_runs"
TRIGGER_UPDATE = "trg_sportsq_fixture_lifecycle_runs_no_update"
TRIGGER_DELETE = "trg_sportsq_fixture_lifecycle_runs_no_delete"

metadata = MetaData()
lifecycle_runs_table = Table(
    TABLE_NAME,
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("run_key", String(80), nullable=False),
    Column("competition_id", Integer, nullable=False),
    Column("season", Integer, nullable=False),
    Column("started_at", DateTime(timezone=True), nullable=False),
    Column("finished_at", DateTime(timezone=True), nullable=False),
    Column("status", String(30), nullable=False),
    Column("market_status", String(30), nullable=True),
    Column("result_refresh_status", String(30), nullable=True),
    Column("prediction_locked_new", Integer, nullable=False, default=0),
    Column("scorecall_locked_new", Integer, nullable=False, default=0),
    Column("graded_new", Integer, nullable=False, default=0),
    Column("content_ready_count", Integer, nullable=False, default=0),
    Column("final_holdout_touched", Boolean, nullable=False, default=False),
    Column("detail_json", Text, nullable=False),
    Column("created_at", DateTime(timezone=True), nullable=False),
    UniqueConstraint("run_key", name="uq_sportsq_fixture_lifecycle_run_key"),
)


def _utc(value: datetime | None = None) -> datetime:
    value = value or datetime.now(timezone.utc)
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_utc(value: datetime) -> datetime:
    return _utc(value).replace(tzinfo=None)


def _redact(value: Any) -> Any:
    sensitive = (
        "secret", "token", "password", "credential",
        "api_key", "apikey", "authorization",
    )
    if isinstance(value, dict):
        safe: dict[str, Any] = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(marker in lowered for marker in sensitive):
                safe[str(key)] = "<REDACTED>"
            elif "url" in lowered:
                safe[str(key)] = "<REDACTED_URL>"
            else:
                safe[str(key)] = _redact(item)
        return safe
    if isinstance(value, (list, tuple)):
        return [_redact(item) for item in value]
    if isinstance(value, datetime):
        return _utc(value).isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def ensure_fixture_lifecycle_schema() -> dict[str, Any]:
    metadata.create_all(engine, tables=[lifecycle_runs_table], checkfirst=True)

    if engine.dialect.name == "sqlite":
        with engine.begin() as conn:
            conn.execute(text(
                f"""
                CREATE TRIGGER IF NOT EXISTS {TRIGGER_UPDATE}
                BEFORE UPDATE ON {TABLE_NAME}
                BEGIN
                    SELECT RAISE(ABORT, '{TABLE_NAME} is append-only');
                END
                """
            ))
            conn.execute(text(
                f"""
                CREATE TRIGGER IF NOT EXISTS {TRIGGER_DELETE}
                BEFORE DELETE ON {TABLE_NAME}
                BEGIN
                    SELECT RAISE(ABORT, '{TABLE_NAME} is append-only');
                END
                """
            ))

    return {
        "stage": STAGE,
        "table": TABLE_NAME,
        "table_ready": TABLE_NAME in set(sa_inspect(engine).get_table_names()),
        "append_only": True,
        "update_trigger": TRIGGER_UPDATE,
        "delete_trigger": TRIGGER_DELETE,
    }


def _call_signature(fn: Callable[..., Any]) -> dict[str, Any]:
    sig = pyinspect.signature(fn)
    required: list[str] = []
    optional: list[str] = []
    for name, param in sig.parameters.items():
        if param.kind in {
            pyinspect.Parameter.VAR_POSITIONAL,
            pyinspect.Parameter.VAR_KEYWORD,
        }:
            continue
        (required if param.default is pyinspect.Parameter.empty else optional).append(name)
    return {
        "name": getattr(fn, "__name__", type(fn).__name__),
        "signature": str(sig),
        "required": required,
        "optional": optional,
    }


_HISTORY_VALUE_NAMES = {
    "session", "client", "competition_id", "competition", "league_id", "league",
    "season", "from_date", "to_date", "date_from", "date_to",
    "start_date", "end_date", "lookback_hours", "page", "start_page", "max_pages",
}


def history_signature_status(
    provider: str = "live-score-api",
) -> dict[str, Any]:
    fn = (
        refresh_unfinished_results
        if provider == "api-football"
        else sync_live_score_history
    )

    info = _call_signature(fn)

    unknown = [
        name
        for name in info["required"]
        if name not in _HISTORY_VALUE_NAMES
    ]

    return {
        **info,
        "provider": provider,
        "supported_by_stage7_adapter": not unknown,
        "unknown_required_parameters": unknown,
    }


async def _invoke_async(fn: Callable[..., Any], values: dict[str, Any]) -> Any:
    sig = pyinspect.signature(fn)
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    missing: list[str] = []

    for name, param in sig.parameters.items():
        if param.kind in {
            pyinspect.Parameter.VAR_POSITIONAL,
            pyinspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if name not in values:
            if param.default is pyinspect.Parameter.empty:
                missing.append(name)
            continue
        if param.kind == pyinspect.Parameter.POSITIONAL_ONLY:
            args.append(values[name])
        else:
            kwargs[name] = values[name]

    if missing:
        raise RuntimeError("Unsupported required parameters: " + ", ".join(missing))

    result = fn(*args, **kwargs)
    if pyinspect.isawaitable(result):
        result = await result
    return result


def _invoke_sync(fn: Callable[..., Any], values: dict[str, Any]) -> Any:
    sig = pyinspect.signature(fn)
    args: list[Any] = []
    kwargs: dict[str, Any] = {}
    missing: list[str] = []

    for name, param in sig.parameters.items():
        if param.kind in {
            pyinspect.Parameter.VAR_POSITIONAL,
            pyinspect.Parameter.VAR_KEYWORD,
        }:
            continue
        if name not in values:
            if param.default is pyinspect.Parameter.empty:
                missing.append(name)
            continue
        if param.kind == pyinspect.Parameter.POSITIONAL_ONLY:
            args.append(values[name])
        else:
            kwargs[name] = values[name]

    if missing:
        raise RuntimeError("Unsupported required parameters: " + ", ".join(missing))

    result = fn(*args, **kwargs)
    if pyinspect.isawaitable(result):
        raise RuntimeError("Synchronous Stage 7 callable returned an awaitable")
    return result


async def _refresh_recent_results(
    session: Session,
    settings: Settings,
    *,
    competition_id: int,
    season: int,
    now: datetime,
    provider: str = "live-score-api",
) -> dict[str, Any]:
    signature = history_signature_status(provider)

    if not signature[
        "supported_by_stage7_adapter"
    ]:
        return {
            "status": "adapter_not_supported",
            "signature": signature,
        }

    lookback_hours = max(
        1,
        int(
            getattr(
                settings,
                "result_lookback_hours",
                36,
            )
        ),
    )

    values = {
        "session": session,
        "competition_id": int(competition_id),
        "competition": int(competition_id),
        "league_id": int(competition_id),
        "league": int(competition_id),
        "season": int(season),
        "lookback_hours": lookback_hours,
        "now": now,
    }

    if provider == "api-football":
        try:
            settings.require_api_key()
        except Exception as exc:
            return {
                "status": "config_required",
                "detail": type(exc).__name__,
                "signature": signature,
            }

        try:
            async with ApiFootballClient(
                settings
            ) as client:
                values["client"] = client

                result = await _invoke_async(
                    refresh_unfinished_results,
                    values,
                )

            safe = _redact(result)

            status = (
                str(
                    safe.get("status")
                    or "success"
                )
                if isinstance(safe, dict)
                else "success"
            )

            return {
                "status": status,
                "summary": safe,
                "signature": signature,
            }

        except Exception as exc:
            return {
                "status": "failed",
                "detail": type(exc).__name__,
                "signature": signature,
            }

    try:
        settings.require_live_score_credentials()
    except Exception as exc:
        return {
            "status": "config_required",
            "detail": type(exc).__name__,
            "signature": signature,
        }

    max_pages = max(
        1,
        int(
            getattr(
                settings,
                "ls_max_pages",
                50,
            )
        ),
    )

    start_dt = _utc(now) - timedelta(
        hours=lookback_hours
    )

    end_dt = _utc(now)

    values.update({
        "from_date": start_dt.date(),
        "to_date": end_dt.date(),
        "date_from": start_dt.date(),
        "date_to": end_dt.date(),
        "start_date": start_dt.date(),
        "end_date": end_dt.date(),
        "page": 1,
        "start_page": 1,
        "max_pages": max_pages,
    })

    try:
        async with LiveScoreApiClient(
            settings
        ) as client:
            values["client"] = client

            result = await _invoke_async(
                sync_live_score_history,
                values,
            )

        safe = _redact(result)

        status = (
            str(
                safe.get("status")
                or "success"
            )
            if isinstance(safe, dict)
            else "success"
        )

        return {
            "status": status,
            "summary": safe,
            "signature": signature,
        }

    except Exception as exc:
        return {
            "status": "failed",
            "detail": type(exc).__name__,
            "signature": signature,
        }


def _classify_fixture_state(
    *,
    is_finished: bool,
    kickoff_utc: datetime,
    now: datetime,
    has_prediction: bool,
    prediction_graded: bool,
    has_scorecall: bool,
) -> str:
    kickoff = _utc(kickoff_utc)
    current = _utc(now)

    if is_finished:
        if has_prediction and prediction_graded:
            return "COMPLETED_GRADED"
        if has_prediction:
            return "COMPLETED_PENDING_GRADE"
        return "COMPLETED_NO_LOCK"

    if kickoff <= current:
        return "STARTED_UNFINISHED"
    if has_prediction and has_scorecall:
        return "PREMATCH_LOCKED_SCORECALL"
    if has_prediction:
        return "PREMATCH_LOCKED"
    return "PREMATCH_PENDING_LOCK"


def fixture_lifecycle_rows(
    session: Session,
    *,
    competition_id: int,
    season: int,
    limit: int = 100,
    now: datetime | None = None,
    provider: str = "live-score-api",
) -> list[dict[str, Any]]:
    current = _utc(now)

    stmt = (
        select(Fixture, LivePrediction)
        .join(LeagueSeason, Fixture.league_season_id == LeagueSeason.id)
        .outerjoin(
            LivePrediction,
            and_(
                LivePrediction.fixture_id == Fixture.id,
                LivePrediction.competition_id == int(competition_id),
                LivePrediction.season == int(season),
                LivePrediction.policy_version == POLICY_VERSION,
            ),
        )
        .where(
            LeagueSeason.provider == provider,
            LeagueSeason.provider_league_id == int(competition_id),
            LeagueSeason.season == int(season),
        )
        .order_by(Fixture.kickoff_utc.desc(), Fixture.id.desc())
        .limit(max(1, min(int(limit), 500)))
    )

    pairs = list(session.execute(stmt).all())

    try:
        scorecall_fixture_ids = {
            int(value)
            for value in session.execute(
                text(
                    """
                    SELECT fixture_id
                    FROM sportsq_scorecall_locks
                    WHERE competition_id=:competition_id
                      AND season=:season
                    """
                ),
                {
                    "competition_id": int(competition_id),
                    "season": int(season),
                },
            ).scalars()
        }
    except Exception:
        scorecall_fixture_ids = set()

    rows: list[dict[str, Any]] = []
    for fixture, prediction in pairs:
        kickoff = _utc(fixture.kickoff_utc)
        has_prediction = prediction is not None
        graded = bool(
            prediction is not None
            and prediction.actual_result is not None
            and prediction.is_correct is not None
            and prediction.graded_at is not None
        )
        has_scorecall = int(fixture.id) in scorecall_fixture_ids

        rows.append({
            "fixture_id": int(fixture.id),
            "provider_fixture_id": int(fixture.provider_fixture_id),
            "kickoff_utc": kickoff.isoformat(),
            "status_short": fixture.status_short,
            "is_finished": bool(fixture.is_finished),
            "result_1x2": fixture.result_1x2,
            "has_prediction_lock": has_prediction,
            "prediction_graded": graded,
            "has_scorecall_lock": has_scorecall,
            "content_handoff_ready": bool(
                has_prediction and kickoff > current and not fixture.is_finished
            ),
            "lifecycle_state": _classify_fixture_state(
                is_finished=bool(fixture.is_finished),
                kickoff_utc=kickoff,
                now=current,
                has_prediction=has_prediction,
                prediction_graded=graded,
                has_scorecall=has_scorecall,
            ),
        })

    return rows


def _latest_audit(
    session: Session,
    *,
    competition_id: int,
    season: int,
) -> dict[str, Any] | None:
    if TABLE_NAME not in set(sa_inspect(engine).get_table_names()):
        return None

    row = session.execute(
        select(lifecycle_runs_table)
        .where(
            lifecycle_runs_table.c.competition_id == int(competition_id),
            lifecycle_runs_table.c.season == int(season),
        )
        .order_by(
            lifecycle_runs_table.c.finished_at.desc(),
            lifecycle_runs_table.c.id.desc(),
        )
        .limit(1)
    ).mappings().first()

    if row is None:
        return None

    result = dict(row)
    for key in ("started_at", "finished_at", "created_at"):
        if isinstance(result.get(key), datetime):
            result[key] = _utc(result[key]).isoformat()

    try:
        result["detail"] = json.loads(result.get("detail_json") or "{}")
    except Exception:
        result["detail"] = {}
    result.pop("detail_json", None)
    return _redact(result)


def lifecycle_status(
    session: Session,
    *,
    competition_id: int = 2,
    season: int = 2026,
    now: datetime | None = None,
    provider: str = "live-score-api",
) -> dict[str, Any]:
    current = _utc(now)
    tables = set(sa_inspect(engine).get_table_names())
    rows = fixture_lifecycle_rows(
        session,
        competition_id=int(competition_id),
        season=int(season),
        limit=500,
        now=current,
        provider=provider,
    )
    counts = Counter(row["lifecycle_state"] for row in rows)

    trigger_names: list[str] = []
    if TABLE_NAME in tables and engine.dialect.name == "sqlite":
        with engine.connect() as conn:
            trigger_names = [
                str(row[0])
                for row in conn.execute(
                    text(
                        """
                        SELECT name FROM sqlite_master
                        WHERE type='trigger' AND tbl_name=:table_name
                        ORDER BY name
                        """
                    ),
                    {"table_name": TABLE_NAME},
                ).all()
            ]

    return {
        "brand": "MDRN SportsQ",
        "product": PRODUCT,
        "stage": STAGE,
        "status": "ready" if TABLE_NAME in tables else "schema_required",
        "competition_id": int(competition_id),
        "season": int(season),
        "generated_at": current.isoformat(),
        "table": TABLE_NAME,
        "table_ready": TABLE_NAME in tables,
        "append_only_audit": (
            TRIGGER_UPDATE in trigger_names and TRIGGER_DELETE in trigger_names
            if engine.dialect.name == "sqlite"
            else TABLE_NAME in tables
        ),
        "history_adapter": history_signature_status(provider),
        "fixture_states": dict(sorted(counts.items())),
        "fixture_rows_seen": len(rows),
        "content_handoff_ready": sum(1 for row in rows if row["content_handoff_ready"]),
        "latest_run": _latest_audit(
            session,
            competition_id=int(competition_id),
            season=int(season),
        ),
        "safety": {
            "prediction_engine_rewritten": False,
            "existing_prediction_lock_fields_rewritten": False,
            "existing_scorecall_locks_rewritten": False,
            "historical_holdout_touched": False,
            "stage6_live_news_ingestion_enabled_by_stage7": False,
            "social_auto_posting_enabled_by_stage7": False,
        },
    }


def _counter(summary: Any, *names: str) -> int:
    if not isinstance(summary, dict):
        return 0
    for name in names:
        try:
            if summary.get(name) is not None:
                return int(summary[name])
        except (TypeError, ValueError):
            pass
    return 0


async def run_fixture_lifecycle_post_refresh(
    session: Session,
    settings: Settings,
    *,
    competition_id: int,
    season: int,
    market_result: dict[str, Any] | None = None,
    now: datetime | None = None,
    provider: str = "live-score-api",
) -> dict[str, Any]:
    """Complete post-refresh ScoreCall, result refresh, grading and audit."""
    ensure_fixture_lifecycle_schema()

    started = _utc(now)
    run_key = "stage7-" + uuid.uuid4().hex
    market_safe = _redact(market_result or {})
    market_status = (
        str(market_safe.get("status") or "unknown")
        if isinstance(market_safe, dict)
        else "unknown"
    )
    errors: list[dict[str, str]] = []

    try:
        score_raw = _invoke_sync(
            lock_scorecalls_for_existing_predictions,
            {
                "session": session,
                "competition_id": int(competition_id),
                "season": int(season),
                "now": started,
                "limit": None,
            },
        )
        scorecall = {"status": "success", "summary": _redact(score_raw)}
    except Exception as exc:
        scorecall = {"status": "failed", "detail": type(exc).__name__}
        errors.append({"step": "scorecall", "error": type(exc).__name__})

    result_refresh = await _refresh_recent_results(
        session,
        settings,
        competition_id=int(competition_id),
        season=int(season),
        now=started,
        provider=provider,
    )
    if result_refresh.get("status") in {
        "failed", "adapter_not_supported", "config_required",
    }:
        errors.append({
            "step": "result_refresh",
            "error": str(result_refresh.get("status")),
        })

    try:
        grading_raw = _invoke_sync(
            grade_live_predictions,
            {
                "session": session,
                "competition_id": int(competition_id),
                "season": int(season),
                "now": started,
            },
        )
        grading = {"status": "success", "summary": _redact(grading_raw)}
    except Exception as exc:
        grading = {"status": "failed", "detail": type(exc).__name__}
        errors.append({"step": "grading", "error": type(exc).__name__})

    rows = fixture_lifecycle_rows(
        session,
        competition_id=int(competition_id),
        season=int(season),
        limit=500,
        now=started,
        provider=provider,
    )
    states = Counter(row["lifecycle_state"] for row in rows)
    content_ready = sum(1 for row in rows if row["content_handoff_ready"])

    prediction_locked_new = _counter(market_safe, "locked_new")
    if isinstance(market_safe, dict) and isinstance(market_safe.get("predictions"), dict):
        prediction_locked_new = max(
            prediction_locked_new,
            _counter(market_safe["predictions"], "locked_new"),
        )

    scorecall_locked_new = _counter(
        scorecall.get("summary"), "locked_new", "created", "inserted"
    )
    graded_new = _counter(
        grading.get("summary"), "graded", "graded_new", "updated"
    )

    overall = "success"
    if market_status in {"failed", "blocked"}:
        overall = market_status
    elif errors:
        overall = "partial"

    finished = _utc()
    detail = {
        "market": market_safe,
        "scorecall": scorecall,
        "result_refresh": result_refresh,
        "grading": grading,
        "fixture_states": dict(sorted(states.items())),
        "errors": errors,
        "content_handoff": {
            "ready_count": content_ready,
            "auto_generation_performed": False,
        },
        "safety": {
            "prediction_engine_rewritten": False,
            "existing_prediction_locks_rewritten": False,
            "existing_scorecall_locks_rewritten": False,
            "historical_holdout_touched": False,
            "stage6_live_news_ingestion_enabled": False,
            "social_auto_posting_performed": False,
        },
    }

    audit_persisted = False
    audit_error = None
    try:
        session.execute(
            lifecycle_runs_table.insert().values(
                run_key=run_key,
                competition_id=int(competition_id),
                season=int(season),
                started_at=_db_utc(started),
                finished_at=_db_utc(finished),
                status=overall,
                market_status=market_status,
                result_refresh_status=str(result_refresh.get("status") or "unknown"),
                prediction_locked_new=int(prediction_locked_new),
                scorecall_locked_new=int(scorecall_locked_new),
                graded_new=int(graded_new),
                content_ready_count=int(content_ready),
                final_holdout_touched=False,
                detail_json=json.dumps(
                    _redact(detail),
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
                created_at=_db_utc(finished),
            )
        )
        session.commit()
        audit_persisted = True
    except Exception as exc:
        session.rollback()
        audit_error = type(exc).__name__

    return {
        "brand": "MDRN SportsQ",
        "product": PRODUCT,
        "stage": STAGE,
        "status": overall,
        "run_key": run_key,
        "competition_id": int(competition_id),
        "season": int(season),
        "started_at": started.isoformat(),
        "finished_at": finished.isoformat(),
        "market_status": market_status,
        "result_refresh": _redact(result_refresh),
        "scorecall": _redact(scorecall),
        "grading": _redact(grading),
        "fixture_states": dict(sorted(states.items())),
        "content_handoff_ready": int(content_ready),
        "audit_persisted": audit_persisted,
        "audit_error": audit_error,
        "final_holdout_touched": False,
        "prediction_engine_rewritten": False,
        "existing_prediction_locks_rewritten": False,
        "existing_scorecall_locks_rewritten": False,
        "stage6_live_news_ingestion_enabled": False,
        "social_auto_posting_performed": False,
    }
