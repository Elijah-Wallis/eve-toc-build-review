#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import time
from pathlib import Path
from typing import Any
from urllib.request import Request, urlopen


def _post_rows(
    *,
    base_url: str,
    schema: str,
    table: str,
    rows: list[dict[str, Any]],
    on_conflict: str,
    page_size: int,
    upsert: bool,
    auth: str,
    dry_run: bool,
) -> int:
    if not rows:
        return 0
    sent = 0
    if dry_run:
        return len(rows)

    base_url = base_url.rstrip("/")
    headers = {
        "apikey": auth,
        "Authorization": f"Bearer {auth}",
        "Content-Type": "application/json",
        "Accept-Profile": schema,
        "Content-Profile": schema,
    }
    if upsert and on_conflict:
        headers["Prefer"] = "resolution=merge-duplicates"

    for i in range(0, len(rows), max(1, int(page_size))):
        batch = rows[i : i + max(1, int(page_size))]
        params = ""
        if upsert and on_conflict:
            params = f"?on_conflict={on_conflict}"
        url = f"{base_url}/rest/v1/{table}{params}"
        req = Request(
            url,
            data=json.dumps(batch).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        with urlopen(req, timeout=30) as r:
            _ = r.read()
        sent += len(batch)
    return sent


def _iter_jsonl(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    if not path.exists():
        return out
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out


def export_synthetic_journeys(
    *,
    calls_dir: str,
    supabase_url: str,
    key: str,
    schema: str = "public",
    dry_run: bool = False,
    page_size: int = 200,
    upsert: bool = True,
    journey_path: str = "data/retell_calls/synthetic_customer_journeys.jsonl",
) -> dict[str, int]:
    jfile = Path(journey_path)
    if not jfile.exists():
        alt = Path(calls_dir) / "synthetic_customer_journeys.jsonl"
        if alt.exists():
            jfile = alt

    journey_rows = _iter_jsonl(jfile)

    leads: list[dict[str, Any]] = []
    calls: list[dict[str, Any]] = []
    outcomes: list[dict[str, Any]] = []

    for r in journey_rows:
        if not isinstance(r, dict):
            continue
        tenant = str(r.get("tenant") or "synthetic_medspa").strip() or "synthetic_medspa"
        campaign_id = str(r.get("campaign_id") or "").strip()
        lead_id = str(r.get("lead_id") or "").strip()
        clinic_id = str(r.get("clinic_id") or "").strip()
        call_id = str(r.get("call_id") or "").strip()
        to_number = str(r.get("to_number") or "").strip()
        call_status = str(r.get("call_status") or "").strip() or "unknown"
        call_outcome = str(r.get("call_outcome") or "").strip() or "unknown"
        conversion_stage = str(r.get("conversion_stage") or "").strip() or "unknown"
        captured_email = str(r.get("captured_email") or "").strip()
        sentiment = str(r.get("sentiment") or "").strip() or "unknown"

        duration = r.get("call_duration_ms")
        duration_ms: int | None
        if isinstance(duration, (int, float)):
            duration_ms = int(duration)
        else:
            duration_ms = None
        outcome_ts_raw = r.get("outcome_ts")
        if isinstance(outcome_ts_raw, (int, float)):
            outcome_ts = int(outcome_ts_raw)
        else:
            outcome_ts = int(time.time() * 1000)

        leads.append(
            {
                "tenant": tenant,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "clinic_id": clinic_id,
                "lead_source": "synthetic",
                "to_number": to_number,
            }
        )
        calls.append(
            {
                "tenant": tenant,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "clinic_id": clinic_id,
                "call_id": call_id,
                "to_number": to_number,
                "call_status": call_status,
                "call_outcome": call_outcome,
                "conversion_stage": conversion_stage,
                "sentiment": sentiment,
                "call_duration_ms": duration_ms,
            }
        )
        outcomes.append(
            {
                "tenant": tenant,
                "campaign_id": campaign_id,
                "lead_id": lead_id,
                "clinic_id": clinic_id,
                "call_id": call_id,
                "outcome_ts": outcome_ts,
                "call_outcome": call_outcome,
                "conversion_stage": conversion_stage,
                "captured_email": captured_email,
                "tool_calls": json.dumps(r.get("tool_calls") or [], sort_keys=True),
                "sentiment": sentiment,
                "call_duration_ms": duration_ms,
            }
        )

    lead_stats = {
        "lead_sent": _post_rows(
            base_url=supabase_url,
            schema=schema,
            table="ont_leads",
            rows=leads,
            on_conflict="tenant,campaign_id,lead_id",
            page_size=page_size,
            upsert=upsert,
            auth=key,
            dry_run=dry_run,
        ),
        "call_sent": _post_rows(
            base_url=supabase_url,
            schema=schema,
            table="ont_calls",
            rows=calls,
            on_conflict="tenant,call_id",
            page_size=page_size,
            upsert=upsert,
            auth=key,
            dry_run=dry_run,
        ),
        "outcome_sent": _post_rows(
            base_url=supabase_url,
            schema=schema,
            table="ont_call_outcomes",
            rows=outcomes,
            on_conflict="tenant,call_id,outcome_ts",
            page_size=page_size,
            upsert=upsert,
            auth=key,
            dry_run=dry_run,
        ),
    }
    return lead_stats


def parse_args() -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Export synthetic customer journey events to Supabase.")
    ap.add_argument("--calls-dir", default="data/retell_calls")
    ap.add_argument("--journey-path", default="data/retell_calls/synthetic_customer_journeys.jsonl")
    ap.add_argument("--supabase-url", default=os.getenv("SUPABASE_URL", ""))
    ap.add_argument("--supabase-key", default=os.getenv("SUPABASE_SERVICE_KEY", ""))
    ap.add_argument("--schema", default=os.getenv("SUPABASE_SCHEMA", "public"))
    ap.add_argument("--page-size", type=int, default=200)
    ap.add_argument("--dry-run", action="store_true", default=False)
    ap.add_argument("--no-upsert", action="store_true", default=False)
    return ap.parse_args()


def main() -> int:
    args = parse_args()

    if not args.supabase_url or not args.supabase_key:
        print("SUPABASE_URL and SUPABASE_SERVICE_KEY (or --supabase-url/--supabase-key) are required")
        return 2

    result = export_synthetic_journeys(
        calls_dir=args.calls_dir,
        supabase_url=args.supabase_url,
        key=args.supabase_key,
        schema=args.schema,
        dry_run=bool(args.dry_run),
        page_size=max(1, int(args.page_size)),
        upsert=not bool(args.no_upsert),
        journey_path=args.journey_path,
    )

    print(json.dumps(
        {
            "status": "ok",
            "dry_run": bool(args.dry_run),
            "upsert": not bool(args.no_upsert),
            "schema": args.schema,
            "sent": result,
        },
        sort_keys=True,
        indent=2,
    ))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
