#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import random
import time
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


DEFAULT_CAMPAIGN_ID = "ont-live-001"
DEFAULT_CAMPAIGN_NAME = "b2b_outbound_workflow"
DEFAULT_CAMPAIGN_TIER = "outbound"
DEFAULT_STATES = ["Texas", "Florida", "California"]
DEFAULT_APIFY_ACTOR = "compass/crawler-google-places"
DEFAULT_OUTPUT_DIR = "data/leads"
DEFAULT_MAX_ATTEMPTS = 500
DEFAULT_DAILY_CALL_CAP = 3
DEFAULT_CONCURRENCY = 20
DEFAULT_ATTEMPT_WARNING_THRESHOLD = 200


def _to_int(v: Any) -> int:
    try:
        return int(float(str(v).strip()))
    except Exception:
        return 0


def _to_float(v: Any) -> float:
    try:
        return float(str(v).strip())
    except Exception:
        return 0.0


def _to_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    s = str(v or "").strip().lower()
    return s in {"1", "true", "yes", "y", "on"}


def _norm(v: Any) -> str:
    return str(v or "").strip()


def _normalize_phone(v: Any) -> str:
    s = "".join(ch for ch in str(v or "") if ch.isdigit() or ch == "+")
    if not s:
        return ""
    if s.startswith("++"):
        s = s[1:]
    if s.startswith("+"):
        return s
    if len(s) >= 10:
        return f"+{s}"
    return s


def _normalize_email(v: Any) -> str:
    email = str(v or "").strip().lower()
    return email


def _extract_first(row: dict[str, Any], keys: list[Any]) -> Any:
    for key_path in keys:
        if isinstance(key_path, (list, tuple)):
            cur: Any = row
            ok = True
            for key in key_path:
                if isinstance(cur, dict) and key in cur:
                    cur = cur[key]
                else:
                    ok = False
                    break
            if ok:
                if cur not in (None, ""):
                    return cur
        else:
            v = row.get(str(key_path))
            if v not in (None, ""):
                return v
    return ""


def _extract_email_candidates(v: Any) -> list[str]:
    if isinstance(v, str):
        txt = v.strip()
        if "@" in txt and "." in txt:
            return [_normalize_email(txt)]
    if isinstance(v, (list, tuple)):
        out: list[str] = []
        for item in v:
            out.extend(_extract_email_candidates(item))
        return out
    if isinstance(v, dict):
        emails: list[str] = []
        for key in ("email", "value", "address"):
            emails.extend(_extract_email_candidates(v.get(key)))
        return emails
    return []


def _lead_id(rec: dict[str, Any], idx: int) -> str:
    candidate = str(
        _extract_first(
            rec,
            [
                "id",
                ("id",),
                "place_id",
                "google_place_id",
                ("place_id",),
                "url",
                "website",
                "phone",
                "phoneNumber",
            ],
        )
    ).strip()
    if candidate:
        return hashlib.sha256(f"{candidate}".encode("utf-8")).hexdigest()[:16]
    name = _norm(_extract_first(rec, ["business_name", "name", "title", "company_name"]))
    phone = _normalize_phone(_extract_first(rec, ["phone", "phone_number", "main_phone", "phoneNumber"]))
    base = f"{name}|{phone}|{idx}"
    return hashlib.sha256(base.encode("utf-8")).hexdigest()[:16]


