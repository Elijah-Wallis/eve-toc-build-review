#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def _load_csv_rows(path: Path) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    with path.open("r", encoding="utf-8", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append({k: str(v or "").strip() for k, v in row.items()})
    return rows


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            payload = json.loads(f.read())
        if isinstance(payload, dict):
            return payload
    except Exception:
        pass
    return {}


def _build_queue(leads: list[dict[str, str]], campaign_id: str, campaign_tier: str, notes: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for row in leads:
        lead_id = str(row.get("lead_id") or "").strip()
        if not lead_id:
            continue

        clinic_id = str(row.get("clinic_id") or "").strip()
        clinic_phone = str(row.get("clinic_phone") or "").strip()
        if not clinic_phone:
            continue

        metadata = {
            "tenant": "synthetic_medspa",
            "campaign_id": campaign_id,
            "clinic_id": clinic_id,
            "lead_id": lead_id,
        }
        out.append(
            {
                "lead_id": lead_id,
                "clinic_id": int(clinic_id) if clinic_id.isdigit() else clinic_id,
                "to_number": clinic_phone,
                "clinic_name": str(row.get("clinic_name") or "").strip(),
                "clinic_phone": clinic_phone,
                "clinic_email": str(row.get("clinic_email") or "").strip(),
                "manager_name": str(row.get("manager_name") or "").strip(),
                "manager_email": str(row.get("manager_email") or "").strip(),
                "campaign_id": campaign_id,
                "campaign_tier": campaign_tier,
                "notes": notes or str(row.get("notes") or ""),
                "metadata": metadata,
            }
        )
    return out


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build synthetic campaign call queue from generated CSV artifacts.")
    parser.add_argument("--input-dir", default=".", help="Directory containing medspa_leads.csv")
    parser.add_argument("--out", default="data/retell_calls", help="Output directory for campaign queue payloads")
    parser.add_argument(
        "--campaign-id",
        default=os.getenv("SYNTHETIC_CAMPAIGN_ID", "ont-synthetic-default"),
        help="Campaign id for synthetic journey artifacts",
    )
    parser.add_argument("--campaign-tier", default="synthetic")
    parser.add_argument("--notes", default="synthetic medspa outbound campaign")
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--write-summary", action="store_true", default=False)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    input_dir = Path(args.input_dir)
    out_dir = Path(args.out)

    leads_path = input_dir / "medspa_leads.csv"
    manifest_path = input_dir / "_manifest.json"
    if not leads_path.exists():
        print(f"Missing lead file: {leads_path}")
        return 2

    leads = _load_csv_rows(leads_path)
    manifest = _load_manifest(manifest_path)
    campaign_id = str(args.campaign_id or manifest.get("campaign_id") or "ont-synthetic-default").strip()

    queue = _build_queue(leads, campaign_id=campaign_id, campaign_tier=args.campaign_tier, notes=args.notes)
    queue.sort(key=lambda r: (str(r.get("clinic_id")), str(r.get("lead_id"))))

    queue_path = out_dir / "synthetic_campaign_call_queue.jsonl"

    summary = {
        "schema_version": "1.0.0",
        "status": "ok" if queue else "empty",
        "tenant": "synthetic_medspa",
        "campaign_id": campaign_id,
        "input_dir": str(input_dir),
        "queue_file": str(queue_path),
        "queue_size": len(queue),
        "manifest_counts": manifest.get("counts", {}),
        "generated_at_utc": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
    }

    if args.dry_run:
        summary["status"] = "dry_run"
        print(json.dumps(summary, indent=2, sort_keys=True))
        if queue:
            print("sample_record", json.dumps(queue[0], sort_keys=True))
        return 0

    out_dir.mkdir(parents=True, exist_ok=True)
    with queue_path.open("w", encoding="utf-8") as f:
        for row in queue:
            f.write(json.dumps(row, separators=(",", ":"), sort_keys=True) + "\n")
    summary["queue_file"] = str(queue_path)

    if args.write_summary:
        (out_dir / "synthetic_campaign_summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True),
            encoding="utf-8",
        )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
