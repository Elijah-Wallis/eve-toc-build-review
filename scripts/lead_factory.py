#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import os
import re
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


HIGH_TICKET_KEYWORDS = {
    "dental",
    "dentist",
    "implants",
    "orthodont",
    "invisalign",
    "plastic",
    "cosmetic",
    "surgery",
    "medspa",
    "med spa",
    "injector",
    "botox",
    "filler",
    "morpheus",
    "coolsculpt",
    "ivf",
    "hair transplant",
    "pain clinic",
    "chiropractic",
    "dermatology",
    "vision correction",
}

PAIN_KEYWORDS = {
    "missed call",
    "voicemail",
    "no answer",
    "busy line",
    "inbound overflow",
    "booking backlog",
    "low show-up",
    "front desk overloaded",
}

GENERIC_EMAIL_PREFIXES = {"info", "admin", "contact", "hello", "frontdesk", "office"}


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    if v is None:
        return False
    s = str(v).strip().lower()
    return s in {"1", "true", "yes", "y", "on", "active"}


def _to_float(v: Any) -> float:
    if v is None or v == "":
        return 0.0
    try:
        return float(v)
    except Exception:
        return 0.0


def _norm_text(v: Any) -> str:
    return re.sub(r"\s+", " ", str(v or "").strip().lower())


def _split_tags(v: Any) -> list[str]:
    if isinstance(v, list):
        return [_norm_text(x) for x in v if str(x).strip()]
    s = str(v or "").strip()
    if not s:
        return []
    if s.startswith("[") and s.endswith("]"):
        try:
            arr = json.loads(s)
            if isinstance(arr, list):
                return [_norm_text(x) for x in arr if str(x).strip()]
        except Exception:
            pass
    parts = re.split(r"[;,|]", s)
    return [_norm_text(x) for x in parts if x.strip()]


@dataclass(frozen=True, slots=True)
class LeadScore:
    lead_id: str
    business_name: str
    website: str
    phone: str
    email: str
    city: str
    state: str
    vertical: str
    ad_active: bool
    high_ticket: bool
    pain_signal: bool
    can_pay_5k_10k: bool
    score: float
    reasons: list[str]
    source: str


def _load_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("data", "items", "leads", "records"):
                v = data.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
            return [data]
        return []

    if path.suffix.lower() in {".csv", ".tsv"}:
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        out: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f, delimiter=delim)
            for row in reader:
                out.append(dict(row))
        return out
    raise ValueError(f"unsupported input file type: {path}")


def _extract_vertical(rec: dict[str, Any]) -> str:
    cat = _norm_text(rec.get("category") or rec.get("industry") or rec.get("vertical"))
    name = _norm_text(rec.get("business_name") or rec.get("name"))
    tags = " ".join(_split_tags(rec.get("services") or rec.get("keywords") or rec.get("tags")))
    blob = f"{cat} {name} {tags}"
    for kw in sorted(HIGH_TICKET_KEYWORDS):
        if kw in blob:
            return kw
    return cat or "unknown"


def _ad_active(rec: dict[str, Any]) -> bool:
    candidates = [
        rec.get("ad_active"),
        rec.get("google_ads_active"),
        rec.get("meta_ads_active"),
        rec.get("facebook_ads_active"),
        rec.get("ads_running"),
    ]
    if any(_to_bool(x) for x in candidates):
        return True
    spend = max(
        _to_float(rec.get("ad_spend_monthly")),
        _to_float(rec.get("google_ads_monthly")),
        _to_float(rec.get("meta_ads_monthly")),
    )
    return spend > 0


def _high_ticket(rec: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            _norm_text(rec.get("business_name") or rec.get("name")),
            _norm_text(rec.get("category") or rec.get("industry") or rec.get("vertical")),
            " ".join(_split_tags(rec.get("services") or rec.get("keywords") or rec.get("tags"))),
        ]
    )
    return any(kw in blob for kw in HIGH_TICKET_KEYWORDS)


def _pain_signal(rec: dict[str, Any]) -> bool:
    blob = " ".join(
        [
            _norm_text(rec.get("pain_signals")),
            _norm_text(rec.get("notes")),
            _norm_text(rec.get("review_snippets")),
            " ".join(_split_tags(rec.get("problems") or rec.get("objections"))),
        ]
    )
    return any(k in blob for k in PAIN_KEYWORDS)


