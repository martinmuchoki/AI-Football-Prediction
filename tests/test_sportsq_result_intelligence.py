from __future__ import annotations

from datetime import datetime, timezone
import uuid

import pytest

from sqlalchemy import (
    insert,
    inspect,
    select,
    text,
)

from sqlalchemy.exc import DatabaseError

from app.models import Fixture, FixtureReconciliation

from app.services.result_verification import (
    API_FOOTBALL_SOURCE,
    LIVE_SCORE_SOURCE,
)

from app.services.sportsq_result_intelligence import (
    GRADING_VERSION,
    SCORECALL_GRADES_TABLE_NAME,
    ensure_scorecall_grading_schema,
    grade_verified_scorecall_locks,
    scorecall_grades_table,
    scorecall_performance_summary,
)

from app.services.sportsq_scorecall import (
    SCORECALL_TABLE_NAME,
    scorecall_locks_table,
)


COMPETITION_ID = 39
SEASON = 2026

_PROVIDER_COUNTER = 990000


def _now():
    return datetime.now(timezone.utc)


def _next_provider_fixture_id():
    global _PROVIDER_COUNTER
    _PROVIDER_COUNTER += 1
    return _PROVIDER_COUNTER


def _scalar_default(column, now):
    """
    Produce deterministic values only for required Fixture columns
    not explicitly handled below.
    """

    try:
        pytype = column.type.python_type
    except Exception:
        pytype = str

    # Fixture FK identifiers are handled explicitly in
    # _create_finished_fixture(). Never give both teams the
    # same generic integer fallback.
    if column.name in {
        "league_season_id",
        "home_team_id",
        "away_team_id",
    }:
        raise RuntimeError(
            f"Fixture FK must be supplied explicitly: {column.name}"
        )

    if pytype is int:
        return 1

    if pytype is float:
        return 0.0

    if pytype is bool:
        return False

    if pytype is datetime:
        return now

    if pytype is str:
        return f"test-{column.name}"

    return None