def _segment_score(rec: dict[str, Any]) -> tuple[str, int, list[str]]:
    review_score = _to_float(_extract_first(rec, ["review_score", "rating", "avg_rating", "stars"]))
    review_count = _to_int(_extract_first(rec, ["review_count", "reviews_count", "total_reviews", "review_count_total"]))
    employee_count = _to_int(_extract_first(rec, ["employee_count", "employees", "team_size"]))

    score = 0
    reasons: list[str] = []

    if review_score >= 4.8:
        score += 30
        reasons.append("rating:very_high")
    elif review_score >= 4.5:
        score += 20
        reasons.append("rating:high")
    elif review_score >= 4.0:
        score += 10
        reasons.append("rating:good")

    if review_count >= 250:
        score += 25
        reasons.append("review_count:very_high")
    elif review_count >= 80:
        score += 18
        reasons.append("review_count:high")
    elif review_count >= 25:
        score += 10
        reasons.append("review_count:medium")

    if 2 <= employee_count <= 35:
        score += 14
        reasons.append("team_size:good_fit")
    if employee_count >= 10:
        score += 6

    website_val = _extract_first(rec, ["website", "main_website", "url", "mainUrl", "website_url"])
    if _to_bool(website_val):
        score += 6
        reasons.append("has_website")

    manager_email = _extract_first(
        rec,
        [
            "manager_email",
            ("contacts", "email"),
            ("owner", "email"),
            "decision_maker_email",
        ],
    )
    if _normalize_email(manager_email):
        score += 8
        reasons.append("manager_email")

    state = _norm(_extract_first(rec, ["state", "region", "province"])).lower()
    if state in {"tx", "fl", "ca"}:
        score += 4
        reasons.append("target_state")

    if score >= 72:
        return "priority", score, reasons
    if score >= 54:
        return "standard", score, reasons
    if score >= 35:
        return "nurture", score, reasons
    return "low", score, reasons


def _parse_state_filter(raw: str) -> list[str]:
    parts = [x.strip() for x in raw.split(",") if x.strip()]
    out: list[str] = []
    for p in parts:
        out.append(p.strip().lower())
        if len(p) == 2:
            continue
        if p.strip().lower() == "texas":
            out.append("tx")
        elif p.strip().lower() == "florida":
            out.append("fl")
        elif p.strip().lower() == "california":
            out.append("ca")
    return sorted(set(out))


def _extract_state(rec: dict[str, Any]) -> str:
    return _norm(_extract_first(rec, ["state", "region", "province", "address_state", "area"])).upper()


def _state_allowed(state: str, targets: list[str]) -> bool:
    if not targets:
        return True
    s = str(state).strip().lower()
    if not s:
        return False
    return any(t in {s, s.split("-")[0], s.replace(" ", "")} for t in targets)


def _post_n8n_batch(webhook_url: str, rows: list[dict[str, Any]], batch_size: int = 25) -> tuple[int, int]:
    if not webhook_url:
        return 0, 0
    if batch_size <= 0:
        batch_size = 25
    payload_rows = [dict(r) for r in rows]
    sent = 0
    failed = 0
    for i in range(0, len(payload_rows), batch_size):
        batch = payload_rows[i : i + batch_size]
        payload = {
            "batch_size": len(batch),
            "campaign_id": payload_rows[0].get("campaign_id") if payload_rows else "",
            "campaign_name": payload_rows[0].get("campaign_name") if payload_rows else "",
            "leads": batch,
        }
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


def _load_local_records(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    if path.suffix.lower() in {".json", ".jsonl", ".ndjson"}:
        raw = path.read_text(encoding="utf-8")
        try:
            parsed: Any = json.loads(raw)
        except Exception:
            return []
        if isinstance(parsed, list):
            return [x for x in parsed if isinstance(x, dict)]
        if isinstance(parsed, dict):
            for key in ("data", "items", "leads", "records"):
                v = parsed.get(key)
                if isinstance(v, list):
                    return [x for x in v if isinstance(x, dict)]
        return []
    delimiter = "\t" if path.suffix.lower() == ".tsv" else ","
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter=delimiter):
            out.append({k: v for k, v in row.items()})
    return out