def _can_pay(rec: dict[str, Any]) -> bool:
    employees = _to_float(rec.get("employee_count") or rec.get("staff_count"))
    revenue = _to_float(rec.get("annual_revenue") or rec.get("revenue"))
    reviews = _to_float(rec.get("reviews_count") or rec.get("google_reviews"))
    locations = _to_float(rec.get("locations_count") or rec.get("num_locations"))
    rating = _to_float(rec.get("rating"))
    ad_spend = max(
        _to_float(rec.get("ad_spend_monthly")),
        _to_float(rec.get("google_ads_monthly")),
        _to_float(rec.get("meta_ads_monthly")),
    )
    signal = 0
    signal += 1 if employees >= 5 else 0
    signal += 1 if revenue >= 750_000 else 0
    signal += 1 if reviews >= 50 else 0
    signal += 1 if locations >= 2 else 0
    signal += 1 if ad_spend >= 2000 else 0
    signal += 1 if rating >= 4.0 else 0
    return signal >= 2


def _make_id(rec: dict[str, Any]) -> str:
    phone = re.sub(r"\D", "", str(rec.get("phone") or ""))
    web = _norm_text(rec.get("website") or rec.get("domain"))
    name = _norm_text(rec.get("business_name") or rec.get("name"))
    base = phone or web or name
    if not base:
        raw = json.dumps(rec, sort_keys=True, separators=(",", ":")).encode("utf-8")
        digest = hashlib.sha256(raw).hexdigest()[:16]
        return f"lead_{digest}"
    return f"lead_{re.sub(r'[^a-z0-9]+', '_', base)[:64]}"


def score_record(rec: dict[str, Any], *, source: str = "") -> LeadScore:
    name = str(rec.get("business_name") or rec.get("name") or "").strip()
    website = str(rec.get("website") or rec.get("domain") or "").strip()
    phone = str(rec.get("phone") or "").strip()
    email = str(rec.get("email") or "").strip()
    city = str(rec.get("city") or "").strip()
    state = str(rec.get("state") or "").strip()

    ad = _ad_active(rec)
    high = _high_ticket(rec)
    pain = _pain_signal(rec)
    pay = _can_pay(rec)

    score = 0.0
    reasons: list[str] = []
    if ad:
        score += 35
        reasons.append("ad_active")
    if high:
        score += 30
        reasons.append("high_ticket_vertical")
    if pay:
        score += 20
        reasons.append("can_pay_5k_10k")
    if pain:
        score += 10
        reasons.append("pain_signal")
    if phone:
        score += 3
        reasons.append("has_phone")
    if website:
        score += 2
        reasons.append("has_website")
    if email:
        score += 3
        reasons.append("has_email")
    # Penalize generic-only contact quality slightly.
    if email and email.split("@", 1)[0].lower() in GENERIC_EMAIL_PREFIXES:
        score -= 2
        reasons.append("generic_email")

    score = max(0.0, min(100.0, score))

    return LeadScore(
        lead_id=_make_id(rec),
        business_name=name or "unknown",
        website=website,
        phone=phone,
        email=email,
        city=city,
        state=state,
        vertical=_extract_vertical(rec),
        ad_active=ad,
        high_ticket=high,
        pain_signal=pain,
        can_pay_5k_10k=pay,
        score=round(score, 2),
        reasons=reasons,
        source=source,
    )


def _dedupe(leads: list[LeadScore]) -> list[LeadScore]:
    seen: set[str] = set()
    out: list[LeadScore] = []
    for lead in sorted(leads, key=lambda x: x.score, reverse=True):
        keys = [
            re.sub(r"\D", "", lead.phone),
            _norm_text(lead.website),
            _norm_text(lead.business_name),
        ]
        key = next((k for k in keys if k), "")
        if not key:
            key = lead.lead_id
        if key in seen:
            continue
        seen.add(key)
        out.append(lead)
    return out


def _qualified(leads: list[LeadScore], *, min_score: float) -> list[LeadScore]:
    out: list[LeadScore] = []
    for lead in leads:
        if not lead.ad_active:
            continue
        if not lead.high_ticket:
            continue
        if not lead.can_pay_5k_10k:
            continue
        if float(lead.score) < float(min_score):
            continue
        out.append(lead)
    return out


