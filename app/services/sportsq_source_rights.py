from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import (
    Boolean,
    Column,
    DateTime,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    insert,
    inspect,
    select,
    update,
)
from sqlalchemy.engine import Engine

from app.db import engine


STAGE4_TABLE_NAMES = {
    "sportsq_sources",
    "sportsq_media_assets",
    "sportsq_rights_verifications",
}

ROOT = Path(__file__).resolve().parents[2]
RESOURCE_ROOT = ROOT / "resources" / "stage4"
SEED_ROOT = RESOURCE_ROOT / "seed"
POLICY_ROOT = RESOURCE_ROOT / "policies"

metadata = MetaData()

sources_table = Table(
    "sportsq_sources",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("source_id", String(160), nullable=False, unique=True),
    Column("name", String(220), nullable=False),
    Column("source_kind", String(60), nullable=False),
    Column("category", String(100), nullable=True),
    Column("scope", String(240), nullable=True),
    Column("base_url", Text, nullable=False),
    Column("trust_class", String(40), nullable=False),
    Column("status", String(32), nullable=False, default="ACTIVE"),
    Column("use_for", Text, nullable=True),
    Column("media_reuse_default", String(100), nullable=True),
    Column("source_type", String(100), nullable=True),
    Column("priority", Integer, nullable=True),
    Column("commercial_reuse", String(120), nullable=True),
    Column("modification", String(120), nullable=True),
    Column("attribution", String(120), nullable=True),
    Column("default_gate", String(120), nullable=True),
    Column("recommended_use", Text, nullable=True),
    Column("licence_reference", Text, nullable=True),
    Column("notes", Text, nullable=True),
    Column("last_verified_at", DateTime(timezone=True), nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

media_assets_table = Table(
    "sportsq_media_assets",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("asset_id", String(180), nullable=False, unique=True),
    Column("source_id", String(160), nullable=False),
    Column("source_url", Text, nullable=False),
    Column("title", Text, nullable=True),
    Column("team_home", String(220), nullable=True),
    Column("team_away", String(220), nullable=True),
    Column("match_date", String(40), nullable=True),
    Column("media_type", String(40), nullable=False, default="VIDEO"),
    Column("licence_code", String(120), nullable=True),
    Column("rights_holder", Text, nullable=True),
    Column("commercial_use_allowed", Boolean, nullable=True),
    Column("modification_allowed", Boolean, nullable=True),
    Column("attribution_required", Boolean, nullable=True),
    Column("attribution_text", Text, nullable=True),
    Column("verified_at", DateTime(timezone=True), nullable=True),
    Column("verified_by", String(220), nullable=True),
    Column("evidence_url", Text, nullable=True),
    Column("reuse_verified", Boolean, nullable=False, default=False),
    Column("verification_status", String(40), nullable=False, default="PENDING"),
    Column("notes", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
    Column("updated_at", DateTime(timezone=True), nullable=False),
)

rights_verifications_table = Table(
    "sportsq_rights_verifications",
    metadata,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("verification_id", String(180), nullable=False, unique=True),
    Column("asset_id", String(180), nullable=False),
    Column("licence_code", String(120), nullable=True),
    Column("commercial_use_allowed", Boolean, nullable=False),
    Column("modification_allowed", Boolean, nullable=False),
    Column("attribution_required", Boolean, nullable=False, default=False),
    Column("share_alike_required", Boolean, nullable=False, default=False),
    Column("trademark_check", String(40), nullable=False, default="REVIEW"),
    Column("personality_rights_check", String(40), nullable=False, default="REVIEW"),
    Column("evidence_url", Text, nullable=True),
    Column("verified_at", DateTime(timezone=True), nullable=False),
    Column("verified_by", String(220), nullable=True),
    Column("reuse_verified", Boolean, nullable=False, default=False),
    Column("decision_reason", Text, nullable=True),
    Column("created_at", DateTime(timezone=True), nullable=False),
)


class MediaReuseBlockedError(RuntimeError):
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _load_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _serialise(row: Any) -> dict[str, Any]:
    data = dict(row)
    for key, value in list(data.items()):
        if isinstance(value, datetime):
            if value.tzinfo is None:
                value = value.replace(tzinfo=timezone.utc)
            data[key] = value.astimezone(timezone.utc).isoformat()
    return data


def ensure_stage4_schema(db_engine: Engine = engine) -> dict[str, Any]:
    metadata.create_all(
        db_engine,
        tables=[
            sources_table,
            media_assets_table,
            rights_verifications_table,
        ],
        checkfirst=True,
    )
    existing = set(inspect(db_engine).get_table_names())
    missing = sorted(STAGE4_TABLE_NAMES - existing)
    if missing:
        raise RuntimeError(f"Stage 4 schema incomplete: {missing}")
    return {"created_or_present": sorted(STAGE4_TABLE_NAMES)}


def seed_stage4_sources(db_engine: Engine = engine) -> dict[str, int]:
    official_path = SEED_ROOT / "official_football_sources.json"
    media_path = SEED_ROOT / "reusable_media_sources.json"

    if not official_path.exists() or not media_path.exists():
        raise RuntimeError("Approved Stage 4 seed resources are missing.")

    official_rows = _load_json(official_path)
    media_rows = _load_json(media_path)
    now = _utcnow()

    inserted = 0
    updated = 0

    def apply(conn, payload: dict[str, Any]) -> None:
        nonlocal inserted, updated
        source_id = str(payload["source_id"])

        existing = conn.execute(
            select(sources_table.c.id).where(
                sources_table.c.source_id == source_id
            )
        ).scalar_one_or_none()

        allowed_columns = set(sources_table.c.keys())
        values = {
            key: value
            for key, value in payload.items()
            if key in allowed_columns
            and key not in {"id", "created_at", "updated_at"}
        }
        values["updated_at"] = now

        if existing is None:
            values["created_at"] = now
            conn.execute(insert(sources_table).values(**values))
            inserted += 1
        else:
            conn.execute(
                update(sources_table)
                .where(sources_table.c.source_id == source_id)
                .values(**values)
            )
            updated += 1

    with db_engine.begin() as conn:
        for row in official_rows:
            apply(
                conn,
                {
                    **row,
                    "source_kind": "OFFICIAL_FOOTBALL",
                    "status": "ACTIVE",
                },
            )

        for row in media_rows:
            apply(
                conn,
                {
                    **row,
                    "source_kind": "REUSABLE_MEDIA_PROVIDER",
                    "category": "media_provider",
                    "scope": "reusable_media",
                    "trust_class": "OPEN_MEDIA",
                    "status": "ACTIVE",
                    "use_for": row.get("recommended_use"),
                    "media_reuse_default": row.get("default_gate"),
                },
            )

    return {
        "official_seed_records": len(official_rows),
        "media_provider_seed_records": len(media_rows),
        "total_seed_records": len(official_rows) + len(media_rows),
        "inserted": inserted,
        "updated": updated,
    }


def list_sources(
    *,
    trust_class: str | None = None,
    source_kind: str | None = None,
    status: str | None = "ACTIVE",
    db_engine: Engine = engine,
) -> list[dict[str, Any]]:
    stmt = select(sources_table).order_by(
        sources_table.c.source_kind.asc(),
        sources_table.c.name.asc(),
    )

    if trust_class:
        stmt = stmt.where(sources_table.c.trust_class == trust_class)
    if source_kind:
        stmt = stmt.where(sources_table.c.source_kind == source_kind)
    if status:
        stmt = stmt.where(sources_table.c.status == status)

    with db_engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()

    return [_serialise(row) for row in rows]


def get_source(
    source_id: str,
    *,
    db_engine: Engine = engine,
) -> dict[str, Any] | None:
    with db_engine.connect() as conn:
        row = conn.execute(
            select(sources_table).where(
                sources_table.c.source_id == source_id
            )
        ).mappings().first()

    return _serialise(row) if row else None


def upsert_media_asset(
    asset: dict[str, Any],
    *,
    db_engine: Engine = engine,
) -> dict[str, Any]:
    required = {"asset_id", "source_id", "source_url"}
    missing = sorted(key for key in required if not asset.get(key))
    if missing:
        raise ValueError(f"Missing required media fields: {missing}")

    now = _utcnow()
    allowed_columns = set(media_assets_table.c.keys())
    values = {
        key: value
        for key, value in asset.items()
        if key in allowed_columns
        and key not in {"id", "created_at", "updated_at"}
    }
    values.setdefault("media_type", "VIDEO")
    values.setdefault("reuse_verified", False)
    values.setdefault("verification_status", "PENDING")
    values["updated_at"] = now

    with db_engine.begin() as conn:
        existing = conn.execute(
            select(media_assets_table.c.id).where(
                media_assets_table.c.asset_id == str(asset["asset_id"])
            )
        ).scalar_one_or_none()

        if existing is None:
            values["created_at"] = now
            conn.execute(insert(media_assets_table).values(**values))
        else:
            conn.execute(
                update(media_assets_table)
                .where(
                    media_assets_table.c.asset_id
                    == str(asset["asset_id"])
                )
                .values(**values)
            )

    result = get_media_asset(
        str(asset["asset_id"]),
        db_engine=db_engine,
    )
    if result is None:
        raise RuntimeError("Media asset upsert failed.")

    return result


def get_media_asset(
    asset_id: str,
    *,
    db_engine: Engine = engine,
) -> dict[str, Any] | None:
    with db_engine.connect() as conn:
        row = conn.execute(
            select(media_assets_table).where(
                media_assets_table.c.asset_id == asset_id
            )
        ).mappings().first()

    return _serialise(row) if row else None


def list_media_assets(
    *,
    reuse_verified: bool | None = None,
    verification_status: str | None = None,
    limit: int = 100,
    db_engine: Engine = engine,
) -> list[dict[str, Any]]:
    safe_limit = max(1, min(int(limit), 500))

    stmt = (
        select(media_assets_table)
        .order_by(media_assets_table.c.id.desc())
        .limit(safe_limit)
    )

    if reuse_verified is not None:
        stmt = stmt.where(
            media_assets_table.c.reuse_verified == bool(reuse_verified)
        )

    if verification_status:
        stmt = stmt.where(
            media_assets_table.c.verification_status
            == verification_status
        )

    with db_engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()

    return [_serialise(row) for row in rows]


def get_rights_for_asset(
    asset_id: str,
    *,
    db_engine: Engine = engine,
) -> list[dict[str, Any]]:
    stmt = (
        select(rights_verifications_table)
        .where(rights_verifications_table.c.asset_id == asset_id)
        .order_by(rights_verifications_table.c.id.desc())
    )

    with db_engine.connect() as conn:
        rows = conn.execute(stmt).mappings().all()

    return [_serialise(row) for row in rows]


def evaluate_media_reuse(
    asset: dict[str, Any],
    verification: dict[str, Any] | None,
    *,
    commercial_context: bool = True,
    editing_required: bool = True,
) -> dict[str, Any]:
    reasons: list[str] = []

    if not asset:
        reasons.append("ASSET_MISSING")

    if not verification:
        reasons.append("RIGHTS_VERIFICATION_MISSING")
        verification = {}

    if not asset.get("source_url"):
        reasons.append("SOURCE_URL_MISSING")

    if not verification.get("licence_code"):
        reasons.append("LICENCE_UNKNOWN")

    if not verification.get("evidence_url"):
        reasons.append("RIGHTS_EVIDENCE_MISSING")

    if not verification.get("verified_at"):
        reasons.append("VERIFICATION_TIMESTAMP_MISSING")

    if (
        commercial_context
        and verification.get("commercial_use_allowed") is not True
    ):
        reasons.append("COMMERCIAL_USE_NOT_ALLOWED")

    if (
        editing_required
        and verification.get("modification_allowed") is not True
    ):
        reasons.append("MODIFICATION_NOT_ALLOWED")

    if verification.get("attribution_required") is True:
        attribution_text = str(
            asset.get("attribution_text")
            or verification.get("attribution_text")
            or ""
        ).strip()
        if not attribution_text:
            reasons.append("ATTRIBUTION_TEXT_MISSING")

    if str(
        verification.get("trademark_check") or ""
    ).upper() != "CLEAR":
        reasons.append("TRADEMARK_CHECK_UNRESOLVED")

    if str(
        verification.get("personality_rights_check") or ""
    ).upper() != "CLEAR":
        reasons.append("PERSONALITY_RIGHTS_CHECK_UNRESOLVED")

    if verification.get("reuse_verified") is not True:
        reasons.append("REUSE_NOT_VERIFIED")

    if asset.get("reuse_verified") is not True:
        reasons.append("ASSET_REUSE_FLAG_FALSE")

    if asset.get("verification_status") != "VERIFIED":
        reasons.append("ASSET_NOT_VERIFIED")

    allowed = not reasons

    return {
        "allowed": allowed,
        "reuse_verified": allowed,
        "gate": "REUSE_VERIFIED",
        "policy": "FAIL_CLOSED",
        "reasons": sorted(set(reasons)),
    }


def record_rights_verification(
    verification: dict[str, Any],
    *,
    db_engine: Engine = engine,
) -> dict[str, Any]:
    required = {
        "verification_id",
        "asset_id",
        "commercial_use_allowed",
        "modification_allowed",
        "reuse_verified",
    }
    missing = sorted(
        key for key in required if key not in verification
    )
    if missing:
        raise ValueError(
            f"Missing rights verification fields: {missing}"
        )

    asset_id = str(verification["asset_id"])
    asset = get_media_asset(asset_id, db_engine=db_engine)

    if asset is None:
        raise ValueError(
            f"Cannot verify unknown media asset: {asset_id}"
        )

    now = _utcnow()
    allowed_columns = set(rights_verifications_table.c.keys())

    values = {
        key: value
        for key, value in verification.items()
        if key in allowed_columns
        and key not in {"id", "created_at"}
    }
    values.setdefault("attribution_required", False)
    values.setdefault("share_alike_required", False)
    values.setdefault("trademark_check", "REVIEW")
    values.setdefault("personality_rights_check", "REVIEW")
    values.setdefault("verified_at", now)
    values["created_at"] = now

    decision_asset = dict(asset)
    decision_asset["reuse_verified"] = bool(
        verification.get("reuse_verified")
    )
    decision_asset["verification_status"] = (
        "VERIFIED"
        if verification.get("reuse_verified") is True
        else "BLOCKED"
    )

    decision = evaluate_media_reuse(
        decision_asset,
        values,
    )

    with db_engine.begin() as conn:
        conn.execute(
            insert(rights_verifications_table).values(**values)
        )
        conn.execute(
            update(media_assets_table)
            .where(media_assets_table.c.asset_id == asset_id)
            .values(
                licence_code=values.get("licence_code"),
                commercial_use_allowed=values.get(
                    "commercial_use_allowed"
                ),
                modification_allowed=values.get(
                    "modification_allowed"
                ),
                attribution_required=values.get(
                    "attribution_required"
                ),
                verified_at=values.get("verified_at"),
                verified_by=values.get("verified_by"),
                evidence_url=values.get("evidence_url"),
                reuse_verified=bool(decision["allowed"]),
                verification_status=(
                    "VERIFIED"
                    if decision["allowed"]
                    else "BLOCKED"
                ),
                updated_at=now,
            )
        )

    return {
        "verification_id": values["verification_id"],
        "asset_id": asset_id,
        "decision": decision,
    }


def assert_media_reusable(
    asset_id: str,
    *,
    commercial_context: bool = True,
    editing_required: bool = True,
    db_engine: Engine = engine,
) -> dict[str, Any]:
    asset = get_media_asset(asset_id, db_engine=db_engine)

    if asset is None:
        raise MediaReuseBlockedError(
            f"{asset_id}: ASSET_MISSING"
        )

    rights = get_rights_for_asset(
        asset_id,
        db_engine=db_engine,
    )
    latest = rights[0] if rights else None

    decision = evaluate_media_reuse(
        asset,
        latest,
        commercial_context=commercial_context,
        editing_required=editing_required,
    )

    if not decision["allowed"]:
        raise MediaReuseBlockedError(
            f"{asset_id}: "
            + ", ".join(decision["reasons"])
        )

    return {
        "asset": asset,
        "rights": latest,
        "decision": decision,
    }


def stage4_status(
    db_engine: Engine = engine,
) -> dict[str, Any]:
    existing = set(inspect(db_engine).get_table_names())
    schema_ready = STAGE4_TABLE_NAMES.issubset(existing)

    source_count = 0
    media_asset_count = 0
    rights_count = 0
    verified_count = 0

    if schema_ready:
        with db_engine.connect() as conn:
            source_count = len(
                conn.execute(select(sources_table.c.id)).all()
            )
            media_asset_count = len(
                conn.execute(select(media_assets_table.c.id)).all()
            )
            rights_count = len(
                conn.execute(
                    select(rights_verifications_table.c.id)
                ).all()
            )
            verified_count = len(
                conn.execute(
                    select(media_assets_table.c.id).where(
                        media_assets_table.c.reuse_verified.is_(True)
                    )
                ).all()
            )

    rights_policy_path = POLICY_ROOT / "rights_policy.json"
    trust_policy_path = POLICY_ROOT / "source_trust_policy.json"

    return {
        "brand": "MDRN SportsQ",
        "stage": 4,
        "capability": "SOURCE_RIGHTS_INFRASTRUCTURE",
        "schema_ready": schema_ready,
        "tables": sorted(STAGE4_TABLE_NAMES),
        "source_count": source_count,
        "media_asset_count": media_asset_count,
        "rights_verification_count": rights_count,
        "reuse_verified_media_count": verified_count,
        "reuse_gate": "REUSE_VERIFIED",
        "reuse_policy": "FAIL_CLOSED",
        "network_media_downloads_enabled": False,
        "external_media_auto_import_enabled": False,
        "official_sources_are_media_licences": False,
        "rights_policy_loaded": (
            rights_policy_path.exists()
            and bool(_load_json(rights_policy_path))
        ),
        "trust_policy_loaded": (
            trust_policy_path.exists()
            and bool(_load_json(trust_policy_path))
        ),
        "prediction_safety": {
            "prediction_engine_rewritten": False,
            "live_prediction_rows_modified": False,
            "existing_prediction_locks_modified": False,
            "historical_holdout_touched": False,
        },
    }
