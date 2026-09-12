from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class LeagueSeason(Base):
    __tablename__ = "league_seasons"
    __table_args__ = (
        UniqueConstraint("provider", "provider_league_id", "season", name="uq_league_season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), default="api-football", nullable=False)
    provider_league_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    country: Mapped[str | None] = mapped_column(String(120))
    competition_type: Mapped[str | None] = mapped_column(String(40))
    logo_url: Mapped[str | None] = mapped_column(Text)
    flag_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Team(Base):
    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("provider", "provider_team_id", name="uq_team_provider"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), default="api-football", nullable=False)
    provider_team_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    code: Mapped[str | None] = mapped_column(String(20))
    country: Mapped[str | None] = mapped_column(String(120))
    logo_url: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class Fixture(Base):
    __tablename__ = "fixtures"
    __table_args__ = (
        UniqueConstraint("provider", "provider_fixture_id", name="uq_fixture_provider"),
        CheckConstraint("home_team_id <> away_team_id", name="ck_teams_different"),
        Index("ix_fixtures_kickoff", "kickoff_utc"),
        Index("ix_fixtures_status", "status_short"),
        Index("ix_fixtures_finished", "is_finished"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    provider: Mapped[str] = mapped_column(String(40), default="api-football", nullable=False)
    provider_fixture_id: Mapped[int] = mapped_column(BigInteger, nullable=False)

    league_season_id: Mapped[int] = mapped_column(ForeignKey("league_seasons.id"), nullable=False)
    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)

    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str | None] = mapped_column(String(64))
    round_name: Mapped[str | None] = mapped_column(String(120))
    referee: Mapped[str | None] = mapped_column(String(200))

    venue_id: Mapped[int | None] = mapped_column(BigInteger)
    venue_name: Mapped[str | None] = mapped_column(String(200))
    venue_city: Mapped[str | None] = mapped_column(String(160))

    status_short: Mapped[str] = mapped_column(String(20), nullable=False)
    status_long: Mapped[str | None] = mapped_column(String(80))
    elapsed: Mapped[int | None] = mapped_column(Integer)

    home_goals: Mapped[int | None] = mapped_column(Integer)
    away_goals: Mapped[int | None] = mapped_column(Integer)

    halftime_home: Mapped[int | None] = mapped_column(Integer)
    halftime_away: Mapped[int | None] = mapped_column(Integer)
    fulltime_home: Mapped[int | None] = mapped_column(Integer)
    fulltime_away: Mapped[int | None] = mapped_column(Integer)
    extratime_home: Mapped[int | None] = mapped_column(Integer)
    extratime_away: Mapped[int | None] = mapped_column(Integer)
    penalty_home: Mapped[int | None] = mapped_column(Integer)
    penalty_away: Mapped[int | None] = mapped_column(Integer)

    is_finished: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    result_1x2: Mapped[str | None] = mapped_column(String(8))

    raw_json: Mapped[str] = mapped_column(Text, nullable=False)
    provider_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    league_season: Mapped[LeagueSeason] = relationship()
    home_team: Mapped[Team] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped[Team] = relationship(foreign_keys=[away_team_id])


class SyncRun(Base):
    __tablename__ = "sync_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_type: Mapped[str] = mapped_column(String(40), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), default="api-football", nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    requested: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    received: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    inserted: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    updated: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    rejected: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    error_message: Mapped[str | None] = mapped_column(Text)


class LivePrediction(Base):
    """Immutable pre-match prediction snapshot for a live fixture.

    A row is inserted once per fixture/policy version and is never overwritten.
    This preserves the exact pre-kickoff odds and decision used for later grading.
    """

    __tablename__ = "live_predictions"
    __table_args__ = (
        UniqueConstraint("fixture_id", "policy_version", name="uq_live_prediction_fixture_policy"),
        Index("ix_live_predictions_kickoff", "kickoff_utc"),
        Index("ix_live_predictions_publish", "publish"),
        Index("ix_live_predictions_locked", "locked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="live-score-api")
    provider_fixture_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    competition_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)

    model_name: Mapped[str] = mapped_column(String(80), nullable=False, default="bookmaker_open")
    policy_version: Mapped[str] = mapped_column(String(80), nullable=False)
    market_source: Mapped[str] = mapped_column(String(80), nullable=False, default="odds.pre")

    home_odds: Mapped[float] = mapped_column(Float, nullable=False)
    draw_odds: Mapped[float] = mapped_column(Float, nullable=False)
    away_odds: Mapped[float] = mapped_column(Float, nullable=False)
    market_overround: Mapped[float] = mapped_column(Float, nullable=False)

    p_home: Mapped[float] = mapped_column(Float, nullable=False)
    p_draw: Mapped[float] = mapped_column(Float, nullable=False)
    p_away: Mapped[float] = mapped_column(Float, nullable=False)

    prediction: Mapped[str] = mapped_column(String(1), nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    threshold: Mapped[float] = mapped_column(Float, nullable=False)
    selector_label: Mapped[str] = mapped_column(String(32), nullable=False)
    publish: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    odds_snapshot_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    # Reserved for Phase 8 grading; nullable until the result is known.
    actual_result: Mapped[str | None] = mapped_column(String(1))
    is_correct: Mapped[bool | None] = mapped_column(Boolean)
    graded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    fixture: Mapped[Fixture] = relationship()


class OddsSnapshot(Base):
    """Time-series pre-match 1X2 odds snapshot.

    Unlike LivePrediction, which locks once and never changes, this table records
    each distinct pre-match market state. Unchanged prices are deduplicated.
    """

    __tablename__ = "odds_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "fixture_id",
            "market_source",
            "raw_hash",
            name="uq_odds_snapshot_fixture_market_hash",
        ),
        Index("ix_odds_snapshots_fixture", "fixture_id"),
        Index("ix_odds_snapshots_captured", "captured_at"),
        Index("ix_odds_snapshots_competition", "competition_id", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    fixture_id: Mapped[int] = mapped_column(ForeignKey("fixtures.id"), nullable=False)
    provider: Mapped[str] = mapped_column(String(40), nullable=False, default="live-score-api")
    provider_fixture_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    competition_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    market_source: Mapped[str] = mapped_column(String(80), nullable=False, default="odds.pre")

    home_odds: Mapped[float] = mapped_column(Float, nullable=False)
    draw_odds: Mapped[float] = mapped_column(Float, nullable=False)
    away_odds: Mapped[float] = mapped_column(Float, nullable=False)
    market_overround: Mapped[float] = mapped_column(Float, nullable=False)

    p_home: Mapped[float] = mapped_column(Float, nullable=False)
    p_draw: Mapped[float] = mapped_column(Float, nullable=False)
    p_away: Mapped[float] = mapped_column(Float, nullable=False)

    raw_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    kickoff_utc: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    fixture: Mapped[Fixture] = relationship()


class DataSource(Base):
    __tablename__ = "data_sources"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_data_source_slug"),
        Index("ix_data_sources_priority", "priority"),
        Index("ix_data_sources_health", "health_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    source_type: Mapped[str] = mapped_column(String(40), nullable=False)
    priority: Mapped[int] = mapped_column(Integer, nullable=False, default=100)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    base_url: Mapped[str | None] = mapped_column(Text)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False, default="v1")

    health_status: Mapped[str] = mapped_column(String(20), nullable=False, default="UNKNOWN")
    last_checked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error_message: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "canonical_url",
            "content_hash",
            name="uq_source_snapshot_content",
        ),
        Index("ix_source_snapshots_collected", "collected_at"),
        Index("ix_source_snapshots_hash", "content_hash"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(1200), nullable=False)
    status_code: Mapped[int] = mapped_column(Integer, nullable=False)
    content_type: Mapped[str | None] = mapped_column(String(160))
    content_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    body_text: Mapped[str | None] = mapped_column(Text)
    body_bytes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    robots_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    parser_version: Mapped[str] = mapped_column(String(80), nullable=False, default="v1")
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)

    source: Mapped[DataSource] = relationship()


class SourceObservation(Base):
    __tablename__ = "source_observations"
    __table_args__ = (
        Index("ix_source_observation_entity", "entity_type", "entity_key", "field_name"),
        Index("ix_source_observation_observed", "observed_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(80), nullable=False)
    entity_key: Mapped[str] = mapped_column(String(240), nullable=False)
    field_name: Mapped[str] = mapped_column(String(120), nullable=False)
    value_json: Mapped[str] = mapped_column(Text, nullable=False)
    source_url: Mapped[str | None] = mapped_column(Text)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw_hash: Mapped[str | None] = mapped_column(String(64))
    parser_version: Mapped[str | None] = mapped_column(String(80))

    source: Mapped[DataSource] = relationship()




class SourcePolicy(Base):
    """Compliance / licensing gate for external collection sources."""

    __tablename__ = "source_policies"
    __table_args__ = (
        UniqueConstraint("source_id", name="uq_source_policy_source"),
        Index("ix_source_policies_review_status", "review_status"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    review_status: Mapped[str] = mapped_column(String(40), nullable=False, default="UNREVIEWED")
    license_id: Mapped[str | None] = mapped_column(String(120))
    license_url: Mapped[str | None] = mapped_column(Text)
    terms_url: Mapped[str | None] = mapped_column(Text)
    collection_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    redistribution_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    commercial_use_allowed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    review_note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    source: Mapped[DataSource] = relationship()



class FixtureReconciliation(Base):
    """Persisted comparison of one canonical fixture across two providers."""

    __tablename__ = "fixture_reconciliations"
    __table_args__ = (
        UniqueConstraint(
            "competition_id",
            "season",
            "canonical_key",
            "primary_source",
            "secondary_source",
            name="uq_fixture_reconciliation_pair",
        ),
        Index("ix_fixture_reconciliation_status", "overall_status"),
        Index("ix_fixture_reconciliation_competition", "competition_id", "season"),
        Index("ix_fixture_reconciliation_checked", "checked_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    competition_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    canonical_key: Mapped[str] = mapped_column(String(320), nullable=False)

    primary_source: Mapped[str] = mapped_column(String(80), nullable=False)
    secondary_source: Mapped[str] = mapped_column(String(80), nullable=False)
    primary_fixture_id: Mapped[int | None] = mapped_column(BigInteger)
    secondary_entity_key: Mapped[str | None] = mapped_column(String(320))

    home_team: Mapped[str] = mapped_column(String(200), nullable=False)
    away_team: Mapped[str] = mapped_column(String(200), nullable=False)

    primary_date: Mapped[str | None] = mapped_column(String(10))
    secondary_date: Mapped[str | None] = mapped_column(String(10))
    date_status: Mapped[str] = mapped_column(String(30), nullable=False)

    primary_finished: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    primary_score: Mapped[str | None] = mapped_column(String(30))
    secondary_score: Mapped[str | None] = mapped_column(String(30))
    result_status: Mapped[str] = mapped_column(String(30), nullable=False)

    overall_status: Mapped[str] = mapped_column(String(30), nullable=False)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)


class MarketSafetyEvent(Base):
    """Immutable operational observation of unified market-safety state."""

    __tablename__ = "market_safety_events"
    __table_args__ = (
        Index(
            "ix_market_safety_event_competition",
            "competition_id",
            "season",
            "observed_at",
        ),
        Index(
            "ix_market_safety_event_status",
            "overall_status",
        ),
        Index(
            "ix_market_safety_event_transition",
            "transition",
        ),
        Index(
            "ix_market_safety_event_alert",
            "alert_type",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    competition_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    season: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    overall_status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    prediction_lock_allowed: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
    )

    prediction_gate_action: Mapped[str | None] = mapped_column(
        String(50),
    )

    reasons_json: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        default="[]",
    )

    primary_source_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="UNKNOWN",
    )

    reconciliation_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="UNINITIALIZED",
    )

    quality_gate: Mapped[str | None] = mapped_column(
        String(30),
    )

    reconciliation_freshness: Mapped[str | None] = mapped_column(
        String(30),
    )

    fixture_agreement_rate: Mapped[float | None] = mapped_column(
        Float,
    )

    issue_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    conflict_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    warning_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    odds_snapshots: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    previous_status: Mapped[str | None] = mapped_column(
        String(20),
    )

    transition: Mapped[str | None] = mapped_column(
        String(50),
    )

    alert_type: Mapped[str | None] = mapped_column(
        String(30),
    )

    observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class MarketSafetyAlertDelivery(Base):
    """Audit record for one external safety-alert delivery."""

    __tablename__ = "market_safety_alert_deliveries"
    __table_args__ = (
        UniqueConstraint(
            "event_id",
            "channel",
            name="uq_market_safety_alert_event_channel",
        ),
        Index(
            "ix_market_safety_alert_event",
            "event_id",
        ),
        Index(
            "ix_market_safety_alert_status",
            "delivery_status",
        ),
        Index(
            "ix_market_safety_alert_created",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    event_id: Mapped[int] = mapped_column(
        ForeignKey("market_safety_events.id"),
        nullable=False,
    )

    competition_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    season: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    alert_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    transition: Mapped[str | None] = mapped_column(
        String(50),
    )

    channel: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="webhook",
    )

    delivery_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    http_status: Mapped[int | None] = mapped_column(
        Integer,
    )

    error_type: Mapped[str | None] = mapped_column(
        String(120),
    )

    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class MarketSafetyIncident(Base):
    """Persistent lifecycle for a degraded/blocked market-safety incident."""

    __tablename__ = "market_safety_incidents"
    __table_args__ = (
        Index(
            "ix_market_safety_incident_competition_season",
            "competition_id",
            "season",
        ),
        Index(
            "ix_market_safety_incident_status",
            "status",
        ),
        Index(
            "ix_market_safety_incident_opened",
            "opened_at",
        ),
        Index(
            "ix_market_safety_incident_closed",
            "closed_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    competition_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    season: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
        default="OPEN",
    )

    opened_event_id: Mapped[int] = mapped_column(
        ForeignKey("market_safety_events.id"),
        nullable=False,
    )

    last_event_id: Mapped[int] = mapped_column(
        ForeignKey("market_safety_events.id"),
        nullable=False,
    )

    closed_event_id: Mapped[int | None] = mapped_column(
        ForeignKey("market_safety_events.id"),
    )

    opened_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    current_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    highest_severity: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    escalation_count: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    opened_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    last_observed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    escalated_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    closed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    duration_seconds: Mapped[float | None] = mapped_column(
        Float,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class MarketSafetySlaAlert(Base):
    """One persistent operational SLA escalation stage."""

    __tablename__ = "market_safety_sla_alerts"

    __table_args__ = (
        UniqueConstraint(
            "incident_id",
            "stage",
            name="uq_market_safety_sla_incident_stage",
        ),
        Index(
            "ix_market_safety_sla_incident",
            "incident_id",
        ),
        Index(
            "ix_market_safety_sla_stage",
            "stage",
        ),
        Index(
            "ix_market_safety_sla_created",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
    )

    incident_id: Mapped[int] = mapped_column(
        ForeignKey("market_safety_incidents.id"),
        nullable=False,
    )

    competition_id: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    season: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    stage: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )

    severity: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    market_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    threshold_minutes: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    incident_age_minutes: Mapped[float] = mapped_column(
        Float,
        nullable=False,
    )

    telegram_delivery_status: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="PENDING",
    )

    telegram_delivery_attempts: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=0,
    )

    telegram_http_status: Mapped[int | None] = mapped_column(
        Integer,
    )

    telegram_last_error_type: Mapped[str | None] = mapped_column(
        String(120),
    )

    telegram_delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utcnow,
        nullable=False,
    )


class WebCollectionTarget(Base):
    """A scheduled public-web URL attached to an explicitly registered web source."""

    __tablename__ = "web_collection_targets"
    __table_args__ = (
        UniqueConstraint("source_id", "url", name="uq_web_collection_target_source_url"),
        Index("ix_web_collection_targets_enabled", "enabled"),
        Index("ix_web_collection_targets_due", "last_collected_at"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_id: Mapped[int] = mapped_column(ForeignKey("data_sources.id"), nullable=False)
    url: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    interval_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=60)
    last_collected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_status: Mapped[str] = mapped_column(String(30), nullable=False, default="PENDING")
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False)

    source: Mapped[DataSource] = relationship()