def _create_finished_fixture(session):
    now = _now()
    provider_fixture_id = _next_provider_fixture_id()

    # Discover the actual FK targets from the mapped Fixture table.
    # We create valid referenced rows on the SAME isolated pytest DB
    # instead of assuming IDs exist.
    fixture_table = Fixture.__table__

    fk_map = {}

    for column in fixture_table.columns:
        if column.foreign_keys:
            fk_map[column.name] = next(iter(column.foreign_keys))

    required_fks = {
        "league_season_id",
        "home_team_id",
        "away_team_id",
    }

    missing_fks = required_fks - set(fk_map)

    if missing_fks:
        raise RuntimeError(
            "Fixture FK contract missing: "
            + ", ".join(sorted(missing_fks))
        )

    bind = session.get_bind()

    def _target_table(column_name):
        fk = fk_map[column_name]
        return fk.column.table

    league_table = _target_table("league_season_id")
    team_table = _target_table("home_team_id")

    away_team_table = _target_table("away_team_id")

    if away_team_table.name != team_table.name:
        raise RuntimeError(
            "Home and away team FKs do not target same table."
        )

    # Build required referenced records dynamically from the real
    # SQLAlchemy table definitions.
    def _required_values(table, identity_seed, label):
        values = {}

        for column in table.columns:

            if column.primary_key and column.autoincrement:
                continue

            if column.default is not None:
                continue

            if column.server_default is not None:
                continue

            if column.nullable:
                continue

            name = column.name

            try:
                pytype = column.type.python_type
            except Exception:
                pytype = str

            lname = name.lower()

            if "provider" in lname and "id" in lname:
                values[name] = 800000 + identity_seed

            elif lname in {"provider", "source"}:
                values[name] = API_FOOTBALL_SOURCE

            elif "season" in lname and pytype is int:
                values[name] = SEASON

            elif "competition" in lname and pytype is int:
                values[name] = COMPETITION_ID

            elif "league" in lname and pytype is int:
                values[name] = COMPETITION_ID

            elif "name" in lname or "title" in lname:
                values[name] = f"{label}-{identity_seed}"

            elif "code" in lname:
                values[name] = f"T{identity_seed}"

            elif "country" in lname:
                values[name] = "Test"

            elif pytype is int:
                values[name] = identity_seed

            elif pytype is float:
                values[name] = 0.0

            elif pytype is bool:
                values[name] = True

            elif pytype is datetime:
                values[name] = now

            elif pytype is str:
                values[name] = f"{label}-{name}-{identity_seed}"

            else:
                raise RuntimeError(
                    f"Cannot synthesize required {table.name}.{name}"
                )

        return values


    def _insert_reference(table, seed, label):
        values = _required_values(
            table,
            seed,
            label,
        )

        result = session.execute(
            insert(table).values(**values)
        )

        session.flush()

        pk = result.inserted_primary_key

        if not pk or pk[0] is None:
            raise RuntimeError(
                f"Could not obtain PK for {table.name}"
            )

        return int(pk[0])


    # If LeagueSeason itself has FK dependencies, prefer an existing
    # row when the shared test setup has one. Otherwise synthesize the
    # row only when its required fields are self-contained.
    existing_league = session.execute(
        select(league_table)
        .limit(1)
    ).mappings().first()

    if existing_league is not None:
        league_pk_name = list(
            league_table.primary_key.columns
        )[0].name

        league_season_id = int(
            existing_league[league_pk_name]
        )

    else:
        league_season_id = _insert_reference(
            league_table,
            101,
            "ResultIntelligenceLeague",
        )


    # Teams must be different. Reuse two existing teams when possible.
    existing_teams = list(
        session.execute(
            select(team_table)
            .limit(2)
        ).mappings()
    )

    team_pk_name = list(
        team_table.primary_key.columns
    )[0].name

    if len(existing_teams) >= 2:

        home_team_id = int(
            existing_teams[0][team_pk_name]
        )

        away_team_id = int(
            existing_teams[1][team_pk_name]
        )

    else:

        if len(existing_teams) == 1:
            home_team_id = int(
                existing_teams[0][team_pk_name]
            )
        else:
            home_team_id = _insert_reference(
                team_table,
                201,
                "ResultIntelligenceHome",
            )

        away_team_id = _insert_reference(
            team_table,
            202,
            "ResultIntelligenceAway",
        )


    if home_team_id == away_team_id:
        raise RuntimeError(
            "Deterministic fixture generated identical teams."
        )


    explicit = {
        "provider": API_FOOTBALL_SOURCE,
        "provider_fixture_id": provider_fixture_id,

        "league_season_id": league_season_id,
        "home_team_id": home_team_id,
        "away_team_id": away_team_id,

        "competition_id": COMPETITION_ID,
        "season": SEASON,

        "is_finished": True,

        "status_short": "FT",
        "status_long": "Match Finished",

        "home_goals": 2,
        "away_goals": 1,

        "fulltime_home": 2,
        "fulltime_away": 1,

        "home_team": "Result Intelligence Home",
        "away_team": "Result Intelligence Away",

        "home_team_name": "Result Intelligence Home",
        "away_team_name": "Result Intelligence Away",

        "fixture_date": now,
        "kickoff_at": now,
        "date": now,

        "created_at": now,
        "updated_at": now,
    }

    values = {}

    for column in Fixture.__table__.columns:

        name = column.name

        if column.primary_key and column.autoincrement:
            continue

        if name in explicit:
            values[name] = explicit[name]
            continue

        if column.default is not None:
            continue

        if column.server_default is not None:
            continue

        if column.nullable:
            continue

        value = _scalar_default(
            column,
            now,
        )

        if value is None:
            raise RuntimeError(
                "Unable to generate deterministic Fixture value "
                f"for required column: {name}"
            )

        values[name] = value

    # Remove aliases that do not exist in this actual model.
    actual_columns = {
        column.name
        for column in Fixture.__table__.columns
    }

    values = {
        key: value
        for key, value in values.items()
        if key in actual_columns
    }

    # Re-apply explicit values for existing columns.
    for key, value in explicit.items():
        if key in actual_columns:
            values[key] = value

    result = session.execute(
        insert(Fixture.__table__).values(**values)
    )

    session.commit()

    fixture_id = int(
        result.inserted_primary_key[0]
    )

    fixture = session.get(
        Fixture,
        fixture_id,
    )

    assert fixture is not None

    assert int(
        fixture.provider_fixture_id
    ) == provider_fixture_id

    assert bool(
        fixture.is_finished
    ) is True

    return fixture


def _prepare_tables(session):

    bind = session.get_bind()

    scorecall_locks_table.metadata.create_all(
        bind,
        tables=[scorecall_locks_table],
        checkfirst=True,
    )

    ensure_scorecall_grading_schema(
        session
    )

    tables = set(
        inspect(bind).get_table_names()
    )

    assert SCORECALL_TABLE_NAME in tables
    assert SCORECALL_GRADES_TABLE_NAME in tables


