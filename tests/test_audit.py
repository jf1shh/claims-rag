import json

import pytest

from backend.audit import JsonlAuditSink


def test_given_audit_event_when_recorded_then_request_and_evidence_metadata_are_persisted(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink.record({"event": "chat", "request_id": "req-1", "tenant_id": "tenant-a", "evidence_ids": ["ev-1"]})
    record = json.loads((tmp_path / "audit.jsonl").read_text(encoding="utf-8"))
    assert record["request_id"] == "req-1"
    assert record["evidence_ids"] == ["ev-1"]
    assert "recorded_at" in record


def test_given_two_events_when_recorded_then_both_are_retained(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    for request_id in ("req-1", "req-2"):
        sink.record({"event": "chat", "request_id": request_id, "tenant_id": "tenant-a"})
    records = [json.loads(line) for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    assert [record["request_id"] for record in records] == ["req-1", "req-2"]


def test_given_missing_required_audit_field_when_recorded_then_it_is_rejected(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    with pytest.raises(ValueError, match="missing fields"):
        sink.record({"event": "chat", "request_id": "req-1"})