def _write_outputs(*, out_dir: Path, all_leads: list[LeadScore], qualified: list[LeadScore], top_k: int) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)

    all_json = out_dir / "all_scored.json"
    all_csv = out_dir / "all_scored.csv"
    q_json = out_dir / "qualified.json"
    q_csv = out_dir / "qualified.csv"
    call_queue = out_dir / "call_queue.jsonl"
    summary = out_dir / "summary.json"

    def _rows(items: list[LeadScore]) -> list[dict[str, Any]]:
        return [asdict(x) for x in items]

    all_json.write_text(json.dumps(_rows(all_leads), indent=2, sort_keys=True), encoding="utf-8")
    q_json.write_text(json.dumps(_rows(qualified), indent=2, sort_keys=True), encoding="utf-8")

    cols = [
        "lead_id",
        "business_name",
        "website",
        "phone",
        "email",
        "city",
        "state",
        "vertical",
        "ad_active",
        "high_ticket",
        "pain_signal",
        "can_pay_5k_10k",
        "score",
        "reasons",
        "source",
    ]

    def _write_csv(path: Path, rows: list[LeadScore]) -> None:
        with path.open("w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            for r in rows:
                obj = asdict(r)
                obj["reasons"] = ",".join(r.reasons)
                w.writerow(obj)

    _write_csv(all_csv, all_leads)
    _write_csv(q_csv, qualified)

    with call_queue.open("w", encoding="utf-8") as f:
        for lead in qualified[: max(0, int(top_k))]:
            f.write(json.dumps(asdict(lead), sort_keys=True) + "\n")

    summary_obj = {
        "total_scored": len(all_leads),
        "qualified": len(qualified),
        "top_k": max(0, int(top_k)),
        "generated_at_unix": int(time.time()),
    }
    summary.write_text(json.dumps(summary_obj, indent=2, sort_keys=True), encoding="utf-8")


def _post_n8n(webhook_url: str, leads: list[LeadScore], batch_size: int) -> tuple[int, int]:
    sent = 0
    failed = 0
    if not webhook_url:
        return sent, failed
    if batch_size <= 0:
        batch_size = 25
    rows = [asdict(x) for x in leads]
    for i in range(0, len(rows), batch_size):
        batch = rows[i : i + batch_size]
        payload = {"batch_size": len(batch), "leads": batch}
        req = Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=20) as r:
                _ = r.read()
            sent += len(batch)
        except Exception:
            failed += len(batch)
    return sent, failed


def _load_inputs(input_paths: list[str]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for raw in input_paths:
        p = Path(raw)
        if not p.exists():
            continue
        rows = _load_records(p)
        for row in rows:
            row = dict(row)
            row.setdefault("_source_file", str(p))
            records.append(row)
    return records


def _load_from_url(url: str) -> list[dict[str, Any]]:
    req = Request(url, method="GET")
    with urlopen(req, timeout=30) as r:
        raw = r.read().decode("utf-8")
    data = json.loads(raw)
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "leads", "records"):
            v = data.get(key)
            if isinstance(v, list):
                return [x for x in v if isinstance(x, dict)]
        return [data]
    return []


def main() -> int:
    ap = argparse.ArgumentParser(description="Lead Factory: score + qualify + export + optional n8n push.")
    ap.add_argument("--input", action="append", default=[], help="input file(s): .csv/.tsv/.json (repeatable)")
    ap.add_argument("--source-url", action="append", default=[], help="HTTP JSON source(s) returning lead records")
    ap.add_argument("--out-dir", default="data/leads")
    ap.add_argument("--min-score", type=float, default=60.0)
    ap.add_argument("--top-k", type=int, default=500)
    ap.add_argument("--n8n-webhook-url", default=os.getenv("N8N_LEAD_WEBHOOK_URL", ""))
    ap.add_argument("--n8n-batch-size", type=int, default=25)
    args = ap.parse_args()

    records = _load_inputs(args.input)
    for url in args.source_url:
        try:
            rows = _load_from_url(url)
        except Exception:
            rows = []
        for row in rows:
            row = dict(row)
            row.setdefault("_source_file", url)
            records.append(row)
    if not records:
        print("No input records found. Provide --input or --source-url.", file=sys.stderr)
        return 2

    scored = [score_record(r, source=str(r.get("_source_file") or "input")) for r in records]
    deduped = _dedupe(scored)
    qualified = _qualified(deduped, min_score=float(args.min_score))

    out_dir = Path(args.out_dir)
    _write_outputs(out_dir=out_dir, all_leads=deduped, qualified=qualified, top_k=int(args.top_k))

    sent, failed = _post_n8n(args.n8n_webhook_url, qualified[: max(0, int(args.top_k))], int(args.n8n_batch_size))

    payload = {
        "status": "ok",
        "inputs": len(args.input) + len(args.source_url),
        "records_loaded": len(records),
        "records_scored": len(deduped),
        "qualified": len(qualified),
        "min_score": float(args.min_score),
        "top_k": int(args.top_k),
        "n8n_sent": sent,
        "n8n_failed": failed,
        "out_dir": str(out_dir),
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