def _insert_scorecall_lock(
    session,
    fixture,
    *,
    predicted_home=2,
    predicted_away=1,
):

    _prepare_tables(session)

    now = _now()

    values = {}

    for column in scorecall_locks_table.columns:

        name = column.name

        if name == "id":
            continue

        if name == "live_prediction_id":
            values[name] = (
                900000 + int(fixture.id)
            )

        elif name == "fixture_id":
            values[name] = int(
                fixture.id
            )

        elif name == "provider_fixture_id":
            values[name] = int(
                fixture.provider_fixture_id
            )

        elif name == "competition_id":
            values[name] = COMPETITION_ID

        elif name == "season":
            values[name] = SEASON

        elif name == "policy_version":
            values[name] = "verified-v1-test"

        elif name == "locked_prediction":
            values[name] = "H"

        elif name == "predicted_home_goals":
            values[name] = predicted_home

        elif name == "predicted_away_goals":
            values[name] = predicted_away

        elif name == "predicted_score":
            values[name] = (
                f"{predicted_home}-"
                f"{predicted_away}"
            )

        elif name == "scorecall_locked_at":
            values[name] = now

        elif name in (
            "created_at",
            "updated_at",
        ):
            values[name] = now

        elif (
            not column.nullable
            and column.default is None
            and column.server_default is None
        ):

            try:
                pytype = column.type.python_type
            except Exception:
                pytype = str

            if pytype is int:
                values[name] = 1

            elif pytype is float:
                values[name] = 0.0

            elif pytype is bool:
                values[name] = False

            elif pytype is datetime:
                values[name] = now

            else:
                values[name] = (
                    f"test-{name}"
                )

    result = session.execute(
        insert(
            scorecall_locks_table
        ).values(**values)
    )

    session.commit()

    return int(
        result.inserted_primary_key[0]
    )


def _insert_verification(
    session,
    fixture,
    *,
    date_status="MATCH",
    result_status="MATCH",
    overall_status="MATCH",
):

    now = _now()

    actual_home = (
        fixture.fulltime_home
        if fixture.fulltime_home is not None
        else fixture.home_goals
    )

    actual_away = (
        fixture.fulltime_away
        if fixture.fulltime_away is not None
        else fixture.away_goals
    )

    score = (
        f"{int(actual_home)}-"
        f"{int(actual_away)}"
    )

    row = FixtureReconciliation(
        competition_id=COMPETITION_ID,
        season=SEASON,

        canonical_key=(
            "result-intelligence-test|"
            f"{uuid.uuid4()}"
        ),

        primary_source=API_FOOTBALL_SOURCE,
        secondary_source=LIVE_SCORE_SOURCE,

        primary_fixture_id=int(
            fixture.provider_fixture_id
        ),

        secondary_entity_key=(
            "live-score-test-"
            f"{fixture.provider_fixture_id}"
        ),

        home_team="Result Intelligence Home",

        away_team="Result Intelligence Away",

        primary_date="2026-09-22",
        secondary_date="2026-09-22",

        date_status=date_status,

        primary_finished=True,

        primary_score=score,
        secondary_score=score,

        result_status=result_status,
        overall_status=overall_status,

        checked_at=now,
    )

    session.add(row)
    session.commit()
    session.refresh(row)

    return row


def _grade_rows(session):

    return list(
        session.execute(
            select(
                scorecall_grades_table
            )
        ).mappings()
    )


def test_result_intelligence_schema_is_separate_and_immutable(
    session,
):

    _prepare_tables(session)

    status = ensure_scorecall_grading_schema(
        session
    )

    assert status["immutable"] is True
    assert status["verified_results_only"] is True

    assert (
        status["grading_version"]
        == GRADING_VERSION
    )

    lock_columns = {
        c["name"]
        for c in inspect(
            session.get_bind()
        ).get_columns(
            SCORECALL_TABLE_NAME
        )
    }

    assert (
        "exact_score_correct"
        not in lock_columns
    )

    assert (
        "actual_score"
        not in lock_columns
    )

    assert (
        "verification_reconciliation_id"
        not in lock_columns
    )



