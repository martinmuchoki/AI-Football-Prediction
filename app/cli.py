from __future__ import annotations

import argparse
import asyncio
from datetime import date

from app.config import get_settings
from app.db import SessionLocal, init_db
from app.providers.api_football import ApiFootballClient
from app.providers.live_score_api import LiveScoreApiClient
from app.providers.football_data import FootballDataClient
from app.providers.public_web import PublicWebClient
from app.services.fixture_ingestor import refresh_unfinished_results, sync_fixture_window
from app.services.live_score_ingestor import sync_live_score_fixtures, sync_live_score_history
from app.services.football_data_ingestor import sync_football_data_history, sync_football_data_history_file
from app.services.feature_engineering import build_feature_dataset
from app.services.baseline_models import evaluate_baselines
from app.services.ensemble_models import evaluate_phase4_ensemble
from app.services.market_residual import evaluate_market_residual
from app.services.high_confidence_selector import run_selector_csv
from app.services.live_prediction_engine import generate_live_predictions, list_live_predictions
from app.services.prediction_grading import grade_live_predictions, performance_summary
from app.services.social_content import generate_social_package
from app.services.source_registry import list_sources, resolve_field, seed_default_sources, upsert_source
from app.services.web_collector import collect_public_url
from app.services.odds_history import capture_pre_match_odds, list_odds_history, odds_movement
from app.services.source_health import run_source_health_checks
from app.services.market_refresh import refresh_live_market
from app.services.web_targets import add_web_target, collect_due_web_targets, list_web_targets
from app.services.source_policy import list_source_policies, seed_known_source_policies, upsert_source_policy
from app.providers.openfootball_json import OpenFootballJsonClient
from app.services.openfootball_sync import store_openfootball_document, crosscheck_openfootball_vs_live
from app.services.fixture_reconciliation import (
    reconcile_openfootball_payload,
    reconciliation_summary,
    reconciliation_issues,
)


def _date(value: str) -> date:
    return date.fromisoformat(value)


async def _sync_api_football(args) -> None:
    settings = get_settings()
    settings.require_api_key()
    with SessionLocal() as session:
        async with ApiFootballClient(settings) as client:
            summary = await sync_fixture_window(
                session,
                client,
                league=args.league,
                season=args.season,
                from_date=args.from_date,
                to_date=args.to_date,
            )
    print(dict(summary))


async def _refresh_api_football(args) -> None:
    settings = get_settings()
    settings.require_api_key()
    with SessionLocal() as session:
        async with ApiFootballClient(settings) as client:
            summary = await refresh_unfinished_results(
                session,
                client,
                lookback_hours=args.lookback_hours or settings.result_lookback_hours,
            )
    print(dict(summary))


async def _sync_live_fixtures(args) -> None:
    settings = get_settings()
    settings.require_live_score_credentials()
    with SessionLocal() as session:
        async with LiveScoreApiClient(settings) as client:
            summary = await sync_live_score_fixtures(
                session,
                client,
                competition_id=args.competition,
                season=args.season,
                page=args.page,
                start_page=args.start_page,
                max_pages=args.max_pages,
            )
    print(dict(summary))


async def _sync_live_history(args) -> None:
    settings = get_settings()
    settings.require_live_score_credentials()
    with SessionLocal() as session:
        async with LiveScoreApiClient(settings) as client:
            summary = await sync_live_score_history(
                session,
                client,
                competition_id=args.competition,
                season=args.season,
                from_date=args.from_date,
                to_date=args.to_date,
                page=args.page,
                start_page=args.start_page,
                max_pages=args.max_pages,
            )
    print(dict(summary))


def _add_pagination_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--page",
        type=int,
        help="Fetch exactly one page (diagnostics/manual recovery).",
    )
    parser.add_argument(
        "--start-page",
        type=int,
        default=1,
        help="Starting page for automatic pagination; default 1.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        help="Safety limit for auto-pagination; defaults to LS_MAX_PAGES.",
    )



async def _sync_football_data_history(args) -> None:
    settings = get_settings()
    with SessionLocal() as session:
        if args.file:
            summary = sync_football_data_history_file(
                session,
                file_path=args.file,
                season=args.season,
                league_code=args.league,
            )
        else:
            async with FootballDataClient(settings) as client:
                summary = await sync_football_data_history(
                    session,
                    client,
                    season=args.season,
                    league_code=args.league,
                )
    print(dict(summary))



