CREATE TABLE league_seasons (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(40) NOT NULL DEFAULT 'api-football',
    provider_league_id BIGINT NOT NULL,
    season INTEGER NOT NULL,
    name VARCHAR(200) NOT NULL,
    country VARCHAR(120),
    competition_type VARCHAR(40),
    logo_url TEXT,
    flag_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_league_season UNIQUE (provider, provider_league_id, season)
);

CREATE TABLE teams (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(40) NOT NULL DEFAULT 'api-football',
    provider_team_id BIGINT NOT NULL,
    name VARCHAR(200) NOT NULL,
    code VARCHAR(20),
    country VARCHAR(120),
    logo_url TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_team_provider UNIQUE (provider, provider_team_id)
);

CREATE TABLE fixtures (
    id SERIAL PRIMARY KEY,
    provider VARCHAR(40) NOT NULL DEFAULT 'api-football',
    provider_fixture_id BIGINT NOT NULL,
    league_season_id INTEGER NOT NULL REFERENCES league_seasons(id),
    home_team_id INTEGER NOT NULL REFERENCES teams(id),
    away_team_id INTEGER NOT NULL REFERENCES teams(id),
    kickoff_utc TIMESTAMPTZ NOT NULL,
    timezone VARCHAR(64),
    round_name VARCHAR(120),
    referee VARCHAR(200),
    venue_id BIGINT,
    venue_name VARCHAR(200),
    venue_city VARCHAR(160),
    status_short VARCHAR(20) NOT NULL,
    status_long VARCHAR(80),
    elapsed INTEGER,
    home_goals INTEGER,
    away_goals INTEGER,
    halftime_home INTEGER,
    halftime_away INTEGER,
    fulltime_home INTEGER,
    fulltime_away INTEGER,
    extratime_home INTEGER,
    extratime_away INTEGER,
    penalty_home INTEGER,
    penalty_away INTEGER,
    is_finished BOOLEAN NOT NULL DEFAULT FALSE,
    result_1x2 VARCHAR(8),
    raw_json TEXT NOT NULL,
    provider_updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fixture_provider UNIQUE (provider, provider_fixture_id),
    CONSTRAINT ck_teams_different CHECK (home_team_id <> away_team_id)
);

CREATE INDEX ix_fixtures_kickoff ON fixtures(kickoff_utc);
CREATE INDEX ix_fixtures_status ON fixtures(status_short);
CREATE INDEX ix_fixtures_finished ON fixtures(is_finished);

CREATE TABLE sync_runs (
    id SERIAL PRIMARY KEY,
    run_type VARCHAR(40) NOT NULL,
    provider VARCHAR(40) NOT NULL DEFAULT 'api-football',
    started_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    finished_at TIMESTAMPTZ,
    status VARCHAR(20) NOT NULL,
    requested INTEGER NOT NULL DEFAULT 0,
    received INTEGER NOT NULL DEFAULT 0,
    inserted INTEGER NOT NULL DEFAULT 0,
    updated INTEGER NOT NULL DEFAULT 0,
    rejected INTEGER NOT NULL DEFAULT 0,
    error_message TEXT
);


CREATE TABLE live_predictions (
    id SERIAL PRIMARY KEY,
    fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
    provider VARCHAR(40) NOT NULL DEFAULT 'live-score-api',
    provider_fixture_id BIGINT NOT NULL,
    competition_id BIGINT NOT NULL,
    season INTEGER NOT NULL,
    model_name VARCHAR(80) NOT NULL DEFAULT 'bookmaker_open',
    policy_version VARCHAR(80) NOT NULL,
    market_source VARCHAR(80) NOT NULL DEFAULT 'odds.pre',
    home_odds DOUBLE PRECISION NOT NULL,
    draw_odds DOUBLE PRECISION NOT NULL,
    away_odds DOUBLE PRECISION NOT NULL,
    market_overround DOUBLE PRECISION NOT NULL,
    p_home DOUBLE PRECISION NOT NULL,
    p_draw DOUBLE PRECISION NOT NULL,
    p_away DOUBLE PRECISION NOT NULL,
    prediction VARCHAR(1) NOT NULL,
    confidence DOUBLE PRECISION NOT NULL,
    threshold DOUBLE PRECISION NOT NULL,
    selector_label VARCHAR(32) NOT NULL,
    publish BOOLEAN NOT NULL DEFAULT FALSE,
    odds_snapshot_at TIMESTAMPTZ NOT NULL,
    locked_at TIMESTAMPTZ NOT NULL,
    kickoff_utc TIMESTAMPTZ NOT NULL,
    actual_result VARCHAR(1),
    is_correct BOOLEAN,
    graded_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_live_prediction_fixture_policy UNIQUE (fixture_id, policy_version)
);

CREATE INDEX ix_live_predictions_kickoff ON live_predictions(kickoff_utc);
CREATE INDEX ix_live_predictions_publish ON live_predictions(publish);
CREATE INDEX ix_live_predictions_locked ON live_predictions(locked_at);

CREATE TABLE data_sources (
    id SERIAL PRIMARY KEY,
    slug VARCHAR(80) NOT NULL,
    name VARCHAR(200) NOT NULL,
    source_type VARCHAR(40) NOT NULL,
    priority INTEGER NOT NULL DEFAULT 100,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    base_url TEXT,
    parser_version VARCHAR(80) NOT NULL DEFAULT 'v1',
    health_status VARCHAR(20) NOT NULL DEFAULT 'UNKNOWN',
    last_checked_at TIMESTAMPTZ,
    last_success_at TIMESTAMPTZ,
    last_error_at TIMESTAMPTZ,
    consecutive_failures INTEGER NOT NULL DEFAULT 0,
    error_message TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_data_source_slug UNIQUE (slug)
);
CREATE INDEX ix_data_sources_priority ON data_sources(priority);
CREATE INDEX ix_data_sources_health ON data_sources(health_status);