def test_legacy_empty_grading_table_is_upgraded(
    session,
):
    bind = session.get_bind()

    if bind.dialect.name != "sqlite":
        pytest.fail("Expected isolated SQLite test DB.")

    # Reproduce the pre-verification production schema.
    with bind.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sportsq_scorecall_grades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scorecall_lock_id INTEGER NOT NULL,
                    live_prediction_id INTEGER NOT NULL,
                    fixture_id INTEGER NOT NULL,
                    provider_fixture_id INTEGER NOT NULL,
                    competition_id INTEGER NOT NULL,
                    season INTEGER NOT NULL,
                    predicted_home_goals INTEGER NOT NULL,
                    predicted_away_goals INTEGER NOT NULL,
                    actual_home_goals INTEGER NOT NULL,
                    actual_away_goals INTEGER NOT NULL,
                    predicted_score VARCHAR(20) NOT NULL,
                    actual_score VARCHAR(20) NOT NULL,
                    exact_score_correct BOOLEAN NOT NULL,
                    outcome_correct BOOLEAN NOT NULL,
                    grading_version VARCHAR(40) NOT NULL,
                    graded_at DATETIME NOT NULL,
                    UNIQUE (scorecall_lock_id),
                    UNIQUE (fixture_id, grading_version)
                )
                """
            )
        )

    status = ensure_scorecall_grading_schema(session)

    columns = {
        column["name"]
        for column in inspect(bind).get_columns(
            SCORECALL_GRADES_TABLE_NAME
        )
    }

    expected = {
        "verification_reconciliation_id",
        "verification_primary_source",
        "verification_secondary_source",
        "verification_date_status",
        "verification_result_status",
        "verification_overall_status",
        "verification_checked_at",
    }

    assert expected <= columns
    assert status["immutable"] is True
    assert status["verified_results_only"] is True

    # Prove migration is idempotent.
    ensure_scorecall_grading_schema(session)

    columns_after_second_run = {
        column["name"]
        for column in inspect(bind).get_columns(
            SCORECALL_GRADES_TABLE_NAME
        )
    }

    assert columns_after_second_run == columns


def test_populated_legacy_grading_table_fails_closed(
    session,
):
    bind = session.get_bind()

    if bind.dialect.name != "sqlite":
        pytest.fail("Expected isolated SQLite test DB.")

    with bind.begin() as conn:
        conn.execute(
            text(
                """
                CREATE TABLE sportsq_scorecall_grades (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    scorecall_lock_id INTEGER NOT NULL,
                    live_prediction_id INTEGER NOT NULL,
                    fixture_id INTEGER NOT NULL,
                    provider_fixture_id INTEGER NOT NULL,
                    competition_id INTEGER NOT NULL,
                    season INTEGER NOT NULL,
                    predicted_home_goals INTEGER NOT NULL,
                    predicted_away_goals INTEGER NOT NULL,
                    actual_home_goals INTEGER NOT NULL,
                    actual_away_goals INTEGER NOT NULL,
                    predicted_score VARCHAR(20) NOT NULL,
                    actual_score VARCHAR(20) NOT NULL,
                    exact_score_correct BOOLEAN NOT NULL,
                    outcome_correct BOOLEAN NOT NULL,
                    grading_version VARCHAR(40) NOT NULL,
                    graded_at DATETIME NOT NULL
                )
                """
            )
        )

        conn.execute(
            text(
                """
                INSERT INTO sportsq_scorecall_grades (
                    scorecall_lock_id,
                    live_prediction_id,
                    fixture_id,
                    provider_fixture_id,
                    competition_id,
                    season,
                    predicted_home_goals,
                    predicted_away_goals,
                    actual_home_goals,
                    actual_away_goals,
                    predicted_score,
                    actual_score,
                    exact_score_correct,
                    outcome_correct,
                    grading_version,
                    graded_at
                )
                VALUES (
                    1, 1, 1, 1, 39, 2026,
                    1, 0, 2, 2,
                    '1-0', '2-2',
                    0, 0,
                    'legacy',
                    '2026-09-01 00:00:00'
                )
                """
            )
        )

    with pytest.raises(
        RuntimeError,
        match="automatic migration refused",
    ):
        ensure_scorecall_grading_schema(session)


def test_verified_match_creates_grade(
    session,
):

    fixture = _create_finished_fixture(
        session
    )

    _insert_scorecall_lock(
        session,
        fixture,
        predicted_home=2,
        predicted_away=1,
    )

    verification = _insert_verification(
        session,
        fixture,
    )

    result = grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    assert result["status"] == "success"
    assert result["graded_new"] == 1
    assert result["skipped_unverified"] == 0

    rows = _grade_rows(session)

    assert len(rows) == 1

    grade = rows[0]

    assert (
        grade["provider_fixture_id"]
        == fixture.provider_fixture_id
    )

    assert (
        grade["predicted_score"]
        == "2-1"
    )

    assert (
        grade["actual_score"]
        == "2-1"
    )

    assert bool(
        grade["exact_score_correct"]
    ) is True

    assert bool(
        grade["outcome_correct"]
    ) is True

    assert (
        grade[
            "verification_reconciliation_id"
        ]
        == verification.id
    )

    assert (
        grade[
            "verification_primary_source"
        ]
        == API_FOOTBALL_SOURCE
    )

    assert (
        grade[
            "verification_secondary_source"
        ]
        == LIVE_SCORE_SOURCE
    )

    assert (
        grade[
            "verification_overall_status"
        ]
        == "MATCH"
    )


def test_unverified_fixture_is_not_graded(
    session,
):

    fixture = _create_finished_fixture(
        session
    )

    _insert_scorecall_lock(
        session,
        fixture,
    )

    result = grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    assert result["graded_new"] == 0
    assert result["skipped_unverified"] == 1

    assert _grade_rows(session) == []


def test_conflict_is_not_graded(
    session,
):

    fixture = _create_finished_fixture(
        session
    )

    _insert_scorecall_lock(
        session,
        fixture,
    )

    _insert_verification(
        session,
        fixture,
        date_status="MATCH",
        result_status="CONFLICT",
        overall_status="CONFLICT",
    )

    result = grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    assert result["graded_new"] == 0
    assert result["skipped_unverified"] == 1

    assert _grade_rows(session) == []


def test_source_lag_is_not_graded(
    session,
):

    fixture = _create_finished_fixture(
        session
    )

    _insert_scorecall_lock(
        session,
        fixture,
    )

    _insert_verification(
        session,
        fixture,
        date_status="MATCH",
        result_status="SOURCE_LAG",
        overall_status="SOURCE_LAG",
    )

    result = grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    assert result["graded_new"] == 0
    assert result["skipped_unverified"] == 1

    assert _grade_rows(session) == []


def test_verified_grading_is_idempotent(
    session,
):

    fixture = _create_finished_fixture(
        session
    )

    _insert_scorecall_lock(
        session,
        fixture,
    )

    _insert_verification(
        session,
        fixture,
    )

    first = grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    second = grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    assert first["graded_new"] == 1

    assert second["graded_new"] == 0
    assert second["already_graded"] == 1

    assert len(
        _grade_rows(session)
    ) == 1


def test_verified_performance_summary(
    session,
):

    fixture = _create_finished_fixture(
        session
    )

    _insert_scorecall_lock(
        session,
        fixture,
        predicted_home=2,
        predicted_away=1,
    )

    _insert_verification(
        session,
        fixture,
    )

    grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    summary = scorecall_performance_summary(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    assert summary["status"] == "success"
    assert summary["graded"] == 1

    assert (
        summary["exact_score_correct"]
        == 1
    )

    assert (
        summary["exact_score_accuracy"]
        == 1.0
    )

    assert (
        summary["outcome_correct"]
        == 1
    )

    assert (
        summary["outcome_accuracy"]
        == 1.0
    )

    assert (
        summary["verified_results_only"]
        is True
    )


def test_grade_rows_are_immutable(
    session,
):

    if (
        session.get_bind().dialect.name
        != "sqlite"
    ):
        pytest.fail(
            "Expected isolated SQLite test DB."
        )

    fixture = _create_finished_fixture(
        session
    )

    _insert_scorecall_lock(
        session,
        fixture,
    )

    _insert_verification(
        session,
        fixture,
    )

    grade_verified_scorecall_locks(
        session,
        competition_id=COMPETITION_ID,
        season=SEASON,
    )

    row = _grade_rows(session)[0]

    with pytest.raises(DatabaseError):

        session.execute(
            text(
                """
                UPDATE sportsq_scorecall_grades
                SET actual_score = '9-9'
                WHERE id = :id
                """
            ),
            {
                "id": int(
                    row["id"]
                )
            },
        )

        session.commit()

    session.rollback()

    with pytest.raises(DatabaseError):

        session.execute(
            text(
                """
                DELETE
                FROM sportsq_scorecall_grades
                WHERE id = :id
                """
            ),
            {
                "id": int(
                    row["id"]
                )
            },
        )

        session.commit()

    session.rollback()

    remaining = _grade_rows(session)

    assert len(remaining) == 1

    assert (
        remaining[0]["actual_score"]
        == "2-1"
    )