def _build_features(args) -> None:
    with SessionLocal() as session:
        summary = build_feature_dataset(
            session,
            output_path=args.output,
            provider=args.provider,
            from_season=args.from_season,
            to_season=args.to_season,
        )
    print(dict(summary))


def _evaluate_baselines(args) -> None:
    summary = evaluate_baselines(
        train_path=args.train,
        validation_path=args.validation,
        output_path=args.output,
    )
    print(dict(summary))



def _evaluate_ensemble(args) -> None:
    summary = evaluate_phase4_ensemble(
        fold1_train_path=args.fold1_train,
        fold1_validation_path=args.fold1_validation,
        fold2_train_path=args.fold2_train,
        fold2_validation_path=args.fold2_validation,
        output_path=args.output,
    )
    print(dict(summary))



def _evaluate_market_residual(args) -> None:
    summary = evaluate_market_residual(
        fold1_train_path=args.fold1_train,
        fold1_validation_path=args.fold1_validation,
        fold2_train_path=args.fold2_train,
        fold2_validation_path=args.fold2_validation,
        output_path=args.output,
    )
    print(dict(summary))


def _select_high_confidence(args) -> None:
    print(dict(run_selector_csv(
        input_path=args.input,
        output_path=args.output,
        model_prefix=args.model_prefix,
        threshold=args.threshold,
    )))