def _apify_fetch_records(
    actor_id: str,
    token: str,
    payload: dict[str, Any],
    timeout_s: int = 120,
) -> list[dict[str, Any]]:
    encoded_actor = urllib.parse.quote(actor_id, safe="")
    encoded_token = urllib.parse.quote(token, safe="")
    sync_url = f"https://api.apify.com/v2/acts/{encoded_actor}/run-sync-get-dataset-items?token={encoded_token}"
    req = Request(sync_url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
    raw = b""
    try:
        with urlopen(req, timeout=timeout_s) as r:
            raw = r.read()
    except Exception:
        return _apify_async_fetch(actor_id=actor_id, token=token, payload=payload, timeout_s=timeout_s)

    try:
        data = json.loads(raw.decode("utf-8"))
    except Exception:
        data = []
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    if isinstance(data, dict):
        for key in ("data", "items", "results"):
            value = data.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
        payload_obj = data.get("data")
        if isinstance(payload_obj, dict):
            items = payload_obj.get("items")
            if isinstance(items, list):
                return [x for x in items if isinstance(x, dict)]
    return []


def _apify_async_fetch(
    actor_id: str,
    token: str,
    payload: dict[str, Any],
    timeout_s: int = 120,
) -> list[dict[str, Any]]:
    encoded_actor = urllib.parse.quote(actor_id, safe="")
    encoded_token = urllib.parse.quote(token, safe="")
    run_url = f"https://api.apify.com/v2/acts/{encoded_actor}/runs?token={encoded_token}"
    run_req = Request(
        run_url,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(run_req, timeout=timeout_s) as r:
        run_data = json.loads(r.read().decode("utf-8"))

    run_id = ""
    if isinstance(run_data, dict):
        run_id = str(
            run_data.get("id")
            or run_data.get("data", {}).get("id")
            or run_data.get("run", {}).get("id")
        ).strip()
    if not run_id:
        return []

    poll_url = f"https://api.apify.com/v2/actor-runs/{run_id}?token={encoded_token}"
    dataset_id = ""
    deadline = time.time() + 120
    while time.time() < deadline:
        with urlopen(Request(poll_url, method="GET"), timeout=timeout_s) as r:
            run_state = json.loads(r.read().decode("utf-8"))
        if not isinstance(run_state, dict):
            break
        status = str(run_state.get("status", "")).lower()
        if status in {"succeeded", "failed", "aborted", "terminated"}:
            dataset_id = str(
                run_state.get("defaultDatasetId")
                or run_state.get("data", {}).get("defaultDatasetId")
            ).strip()
            break
        if status in {"running", "ready", "requesting"}:
            time.sleep(3)
            continue
        dataset_id = str(run_state.get("defaultDatasetId", "")).strip()
        break

    if not dataset_id:
        return []

    dataset_url = f"https://api.apify.com/v2/datasets/{dataset_id}/items?token={encoded_token}"
    with urlopen(Request(dataset_url, method="GET"), timeout=timeout_s) as r:
        data = json.loads(r.read().decode("utf-8"))
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _build_input_payload(args: argparse.Namespace, top_k: int, states: list[str]) -> dict[str, Any]:
    queries = [s.strip() for s in args.query or ["medspa"] if s.strip()]
    if not queries:
        queries = ["medspa"]

    payload: dict[str, Any] = {
        "query": " | ".join(queries),
        "limit": int(top_k),
        "locations": [s.strip().title() for s in states if s.strip()],
    }
    payload.update(getattr(args, "apify_payload", {}) or {})
    return payload


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Build live B2B outreach leads from Apify or local files.")
    ap.add_argument("--campaign-id", default=os.getenv("CAMPAIGN_ID", DEFAULT_CAMPAIGN_ID))
    ap.add_argument("--campaign-name", default=os.getenv("CAMPAIGN_NAME", DEFAULT_CAMPAIGN_NAME))
    ap.add_argument("--campaign-tier", default=os.getenv("CAMPAIGN_TIER", DEFAULT_CAMPAIGN_TIER))
    ap.add_argument("--notes", default=os.getenv("CAMPAIGN_NOTES", "live medspa outbound campaign"))
    ap.add_argument(
        "--states",
        default=",".join(DEFAULT_STATES),
        help="comma-separated list (TX,FL,CA supported by default).",
    )
    ap.add_argument("--top-k", type=int, default=500)
    ap.add_argument("--query", action="append", default=["medspa", "med spa", "aesthetic clinic", "injector"])
    ap.add_argument("--apify-actor-id", default=os.getenv("APIFY_ACTOR_ID", DEFAULT_APIFY_ACTOR))
    ap.add_argument("--apify-token", default=os.getenv("APIFY_API_TOKEN", ""))
    ap.add_argument("--apify-input-json", default="", help="Optional JSON file path for custom Apify input payload.")
    ap.add_argument("--input-file", default="", help="Optional local lead file (.csv/.json) for offline replay.")
    ap.add_argument(
        "--max-attempts",
        type=int,
        default=int(os.getenv("CAMPAIGN_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS))),
    )
    ap.add_argument(
        "--attempt-warning-threshold",
        type=int,
        default=int(os.getenv("CAMPAIGN_ATTEMPT_WARNING_THRESHOLD", str(DEFAULT_ATTEMPT_WARNING_THRESHOLD))),
    )
    ap.add_argument(
        "--daily-call-cap",
        type=int,
        default=int(os.getenv("CAMPAIGN_DAILY_CALL_CAP", str(DEFAULT_DAILY_CALL_CAP))),
    )
    ap.add_argument("--out-dir", default=DEFAULT_OUTPUT_DIR)
    ap.add_argument("--out-queue-file", default="live_call_queue.jsonl")
    ap.add_argument("--lead-file", default="live_leads.csv")
    ap.add_argument("--summary-file", default="live_campaign_summary.json")
    ap.add_argument("--n8n-webhook-url", default=os.getenv("N8N_LEAD_WEBHOOK_URL", ""))
    ap.add_argument("--n8n-batch-size", type=int, default=25)
    ap.add_argument("--seed", type=int, default=42)
    return ap.parse_args()


def main() -> int:
    args = parse_args()
    random.seed(int(args.seed))

    states_filter = _parse_state_filter(args.states)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    queue_path = out_dir / args.out_queue_file
    lead_path = out_dir / args.lead_file
    summary_path = out_dir / args.summary_file

    records: list[dict[str, Any]] = []
    if args.input_file:
        records = _load_local_records(Path(args.input_file))
    else:
        if not args.apify_token:
            print("No APIFY input source: provide --input-file or set APIFY_API_TOKEN.")
            return 2
        apify_payload = {"topK": int(args.top_k)}
        if args.apify_input_json:
            try:
                with open(args.apify_input_json, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                if isinstance(loaded, dict):
                    apify_payload.update(loaded)
            except Exception:
                pass
        else:
            apify_payload = _build_input_payload(args, top_k=args.top_k, states=_parse_state_filter(args.states))
        records = _apify_fetch_records(
            actor_id=str(args.apify_actor_id).strip(),
            token=str(args.apify_token).strip(),
            payload=apify_payload,
        )

    if not records:
        print("No records returned from source.")
        return 2

    campaign_id = str(args.campaign_id).strip()
    campaign_name = str(args.campaign_name).strip()

    rows: list[dict[str, Any]] = []
    for i, rec in enumerate(records, start=1):
        if not isinstance(rec, dict):
            continue
        state = _extract_state(rec)
        if states_filter and not _state_allowed(state, states_filter):
            continue

        clinic_phone = _normalize_phone(
            _extract_first(
                rec,
                [
                    "phone",
                    "phone_number",
                    "mainPhone",
                    ("contact", "phone"),
                    "phoneE164",
                ],
            )
        )
        if not clinic_phone:
            continue

        clinic_name = _norm(
            _extract_first(
                rec,
                ["business_name", "title", "name", "company_name", "clinic", "practice_name"],
            )
        )
        if not clinic_name:
            continue

        website = _norm(
            _extract_first(
                rec,
                ["website", "main_website", "url", "mainUrl", "website_url", ("contact", "website")],
            )
        )
        if not website:
            continue

        clinic_email = _normalize_email(
            _extract_first(
                rec,
                [
                    "clinic_email",
                    "email",
                    "business_email",
                    "main_email",
                    ("contact", "email"),
                    ("contact", "business_email"),
                ],
            )
        )
        manager_email_candidates = _extract_email_candidates(
            _extract_first(
                rec,
                [
                    "manager_email",
                    ("decision_maker", "email"),
                    ("owner", "email"),
                    "owner_email",
                    ("founder", "email"),
                    ("contact", "email"),
                ],
            )
        )
        manager_email = _normalize_email(manager_email_candidates[0] if manager_email_candidates else "")

        manager_name = _norm(
            _extract_first(
                rec,
                [
                    "manager_name",
                    ("decision_maker", "name"),
                    ("owner", "name"),
                    "owner_name",
                    ("founder", "name"),
                    ("contact", "name"),
                ],
            )
        )
        if not manager_name:
            manager_name = "Manager"
        if not manager_email and not _to_bool(clinic_email):
            manager_name = "Owner"

        if not website:
            continue

        segment_name, segment_score, segment_reasons = _segment_score(rec)
        lead_record = dict(
            lead_id=_lead_id(rec, i),
            clinic_id=str(_extract_first(rec, ["clinic_id", "id", "place_id", "google_place_id"]) or ""),
            clinic_name=clinic_name,
            clinic_phone=clinic_phone,
            clinic_email=clinic_email,
            clinic_website=website,
            industry_vertical="medspa",
            manager_name=manager_name,
            manager_email=manager_email,
            campaign_id=campaign_id,
            campaign_name=campaign_name,
            campaign_tier=str(args.campaign_tier).strip(),
            notes=str(args.notes).strip(),
            state=state,
            city=_norm(_extract_first(rec, ["city", "town", "locality"])),
            website=website,
            review_score=f"{_to_float(_extract_first(rec, ['review_score', 'rating', 'stars'])):.2f}",
            review_count=str(_to_int(_extract_first(rec, ["review_count", "reviews_count", "total_reviews"]))),
            employee_count=str(_to_int(_extract_first(rec, ["employee_count", "employees", "team_size"]))),
            lead_segment=segment_name,
            segment_score=segment_score,
            segment_reasons="|".join(segment_reasons),
            attempts=0,
            attempt_warning_threshold=max(1, int(args.attempt_warning_threshold)),
            max_attempts=max(1, int(args.max_attempts)),
            attempts_exceeded_200=False,
            to_number=clinic_phone,
            last_action="never_contacted",
            last_action_ts=0,
            stop_outreach=False,
            next_attempt_at=0,
            call_days="Mon-Sat",
            call_hours="09:00-18:00",
        )
        rows.append(lead_record)

    # Prefer high quality leads and last-action recency.
    rows.sort(key=lambda r: (r.get("segment_score", 0), r.get("last_action_ts", 0)), reverse=True)

    fieldnames = sorted({k for row in rows for k in row.keys()})
    with lead_path.open("w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
        w.writeheader()
        for row in rows:
            w.writerow({k: str(v) for k, v in row.items()})

    with queue_path.open("w", encoding="utf-8") as f:
        for row in rows:
            f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    sent, failed = 0, 0
    if args.n8n_webhook_url:
        sent, failed = _post_n8n_batch(
            webhook_url=args.n8n_webhook_url,
            rows=rows[: max(0, int(args.top_k))],
            batch_size=max(1, int(args.n8n_batch_size)),
        )

    summary = {
        "schema_version": "1.0.0",
        "campaign_id": campaign_id,
        "campaign_name": campaign_name,
        "campaign_tier": str(args.campaign_tier).strip(),
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "apify_actor_id": str(args.apify_actor_id).strip(),
        "counts": {
            "input_records": len(records),
            "qualified_records": len(rows),
            "top_k": int(args.top_k),
            "states": states_filter,
            "daily_call_cap": int(args.daily_call_cap),
            "max_attempts": int(args.max_attempts),
            "attempt_warning_threshold": int(args.attempt_warning_threshold),
        },
        "target_states": [s.upper() for s in args.states.split(",") if s.strip()],
        "source_profile": [
            {
                "name": "apify_google_places",
                "actor": str(args.apify_actor_id).strip(),
                "states": [s.upper() for s in args.states.split(",") if s.strip()],
            }
        ],
        "lead_out": str(queue_path),
        "lead_csv": str(lead_path),
        "n8n_sent": sent,
        "n8n_failed": failed,
    }
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8")

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
