from __future__ import annotations

from functools import lru_cache
from typing import List, Tuple

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_env: str = "development"
    database_url: str = "sqlite:///./data/ai_football.db"

    # API-Football (secondary / historical development provider)
    af_api_key: str = ""
    af_base_url: str = "https://v3.football.api-sports.io"
    af_timeout_seconds: float = 20.0
    af_max_retries: int = 3


    # Football-Data.co.uk (primary historical CSV provider)
    fd_base_url: str = "https://www.football-data.co.uk/mmz4281"
    fd_timeout_seconds: float = 30.0
    fd_max_retries: int = 4

    # Live Score API (primary current/live provider)
    ls_api_key: str = ""
    ls_api_secret: str = ""
    ls_base_url: str = "https://livescore-api.com/api-client"

    # Legacy general timeout is retained for backwards compatibility.
    ls_timeout_seconds: float = 30.0
    ls_connect_timeout_seconds: float = 10.0
    ls_read_timeout_seconds: float = 30.0
    ls_write_timeout_seconds: float = 10.0
    ls_pool_timeout_seconds: float = 10.0

    # Total attempts = LS_MAX_RETRIES. Backoff is exponential and capped.
    ls_max_retries: int = 4
    ls_retry_base_seconds: float = 1.0
    ls_retry_max_seconds: float = 8.0

    # Safety ceiling for automatic pagination.
    ls_max_pages: int = 50

    # API-Football league_id:season_start_year
    tracked_leagues: str = "39:2024"

    # MDRN SportsQ production market authority.
    # Provider-specific competition identity: API-Football EPL = 39.
    sportsq_primary_competitions: str = "39:2026"

    # Live Score competition_id:season_start_year
    ls_tracked_competitions: str = "2:2026"

    fixture_sync_every_hours: int = 6
    result_sync_every_minutes: int = 60
    result_lookback_hours: int = 36
    fixture_lookback_days: int = 1
    fixture_lookahead_days: int = 7

    # Our own API / multi-source public-web collector
    own_api_require_key: bool = False
    own_api_key: str = ""

    # Public web collection is opt-in by explicit domain allowlist.
    public_web_allowed_domains: str = ""
    public_web_user_agent: str = "MDRN-SportsQ-Collector/0.10"
    public_web_timeout_seconds: float = 20.0
    public_web_max_body_bytes: int = 2_000_000
    public_web_robots_fail_closed: bool = True

    # Phase 10A.2 operational cadence. These settings do not change prediction
    # policy; they only refresh source data and build auditable market history.
    live_market_refresh_every_minutes: int = 60
    source_health_every_minutes: int = 60
    source_health_stale_after_minutes: int = 180
    reconciliation_stale_after_minutes: int = 180
    source_health_failure_threshold: int = 3
    public_web_collect_every_minutes: int = 60

    # Phase 10A.12 external operational alerts.
    # Delivery is opt-in and transition-only.
    safety_alert_enabled: bool = False
    safety_alert_webhook_url: str = ""
    safety_alert_webhook_bearer_token: str = ""
    safety_alert_timeout_seconds: float = 10.0
    safety_alert_max_retries: int = 3

    # Phase 10A.13 native Telegram operational alerts.
    # Credentials belong only in .env and must never be logged.
    safety_alert_telegram_enabled: bool = False
    safety_alert_telegram_bot_token: str = ""
    safety_alert_telegram_chat_id: str = ""

    # v0.11.0 production reliability policy.
    # These thresholds control operational escalation only.
    # They never modify prediction outcomes or historical holdout data.
    incident_degraded_warn_minutes: int = 60
    incident_degraded_critical_minutes: int = 180
    incident_blocked_warn_minutes: int = 15
    incident_blocked_critical_minutes: int = 60
    operations_heartbeat_stale_minutes: int = 120

    def tracked_competitions(self) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        if not self.tracked_leagues.strip():
            return out
        for part in self.tracked_leagues.split(","):
            league, season = part.strip().split(":", 1)
            out.append((int(league), int(season)))
        return out

    def sportsq_competitions(self) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        if not self.sportsq_primary_competitions.strip():
            return out

        for part in self.sportsq_primary_competitions.split(","):
            league, season = part.strip().split(":", 1)
            out.append((int(league), int(season)))

        return out


    def live_score_competitions(self) -> List[Tuple[int, int]]:
        out: List[Tuple[int, int]] = []
        if not self.ls_tracked_competitions.strip():
            return out
        for part in self.ls_tracked_competitions.split(","):
            competition, season = part.strip().split(":", 1)
            out.append((int(competition), int(season)))
        return out


    def public_web_allowed_domain_list(self) -> List[str]:
        return [
            part.strip().lower().strip(".")
            for part in self.public_web_allowed_domains.split(",")
            if part.strip()
        ]

    def require_api_key(self) -> str:
        key = self.af_api_key.strip()
        if not key:
            raise RuntimeError("AF_API_KEY is missing. Put it in .env; never hard-code it.")
        return key

    def require_live_score_credentials(self) -> tuple[str, str]:
        key = self.ls_api_key.strip()
        secret = self.ls_api_secret.strip()
        if not key or not secret:
            raise RuntimeError(
                "LS_API_KEY / LS_API_SECRET missing. Put both in .env; never hard-code them."
            )
        return key, secret


@lru_cache
def get_settings() -> Settings:
    return Settings()