def _run_live_predictions(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = generate_live_predictions(
            session,
            competition_id=args.competition,
            season=args.season,
            threshold=args.threshold,
            limit=args.limit,
        )
    compact = {key: value for key, value in summary.items() if key != "predictions"}
    new_high = [row for row in summary.get("predictions", []) if row.get("publish")]
    compact["new_high_confidence_predictions"] = new_high
    print(compact)


def _list_live_predictions(args) -> None:
    init_db()
    with SessionLocal() as session:
        rows = list_live_predictions(
            session,
            competition_id=args.competition,
            season=args.season,
            high_confidence_only=args.high_confidence_only,
        )
    print({"status": "success", "count": len(rows), "predictions": rows})



def _grade_live_predictions(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = grade_live_predictions(
            session,
            competition_id=args.competition,
            season=args.season,
        )
    print(summary)


def _live_performance(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = performance_summary(
            session,
            competition_id=args.competition,
            season=args.season,
            high_confidence_only=args.high_confidence_only,
        )
    print(summary)



def _generate_social_content(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = generate_social_package(
            session,
            competition_id=args.competition,
            season=args.season,
            output_dir=args.output_dir,
            high_confidence_only=not args.include_pass,
            make_video=not args.no_video,
        )
    print(summary)




def _seed_data_sources(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = seed_default_sources(session)
    print(summary)


def _list_data_sources(args) -> None:
    init_db()
    with SessionLocal() as session:
        rows = list_sources(session)
    print({"status": "success", "count": len(rows), "sources": rows})


def _add_web_source(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = upsert_source(
            session,
            slug=args.slug,
            name=args.name,
            source_type="official-web" if args.official else "web",
            priority=args.priority,
            base_url=args.base_url,
            parser_version=args.parser_version,
            enabled=not args.disabled,
        )
    print(summary)


async def _collect_public_url(args) -> None:
    settings = get_settings()
    init_db()
    with SessionLocal() as session:
        async with PublicWebClient(settings) as client:
            summary = await collect_public_url(
                session,
                client,
                source_slug=args.source,
                url=args.url,
            )
    print(summary)


def _resolve_source_field(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = resolve_field(
            session,
            entity_type=args.entity_type,
            entity_key=args.entity_key,
            field_name=args.field,
        )
    print(summary)



def _capture_odds_snapshots(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = capture_pre_match_odds(
            session,
            competition_id=args.competition,
            season=args.season,
            limit=args.limit,
        )
    print(summary)


def _list_odds_history(args) -> None:
    init_db()
    with SessionLocal() as session:
        rows = list_odds_history(
            session,
            competition_id=args.competition,
            season=args.season,
            provider_fixture_id=args.fixture_id,
            limit=args.limit,
        )
    print({"status": "success", "count": len(rows), "history": rows})


def _odds_movement(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = odds_movement(
            session,
            provider_fixture_id=args.fixture_id,
        )
    print(summary)




def _run_source_health_checks(args) -> None:
    init_db()
    settings = get_settings()
    with SessionLocal() as session:
        summary = asyncio.run(
            run_source_health_checks(
                session,
                settings,
                only_slug=args.source,
            )
        )
    print(summary)


def _refresh_live_market(args) -> None:
    init_db()
    settings = get_settings()
    with SessionLocal() as session:
        summary = asyncio.run(
            refresh_live_market(
                session,
                settings,
                competition_id=args.competition,
                season=args.season,
                threshold=args.threshold,
                max_pages=args.max_pages,
            )
        )
    print(summary)



def _add_web_target(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = add_web_target(
            session,
            source_slug=args.source,
            url=args.url,
            interval_minutes=args.interval_minutes,
            enabled=not args.disabled,
        )
    print(summary)


def _list_web_targets(args) -> None:
    init_db()
    with SessionLocal() as session:
        rows = list_web_targets(session)
    print({"status": "success", "count": len(rows), "targets": rows})


def _collect_web_targets(args) -> None:
    init_db()
    settings = get_settings()
    with SessionLocal() as session:
        summary = asyncio.run(
            collect_due_web_targets(
                session,
                settings,
                force=args.force,
                only_source=args.source,
            )
        )
    print(summary)



def _seed_source_policies(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = seed_known_source_policies(session)
    print(summary)


def _list_source_policies(args) -> None:
    init_db()
    with SessionLocal() as session:
        rows = list_source_policies(session)
    print({"status": "success", "count": len(rows), "policies": rows})


def _set_source_policy(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = upsert_source_policy(
            session,
            source_slug=args.source,
            review_status=args.review_status,
            license_id=args.license_id,
            license_url=args.license_url,
            terms_url=args.terms_url,
            collection_allowed=args.collection_allowed,
            redistribution_allowed=args.redistribution_allowed,
            commercial_use_allowed=args.commercial_use_allowed,
            review_note=args.note,
        )
    print(summary)


def _sync_openfootball_epl(args) -> None:
    init_db()
    settings = get_settings()

    async def run():
        async with OpenFootballJsonClient(
            timeout_seconds=settings.public_web_timeout_seconds
        ) as client:
            return await client.fetch_league(
                season_label=args.season_label,
                league_file="en.1.json",
            )

    with SessionLocal() as session:
        seed_known_source_policies(session)
        document = asyncio.run(run())
        stored = store_openfootball_document(
            session,
            document=document,
            season_label=args.season_label,
        )
        crosscheck = crosscheck_openfootball_vs_live(
            session,
            payload=document.payload,
            competition_id=args.competition,
            season=args.season,
        )
    print({"status": "success", "stored": stored, "crosscheck": crosscheck})




def _reconcile_openfootball_epl(args) -> None:
    init_db()
    settings = get_settings()

    async def run():
        async with OpenFootballJsonClient(
            timeout_seconds=settings.public_web_timeout_seconds
        ) as client:
            return await client.fetch_league(
                season_label=args.season_label,
                league_file="en.1.json",
            )

    document = asyncio.run(run())
    with SessionLocal() as session:
        seed_known_source_policies(session)
        store_openfootball_document(
            session,
            document=document,
            season_label=args.season_label,
        )
        summary = reconcile_openfootball_payload(
            session,
            payload=document.payload,
            competition_id=args.competition,
            season=args.season,
        )
    print(summary)


def _reconciliation_summary(args) -> None:
    init_db()
    with SessionLocal() as session:
        summary = reconciliation_summary(
            session,
            competition_id=args.competition,
            season=args.season,
        )
    print(summary)


def _reconciliation_issues(args) -> None:
    init_db()
    with SessionLocal() as session:
        rows = reconciliation_issues(
            session,
            competition_id=args.competition,
            season=args.season,
            limit=args.limit,
        )
    print({"status": "success", "count": len(rows), "issues": rows})


def main() -> None:
    parser = argparse.ArgumentParser(prog="ai-football")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("init-db")

    sync = sub.add_parser("sync-fixtures", help="API-Football fixture sync")
    sync.add_argument("--league", type=int, required=True)
    sync.add_argument("--season", type=int, required=True)
    sync.add_argument("--from", dest="from_date", type=_date, required=True)
    sync.add_argument("--to", dest="to_date", type=_date, required=True)

    refresh = sub.add_parser("refresh-results", help="API-Football result refresh")
    refresh.add_argument("--lookback-hours", type=int)

    live_fix = sub.add_parser(
        "sync-live-fixtures",
        help="Live Score API current fixture sync with automatic pagination/retry",
    )
    live_fix.add_argument("--competition", type=int, required=True)
    live_fix.add_argument("--season", type=int, required=True)
    _add_pagination_args(live_fix)

    live_hist = sub.add_parser(
        "sync-live-history",
        help="Live Score API finished match sync with automatic pagination/retry",
    )
    live_hist.add_argument("--competition", type=int, required=True)
    live_hist.add_argument("--season", type=int, required=True)
    live_hist.add_argument("--from", dest="from_date", type=_date)
    live_hist.add_argument("--to", dest="to_date", type=_date)
    _add_pagination_args(live_hist)

    fd_hist = sub.add_parser(
        "sync-football-data-history",
        help="Football-Data.co.uk historical CSV sync",
    )
    fd_hist.add_argument("--season", type=int, required=True)
    fd_hist.add_argument(
        "--league",
        default="E0",
        help="Football-Data league code; E0 = Premier League",
    )
    fd_hist.add_argument(
        "--file",
        help="Import a local Football-Data CSV instead of downloading it",
    )


    features = sub.add_parser(
        "build-features",
        help="Build leakage-safe Phase 2 pre-match feature dataset",
    )
    features.add_argument("--provider", default="football-data-csv")
    features.add_argument("--from-season", type=int, required=True)
    features.add_argument("--to-season", type=int, required=True)
    features.add_argument("--output", required=True)

    baselines = sub.add_parser(
        "evaluate-baselines",
        help="Evaluate Phase 3 baseline models on a chronological validation set",
    )
    baselines.add_argument("--train", required=True)
    baselines.add_argument("--validation", required=True)
    baselines.add_argument("--output", required=True)

    ensemble = sub.add_parser(
        "evaluate-ensemble",
        help="Evaluate Phase 4 ML ensemble and calibration with two chronological folds",
    )
    ensemble.add_argument("--fold1-train", required=True)
    ensemble.add_argument("--fold1-validation", required=True)
    ensemble.add_argument("--fold2-train", required=True)
    ensemble.add_argument("--fold2-validation", required=True)
    ensemble.add_argument("--output", required=True)

    residual = sub.add_parser(
        "evaluate-market-residual",
        help="Evaluate conservative market-anchored residual corrections",
    )
    residual.add_argument("--fold1-train", required=True)
    residual.add_argument("--fold1-validation", required=True)
    residual.add_argument("--fold2-train", required=True)
    residual.add_argument("--fold2-validation", required=True)
    residual.add_argument("--output", required=True)

    selector = sub.add_parser(
        "select-high-confidence",
        help="Apply the frozen Phase 5 high-confidence selector",
    )
    selector.add_argument("--input", required=True)
    selector.add_argument("--output", required=True)
    selector.add_argument("--model-prefix", default="bookmaker_open")
    selector.add_argument("--threshold", type=float, default=0.65)

    live = sub.add_parser(
        "run-live-predictions",
        help="Lock opening-odds predictions for upcoming Live Score fixtures",
    )
    live.add_argument("--competition", type=int, default=2)
    live.add_argument("--season", type=int, default=2026)
    live.add_argument("--threshold", type=float, default=0.65)
    live.add_argument("--limit", type=int)

    live_list = sub.add_parser(
        "list-live-predictions",
        help="List locked live predictions",
    )
    live_list.add_argument("--competition", type=int, default=2)
    live_list.add_argument("--season", type=int, default=2026)
    live_list.add_argument("--high-confidence-only", action="store_true")

    grade = sub.add_parser(
        "grade-live-predictions",
        help="Grade locked live predictions whose fixtures are finished",
    )
    grade.add_argument("--competition", type=int, default=2)
    grade.add_argument("--season", type=int, default=2026)

    perf = sub.add_parser(
        "live-performance",
        help="Show accuracy, log loss and Brier score for graded live predictions",
    )
    perf.add_argument("--competition", type=int, default=2)
    perf.add_argument("--season", type=int, default=2026)
    perf.add_argument("--high-confidence-only", action="store_true")

    social = sub.add_parser(
        "generate-social-content",
        help="Generate Instagram-ready prediction cards, captions and an optional static MP4",
    )
    social.add_argument("--competition", type=int, default=2)
    social.add_argument("--season", type=int, default=2026)
    social.add_argument("--output-dir", required=True)
    social.add_argument("--include-pass", action="store_true")
    social.add_argument("--no-video", action="store_true")

    sub.add_parser(
        "seed-data-sources",
        help="Seed the Phase 10A multi-source registry with existing trusted providers",
    )

    sub.add_parser(
        "list-data-sources",
        help="List source priority, parser version, and collector health",
    )

    web_source = sub.add_parser(
        "add-web-source",
        help="Register an explicitly approved public/official website source",
    )
    web_source.add_argument("--slug", required=True)
    web_source.add_argument("--name", required=True)
    web_source.add_argument("--base-url", required=True)
    web_source.add_argument("--priority", type=int, default=40)
    web_source.add_argument("--parser-version", default="web-v1")
    web_source.add_argument("--official", action="store_true")
    web_source.add_argument("--disabled", action="store_true")

    collect_web = sub.add_parser(
        "collect-public-url",
        help="Collect an allowlisted public page while respecting robots.txt and safety limits",
    )
    collect_web.add_argument("--source", required=True)
    collect_web.add_argument("--url", required=True)

    resolve = sub.add_parser(
        "resolve-source-field",
        help="Resolve a field using source priority and show conflicts/provenance",
    )
    resolve.add_argument("--entity-type", required=True)
    resolve.add_argument("--entity-key", required=True)
    resolve.add_argument("--field", required=True)

    odds_capture = sub.add_parser(
        "capture-odds-snapshots",
        help="Capture each distinct upcoming Live Score pre-match 1X2 odds state",
    )
    odds_capture.add_argument("--competition", type=int, default=2)
    odds_capture.add_argument("--season", type=int, default=2026)
    odds_capture.add_argument("--limit", type=int)

    odds_list = sub.add_parser(
        "list-odds-history",
        help="List stored pre-match odds history",
    )
    odds_list.add_argument("--competition", type=int, default=2)
    odds_list.add_argument("--season", type=int, default=2026)
    odds_list.add_argument("--fixture-id", type=int)
    odds_list.add_argument("--limit", type=int, default=50)

    movement = sub.add_parser(
        "odds-movement",
        help="Compare the first and latest stored odds snapshot for a fixture",
    )
    movement.add_argument("--fixture-id", type=int, required=True)

    health_check = sub.add_parser(
        "check-source-health",
        help="Run credential-safe health checks for registered data sources",
    )
    health_check.add_argument("--source", help="Optional source slug to check")

    market_refresh = sub.add_parser(
        "refresh-live-market",
        help="Refresh Live Score fixtures, capture changed odds, and lock only new predictions",
    )
    market_refresh.add_argument("--competition", type=int, default=2)
    market_refresh.add_argument("--season", type=int, default=2026)
    market_refresh.add_argument("--threshold", type=float, default=0.65)
    market_refresh.add_argument("--max-pages", type=int)

    web_target = sub.add_parser(
        "add-web-target",
        help="Add a scheduled URL to an explicitly registered web/official-web source",
    )
    web_target.add_argument("--source", required=True)
    web_target.add_argument("--url", required=True)
    web_target.add_argument("--interval-minutes", type=int, default=60)
    web_target.add_argument("--disabled", action="store_true")

    sub.add_parser(
        "list-web-targets",
        help="List scheduled public-web collection targets",
    )

    collect_targets = sub.add_parser(
        "collect-web-targets",
        help="Collect due allowlisted public-web targets; robots.txt and safety controls remain enforced",
    )
    collect_targets.add_argument("--source")
    collect_targets.add_argument("--force", action="store_true")

    sub.add_parser(
        "seed-source-policies",
        help="Seed reviewed licensing/compliance policies for known sources",
    )

    sub.add_parser(
        "list-source-policies",
        help="List collection and redistribution policy status for all sources",
    )

    set_policy = sub.add_parser(
        "set-source-policy",
        help="Set the reviewed compliance policy for a registered source",
    )
    set_policy.add_argument("--source", required=True)
    set_policy.add_argument("--review-status", required=True)
    set_policy.add_argument("--license-id")
    set_policy.add_argument("--license-url")
    set_policy.add_argument("--terms-url")
    set_policy.add_argument("--collection-allowed", action="store_true")
    set_policy.add_argument("--redistribution-allowed", action="store_true")
    set_policy.add_argument("--commercial-use-allowed", action="store_true")
    set_policy.add_argument("--note")

    openfootball = sub.add_parser(
        "sync-openfootball-epl",
        help="Fetch CC0 OpenFootball EPL JSON, store provenance, and cross-check Live Score",
    )
    openfootball.add_argument("--season-label", default="2026-27")
    openfootball.add_argument("--competition", type=int, default=2)
    openfootball.add_argument("--season", type=int, default=2026)

    reconcile = sub.add_parser(
        "reconcile-openfootball-epl",
        help="Persist canonical Live Score vs OpenFootball fixture agreement and conflicts",
    )
    reconcile.add_argument("--season-label", default="2026-27")
    reconcile.add_argument("--competition", type=int, default=2)
    reconcile.add_argument("--season", type=int, default=2026)

    reconcile_summary = sub.add_parser(
        "reconciliation-summary",
        help="Show persisted multi-source fixture agreement and quality gate",
    )
    reconcile_summary.add_argument("--competition", type=int, default=2)
    reconcile_summary.add_argument("--season", type=int, default=2026)

    reconcile_issues = sub.add_parser(
        "reconciliation-issues",
        help="List multi-source fixture conflicts, source lag, and missing matches",
    )
    reconcile_issues.add_argument("--competition", type=int, default=2)
    reconcile_issues.add_argument("--season", type=int, default=2026)
    reconcile_issues.add_argument("--limit", type=int, default=100)

    args = parser.parse_args()

    if args.command == "init-db":
        init_db()
        print("Database initialized.")
    elif args.command == "sync-fixtures":
        asyncio.run(_sync_api_football(args))
    elif args.command == "refresh-results":
        asyncio.run(_refresh_api_football(args))
    elif args.command == "sync-live-fixtures":
        asyncio.run(_sync_live_fixtures(args))
    elif args.command == "sync-live-history":
        asyncio.run(_sync_live_history(args))
    elif args.command == "sync-football-data-history":
        asyncio.run(_sync_football_data_history(args))
    elif args.command == "build-features":
        _build_features(args)
    elif args.command == "evaluate-baselines":
        _evaluate_baselines(args)
    elif args.command == "evaluate-ensemble":
        _evaluate_ensemble(args)
    elif args.command == "evaluate-market-residual":
        _evaluate_market_residual(args)
    elif args.command == "select-high-confidence":
        _select_high_confidence(args)
    elif args.command == "run-live-predictions":
        _run_live_predictions(args)
    elif args.command == "list-live-predictions":
        _list_live_predictions(args)
    elif args.command == "grade-live-predictions":
        _grade_live_predictions(args)
    elif args.command == "live-performance":
        _live_performance(args)
    elif args.command == "generate-social-content":
        _generate_social_content(args)
    elif args.command == "seed-data-sources":
        _seed_data_sources(args)
    elif args.command == "list-data-sources":
        _list_data_sources(args)
    elif args.command == "add-web-source":
        _add_web_source(args)
    elif args.command == "collect-public-url":
        asyncio.run(_collect_public_url(args))
    elif args.command == "resolve-source-field":
        _resolve_source_field(args)
    elif args.command == "capture-odds-snapshots":
        _capture_odds_snapshots(args)
    elif args.command == "list-odds-history":
        _list_odds_history(args)
    elif args.command == "odds-movement":
        _odds_movement(args)
    elif args.command == "check-source-health":
        _run_source_health_checks(args)
    elif args.command == "refresh-live-market":
        _refresh_live_market(args)
    elif args.command == "add-web-target":
        _add_web_target(args)
    elif args.command == "list-web-targets":
        _list_web_targets(args)
    elif args.command == "collect-web-targets":
        _collect_web_targets(args)
    elif args.command == "seed-source-policies":
        _seed_source_policies(args)
    elif args.command == "list-source-policies":
        _list_source_policies(args)
    elif args.command == "set-source-policy":
        _set_source_policy(args)
    elif args.command == "sync-openfootball-epl":
        _sync_openfootball_epl(args)
    elif args.command == "reconcile-openfootball-epl":
        _reconcile_openfootball_epl(args)
    elif args.command == "reconciliation-summary":
        _reconciliation_summary(args)
    elif args.command == "reconciliation-issues":
        _reconciliation_issues(args)


if __name__ == "__main__":
    main()
