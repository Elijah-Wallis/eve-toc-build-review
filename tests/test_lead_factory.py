from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path


def _load_module():
    p = Path(__file__).resolve().parents[1] / "scripts" / "lead_factory.py"
    spec = importlib.util.spec_from_file_location("lead_factory", p)
    assert spec and spec.loader
    m = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = m
    spec.loader.exec_module(m)  # type: ignore[attr-defined]
    return m


def test_score_record_high_ticket_ad_active() -> None:
    m = _load_module()
    rec = {
        "business_name": "Prime Smile Dental",
        "category": "dental clinic",
        "ad_active": True,
        "google_ads_monthly": 5000,
        "employee_count": 10,
        "reviews_count": 120,
        "pain_signals": "missed call",
        "phone": "+13105550101",
    }
    s = m.score_record(rec, source="unit")
    assert s.high_ticket is True
    assert s.ad_active is True
    assert s.can_pay_5k_10k is True
    assert s.pain_signal is True
    assert s.score >= 80


def test_qualified_filters_out_non_icp() -> None:
    m = _load_module()
    good = m.score_record(
        {
            "business_name": "North Star Plastic Surgery",
            "category": "plastic surgery",
            "ad_active": True,
            "google_ads_monthly": 6000,
            "employee_count": 16,
            "reviews_count": 90,
            "pain_signals": "voicemail",
        }
    )
    bad = m.score_record(
        {
            "business_name": "Local Auto Shop",
            "category": "auto repair",
            "ad_active": True,
            "employee_count": 3,
            "reviews_count": 20,
        }
    )
    got = m._qualified([good, bad], min_score=60.0)
    assert len(got) == 1
    assert got[0].business_name == "North Star Plastic Surgery"


def test_script_end_to_end_outputs_files() -> None:
    m = _load_module()
    fixture = Path(__file__).resolve().parent / "fixtures" / "leads_seed.csv"
    with tempfile.TemporaryDirectory() as td:
        out_dir = Path(td) / "out"
        argv = [
            "lead_factory.py",
            "--input",
            str(fixture),
            "--out-dir",
            str(out_dir),
            "--min-score",
            "60",
            "--top-k",
            "10",
        ]
        old = sys.argv[:]
        try:
            sys.argv = argv
            rc = m.main()
        finally:
            sys.argv = old

        assert rc == 0
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        assert summary["total_scored"] >= 3
        assert (out_dir / "qualified.csv").exists()
        assert (out_dir / "call_queue.jsonl").exists()

