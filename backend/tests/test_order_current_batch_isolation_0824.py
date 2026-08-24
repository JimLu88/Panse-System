from pathlib import Path

from app.services import agent_ingest_service


def test_explicit_empty_manifest_does_not_scan_historical_output(
    monkeypatch, tmp_path: Path,
):
    historical = tmp_path / "2026-06-15" / "taobao" / "old.xlsx"
    historical.parent.mkdir(parents=True)
    historical.write_bytes(b"old")
    monkeypatch.setattr(agent_ingest_service, "OUTPUT_DIR", tmp_path)

    assert agent_ingest_service._ingest_candidates([]) == []


def test_none_manifest_keeps_legacy_manual_full_scan(
    monkeypatch, tmp_path: Path,
):
    historical = tmp_path / "2026-06-15" / "taobao" / "old.xlsx"
    historical.parent.mkdir(parents=True)
    historical.write_bytes(b"old")
    monkeypatch.setattr(agent_ingest_service, "OUTPUT_DIR", tmp_path)

    assert agent_ingest_service._ingest_candidates(None) == [
        tmp_path / "2026-06-15",
        tmp_path / "2026-06-15" / "taobao",
        historical,
    ]


def test_report_failure_keeps_stage_and_bounded_details():
    failures = agent_ingest_service._job_report_failures({
        "reports": [{
            "report": "订单报表",
            "ok": False,
            "error": {
                "stage": "trigger",
                "details": ["replay:ReportTriggerError: open_export_dialog"],
            },
        }],
    })

    assert failures == [{
        "report": "订单报表",
        "stage": "trigger",
        "details": ["replay:ReportTriggerError: open_export_dialog"],
    }]
