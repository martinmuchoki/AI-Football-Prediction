from __future__ import annotations

import app.services.sportsq_content_automation as automation


def test_automation_uses_only_ready_rows_and_creates_draft(monkeypatch, tmp_path):
    rows = [
        {"fixture_id": 100, "content_handoff_ready": True},
        {"fixture_id": 101, "content_handoff_ready": False},
    ]

    generated = []
    enqueued = []

    monkeypatch.setattr(
        automation,
        "fixture_lifecycle_rows",
        lambda *args, **kwargs: rows,
    )

    def fake_generate(session, *, fixture_id, competition_id, season, content_root):
        generated.append(fixture_id)
        return {
            "status": "success",
            "package_id": f"pkg-{fixture_id}",
            "content_fingerprint": f"fp-{fixture_id}",
            "idempotent_reuse": False,
        }

    def fake_enqueue(package_id, *, platforms, scheduled_for, content_root):
        enqueued.append((package_id, platforms, scheduled_for))
        return {
            "queue_id": "queue-1",
            "package_id": package_id,
            "status": "DRAFT",
            "approved_at": None,
            "platforms": platforms,
        }

    monkeypatch.setattr(
        automation,
        "generate_fixture_content_package",
        fake_generate,
    )
    monkeypatch.setattr(
        automation,
        "enqueue_package",
        fake_enqueue,
    )

    result = automation.automate_ready_fixture_content(
        object(),
        competition_id=39,
        season=2026,
        content_root=tmp_path,
    )

    assert generated == [100]
    assert enqueued == [("pkg-100", ["manual_export"], None)]
    assert result["status"] == "success"
    assert result["ready_count"] == 1
    assert result["generated_count"] == 1
    assert result["draft_queue_count"] == 1
    assert result["failed_count"] == 0
    assert result["results"][0]["queue_status"] == "DRAFT"
    assert result["safety"]["approval_performed"] is False
    assert result["safety"]["queue_processing_performed"] is False
    assert result["safety"]["social_auto_posting_performed"] is False
    assert result["safety"]["network_posting_performed"] is False


def test_automation_reports_idempotent_package_reuse(monkeypatch, tmp_path):
    monkeypatch.setattr(
        automation,
        "fixture_lifecycle_rows",
        lambda *args, **kwargs: [
            {"fixture_id": 200, "content_handoff_ready": True},
        ],
    )

    monkeypatch.setattr(
        automation,
        "generate_fixture_content_package",
        lambda *args, **kwargs: {
            "status": "success",
            "package_id": "pkg-200",
            "content_fingerprint": "fp-200",
            "idempotent_reuse": True,
        },
    )

    monkeypatch.setattr(
        automation,
        "enqueue_package",
        lambda *args, **kwargs: {
            "queue_id": "queue-existing",
            "status": "DRAFT",
            "approved_at": None,
            "platforms": ["manual_export"],
        },
    )

    result = automation.automate_ready_fixture_content(
        object(),
        competition_id=39,
        season=2026,
        content_root=tmp_path,
    )

    assert result["generated_count"] == 0
    assert result["reused_count"] == 1
    assert result["draft_queue_count"] == 1


def test_automation_isolates_fixture_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(
        automation,
        "fixture_lifecycle_rows",
        lambda *args, **kwargs: [
            {"fixture_id": 300, "content_handoff_ready": True},
            {"fixture_id": 301, "content_handoff_ready": True},
        ],
    )

    def fake_generate(session, *, fixture_id, **kwargs):
        if fixture_id == 300:
            raise RuntimeError("render failed")

        return {
            "status": "success",
            "package_id": "pkg-301",
            "content_fingerprint": "fp-301",
            "idempotent_reuse": False,
        }

    monkeypatch.setattr(
        automation,
        "generate_fixture_content_package",
        fake_generate,
    )

    monkeypatch.setattr(
        automation,
        "enqueue_package",
        lambda *args, **kwargs: {
            "queue_id": "queue-301",
            "status": "DRAFT",
            "approved_at": None,
            "platforms": ["manual_export"],
        },
    )

    result = automation.automate_ready_fixture_content(
        object(),
        competition_id=39,
        season=2026,
        content_root=tmp_path,
    )

    assert result["status"] == "partial"
    assert result["ready_count"] == 2
    assert result["generated_count"] == 1
    assert result["draft_queue_count"] == 1
    assert result["failed_count"] == 1

    by_fixture = {
        row["fixture_id"]: row
        for row in result["results"]
    }

    assert by_fixture[300]["status"] == "isolated_failure"
    assert by_fixture[301]["status"] == "ready"


def test_automation_rejects_non_draft_queue_state(monkeypatch, tmp_path):
    monkeypatch.setattr(
        automation,
        "fixture_lifecycle_rows",
        lambda *args, **kwargs: [
            {"fixture_id": 400, "content_handoff_ready": True},
        ],
    )

    monkeypatch.setattr(
        automation,
        "generate_fixture_content_package",
        lambda *args, **kwargs: {
            "status": "success",
            "package_id": "pkg-400",
            "content_fingerprint": "fp-400",
            "idempotent_reuse": False,
        },
    )

    monkeypatch.setattr(
        automation,
        "enqueue_package",
        lambda *args, **kwargs: {
            "queue_id": "queue-400",
            "status": "APPROVED",
            "approved_at": "2026-09-15T00:00:00+00:00",
            "platforms": ["manual_export"],
        },
    )

    result = automation.automate_ready_fixture_content(
        object(),
        competition_id=39,
        season=2026,
        content_root=tmp_path,
    )

    assert result["status"] == "partial"
    assert result["failed_count"] == 1
    assert result["draft_queue_count"] == 0
    assert result["results"][0]["status"] == "isolated_failure"