CREATE TABLE source_snapshots (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    source_url TEXT NOT NULL,
    canonical_url VARCHAR(1200) NOT NULL,
    status_code INTEGER NOT NULL,
    content_type VARCHAR(160),
    content_hash VARCHAR(64) NOT NULL,
    body_text TEXT,
    body_bytes INTEGER NOT NULL DEFAULT 0,
    robots_allowed BOOLEAN NOT NULL DEFAULT TRUE,
    parser_version VARCHAR(80) NOT NULL DEFAULT 'v1',
    collected_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_snapshot_content UNIQUE (source_id, canonical_url, content_hash)
);
CREATE INDEX ix_source_snapshots_collected ON source_snapshots(collected_at);
CREATE INDEX ix_source_snapshots_hash ON source_snapshots(content_hash);

CREATE TABLE source_observations (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    entity_type VARCHAR(80) NOT NULL,
    entity_key VARCHAR(240) NOT NULL,
    field_name VARCHAR(120) NOT NULL,
    value_json TEXT NOT NULL,
    source_url TEXT,
    observed_at TIMESTAMPTZ NOT NULL,
    collected_at TIMESTAMPTZ NOT NULL,
    raw_hash VARCHAR(64),
    parser_version VARCHAR(80)
);
CREATE INDEX ix_source_observation_entity ON source_observations(entity_type, entity_key, field_name);
CREATE INDEX ix_source_observation_observed ON source_observations(observed_at);


CREATE TABLE odds_snapshots (
    id SERIAL PRIMARY KEY,
    fixture_id INTEGER NOT NULL REFERENCES fixtures(id),
    provider VARCHAR(40) NOT NULL DEFAULT 'live-score-api',
    provider_fixture_id BIGINT NOT NULL,
    competition_id BIGINT NOT NULL,
    season INTEGER NOT NULL,
    market_source VARCHAR(80) NOT NULL DEFAULT 'odds.pre',
    home_odds DOUBLE PRECISION NOT NULL,
    draw_odds DOUBLE PRECISION NOT NULL,
    away_odds DOUBLE PRECISION NOT NULL,
    market_overround DOUBLE PRECISION NOT NULL,
    p_home DOUBLE PRECISION NOT NULL,
    p_draw DOUBLE PRECISION NOT NULL,
    p_away DOUBLE PRECISION NOT NULL,
    raw_hash VARCHAR(64) NOT NULL,
    captured_at TIMESTAMPTZ NOT NULL,
    kickoff_utc TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_odds_snapshot_fixture_market_hash UNIQUE (fixture_id, market_source, raw_hash)
);
CREATE INDEX ix_odds_snapshots_fixture ON odds_snapshots(fixture_id);
CREATE INDEX ix_odds_snapshots_captured ON odds_snapshots(captured_at);
CREATE INDEX ix_odds_snapshots_competition ON odds_snapshots(competition_id, season);


CREATE TABLE web_collection_targets (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    url TEXT NOT NULL,
    enabled BOOLEAN NOT NULL DEFAULT TRUE,
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    last_collected_at TIMESTAMPTZ,
    last_status VARCHAR(30) NOT NULL DEFAULT 'PENDING',
    last_error TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_web_collection_target_source_url UNIQUE (source_id, url)
);
CREATE INDEX ix_web_collection_targets_enabled ON web_collection_targets(enabled);
CREATE INDEX ix_web_collection_targets_due ON web_collection_targets(last_collected_at);


CREATE TABLE source_policies (
    id SERIAL PRIMARY KEY,
    source_id INTEGER NOT NULL REFERENCES data_sources(id),
    review_status VARCHAR(40) NOT NULL DEFAULT 'UNREVIEWED',
    license_id VARCHAR(120),
    license_url TEXT,
    terms_url TEXT,
    collection_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    redistribution_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    commercial_use_allowed BOOLEAN NOT NULL DEFAULT FALSE,
    reviewed_at TIMESTAMPTZ,
    review_note TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_source_policy_source UNIQUE (source_id)
);
CREATE INDEX ix_source_policies_review_status ON source_policies(review_status);


CREATE TABLE fixture_reconciliations (
    id SERIAL PRIMARY KEY,
    competition_id BIGINT NOT NULL,
    season INTEGER NOT NULL,
    canonical_key VARCHAR(320) NOT NULL,
    primary_source VARCHAR(80) NOT NULL,
    secondary_source VARCHAR(80) NOT NULL,
    primary_fixture_id BIGINT,
    secondary_entity_key VARCHAR(320),
    home_team VARCHAR(200) NOT NULL,
    away_team VARCHAR(200) NOT NULL,
    primary_date VARCHAR(10),
    secondary_date VARCHAR(10),
    date_status VARCHAR(30) NOT NULL,
    primary_finished BOOLEAN NOT NULL DEFAULT FALSE,
    primary_score VARCHAR(30),
    secondary_score VARCHAR(30),
    result_status VARCHAR(30) NOT NULL,
    overall_status VARCHAR(30) NOT NULL,
    checked_at TIMESTAMPTZ NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    CONSTRAINT uq_fixture_reconciliation_pair UNIQUE (
        competition_id, season, canonical_key, primary_source, secondary_source
    )
);
CREATE INDEX ix_fixture_reconciliation_status ON fixture_reconciliations(overall_status);
CREATE INDEX ix_fixture_reconciliation_competition ON fixture_reconciliations(competition_id, season);
CREATE INDEX ix_fixture_reconciliation_checked ON fixture_reconciliations(checked_at);
