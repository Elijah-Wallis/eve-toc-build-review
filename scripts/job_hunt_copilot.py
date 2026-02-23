#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import datetime as dt
import hashlib
import json
import math
import random
import re
import sqlite3
import sys
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKDIR = REPO_ROOT / "data" / "job_copilot"
DB_PATH = WORKDIR / "tracker.sqlite3"
PROFILE_PATH = WORKDIR / "profile.json"
ZIP_CACHE_PATH = WORKDIR / "zip_cache.json"
SHORTLIST_PATH = WORKDIR / "shortlist_latest.csv"
OUTPUT_DIR = WORKDIR / "output"
PRACTICE_DIR = WORKDIR / "practice"
JOBS_TEMPLATE_PATH = WORKDIR / "jobs_template.csv"

DEFAULT_PROFILE: dict[str, Any] = {
    "name": "Your Name",
    "email": "you@example.com",
    "phone": "+1-000-000-0000",
    "target_zip": "75074",
    "target_radius_miles": 35,
    "target_roles": [
        "inside sales",
        "telesales",
        "business development representative",
        "field service technician",
        "mechanical technician",
    ],
    "summary": (
        "Hands-on offshore and mechanical professional with sales and telesales experience. "
        "Strong communicator focused on problem-solving, customer trust, and results."
    ),
    "skills": [
        "mechanical troubleshooting",
        "offshore operations",
        "inside sales",
        "telesales",
        "phone prospecting",
        "customer relationship management",
        "objection handling",
        "follow-up discipline",
    ],
    "experience_highlights": [
        "Delivered hands-on mechanical work in high-accountability environments.",
        "Converted inbound and outbound conversations into qualified opportunities.",
        "Handled customer objections and moved leads toward next-step commitments.",
        "Worked cross-functionally with operations teams to solve field issues quickly.",
    ],
}

JOBS_TEMPLATE_CSV = """title,company,location,zip,url,description,source
Inside Sales Representative,Example Industrial Supply,"Plano, TX",75074,https://example.com/jobs/inside-sales,"Handle inbound and outbound customer calls, maintain CRM updates, and coordinate with field service technicians.",manual
Field Service Technician,North Texas Equipment Co.,"Richardson, TX",75081,https://example.com/jobs/field-tech,"Diagnose and repair mechanical systems, communicate service recommendations to customers, and document work orders.",manual
Business Development Representative,Metro Solutions,"Garland, TX",75040,https://example.com/jobs/bdr,"High-volume outreach, objection handling, meeting booking, and consistent follow-up cadence.",manual
"""

ROLE_KEYWORDS = {
    "sales",
    "inside sales",
    "outside sales",
    "business development",
    "bdr",
    "sdr",
    "account executive",
    "account manager",
    "telesales",
    "tele sales",
    "call center",
    "customer service",
    "field service",
    "technician",
    "mechanical",
    "maintenance",
    "service advisor",
}

STOP_WORDS = {
    "and",
    "the",
    "for",
    "with",
    "this",
    "that",
    "you",
    "your",
    "our",
    "are",
    "from",
    "will",
    "about",
    "years",
    "year",
    "required",
    "preferred",
    "experience",
    "work",
    "team",
    "must",
    "ability",
    "strong",
    "skills",
    "job",
    "role",
    "position",
}

PRACTICE_BANK = [
    {
        "q": "A prospect says, 'I’m busy, call later.' Best first response?",
        "choices": [
            "Hang up immediately",
            "Ask for a specific 2-minute callback time",
            "Start pitch anyway",
            "Argue that your offer is urgent",
        ],
        "answer": "B",
        "why": "Getting a specific time keeps momentum without forcing the pitch.",
    },
    {
        "q": "In a discovery call, what should happen before pricing?",
        "choices": [
            "Feature dump",
            "Competitor critique",
            "Pain and impact clarification",
            "Discount offer",
        ],
        "answer": "C",
        "why": "Pricing lands better after clear value tied to pain.",
    },
    {
        "q": "What is the strongest close for a qualified prospect?",
        "choices": [
            "Maybe call me",
            "Do you want to buy now?",
            "Would Tuesday at 10 or Wednesday at 2 work for next step?",
            "I’ll send info and disappear",
        ],
        "answer": "C",
        "why": "Alternative close drives commitment to a concrete next action.",
    },
    {
        "q": "A hiring assessment asks for accurate CRM behavior. Best choice?",
        "choices": [
            "Update records at end of month",
            "Log notes immediately after each interaction",
            "Only track closed deals",
            "Skip notes if outcome is bad",
        ],
        "answer": "B",
        "why": "Real-time logging preserves context and forecast quality.",
    },
    {
        "q": "Mechanical-sales hybrid roles value which combination most?",
        "choices": [
            "Theory only",
            "Hands-on diagnosis plus customer communication",
            "Cold calling only",
            "Spreadsheet design",
        ],
        "answer": "B",
        "why": "These roles require technical credibility and clear communication.",
    },
    {
        "q": "When a customer objects on price, best next step is:",
        "choices": [
            "Immediate 50% discount",
            "Ask what outcome they need and re-anchor value",
            "End the call",
            "Ignore the objection",
        ],
        "answer": "B",
        "why": "Understand constraints first, then connect price to business outcome.",
    },
    {
        "q": "Best metric for early-stage outbound quality?",
        "choices": [
            "Raw dial count only",
            "Meetings booked from qualified conversations",
            "Number of browser tabs open",
            "Email word count",
        ],
        "answer": "B",
        "why": "Qualified-conversation conversion reflects real performance.",
    },
    {
        "q": "A job post asks for 'resilience in high-volume outreach.' You should highlight:",
        "choices": [
            "Only soft skills",
            "Only certifications",
            "Past cadence discipline and follow-up consistency",
            "Unrelated hobbies",
        ],
        "answer": "C",
        "why": "Cadence and follow-up discipline directly evidence resilience.",
    },
]


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-")[:80] or "item"


def ensure_workspace() -> None:
    WORKDIR.mkdir(parents=True, exist_ok=True)
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    PRACTICE_DIR.mkdir(parents=True, exist_ok=True)
    if not PROFILE_PATH.exists():
        PROFILE_PATH.write_text(json.dumps(DEFAULT_PROFILE, indent=2), encoding="utf-8")
    if not JOBS_TEMPLATE_PATH.exists():
        JOBS_TEMPLATE_PATH.write_text(JOBS_TEMPLATE_CSV, encoding="utf-8")
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS jobs (
              job_id TEXT PRIMARY KEY,
              title TEXT NOT NULL,
              company TEXT NOT NULL,
              location TEXT,
              zip_code TEXT,
              url TEXT,
              description TEXT,
              source TEXT,
              created_at TEXT NOT NULL,
              updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS applications (
              id INTEGER PRIMARY KEY AUTOINCREMENT,
              job_id TEXT NOT NULL,
              status TEXT NOT NULL,
              note TEXT,
              created_at TEXT NOT NULL,
              FOREIGN KEY(job_id) REFERENCES jobs(job_id)
            );
            """
        )


def load_profile() -> dict[str, Any]:
    ensure_workspace()
    return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))


def save_profile(profile: dict[str, Any]) -> None:
    PROFILE_PATH.write_text(json.dumps(profile, indent=2), encoding="utf-8")


def load_zip_cache() -> dict[str, list[float]]:
    if not ZIP_CACHE_PATH.exists():
        return {}
    try:
        raw = json.loads(ZIP_CACHE_PATH.read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            out: dict[str, list[float]] = {}
            for k, v in raw.items():
                if (
                    isinstance(k, str)
                    and isinstance(v, list)
                    and len(v) == 2
                    and all(isinstance(n, (int, float)) for n in v)
                ):
                    out[k] = [float(v[0]), float(v[1])]
            return out
    except Exception:
        return {}
    return {}


def save_zip_cache(cache: dict[str, list[float]]) -> None:
    ZIP_CACHE_PATH.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def zip_to_latlon(zip_code: str, cache: dict[str, list[float]]) -> tuple[float, float] | None:
    z = re.sub(r"\D", "", str(zip_code or ""))[:5]
    if len(z) != 5:
        return None
    if z in cache:
        return float(cache[z][0]), float(cache[z][1])
    url = f"https://api.zippopotam.us/us/{z}"
    try:
        with urlopen(url, timeout=4.0) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except URLError:
        return None
    except Exception:
        return None
    places = payload.get("places")
    if not isinstance(places, list) or not places:
        return None
    first = places[0]
    try:
        lat = float(first["latitude"])
        lon = float(first["longitude"])
    except Exception:
        return None
    cache[z] = [lat, lon]
    return lat, lon


def haversine_miles(a: tuple[float, float], b: tuple[float, float]) -> float:
    r = 3958.8
    lat1 = math.radians(a[0])
    lon1 = math.radians(a[1])
    lat2 = math.radians(b[0])
    lon2 = math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    x = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    c = 2 * math.atan2(math.sqrt(x), math.sqrt(1 - x))
    return r * c


def token_set(value: str) -> set[str]:
    tokens = {t for t in re.findall(r"[a-zA-Z][a-zA-Z0-9+/.-]{2,}", (value or "").lower())}
    return {t for t in tokens if t not in STOP_WORDS}


def top_keywords(text: str, limit: int = 8) -> list[str]:
    counts: dict[str, int] = {}
    for tok in re.findall(r"[a-zA-Z][a-zA-Z0-9+/.-]{2,}", (text or "").lower()):
        if tok in STOP_WORDS:
            continue
        counts[tok] = counts.get(tok, 0) + 1
    ranked = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return [k for k, _ in ranked[:limit]]


def make_job_id(title: str, company: str, location: str, url: str) -> str:
    raw = f"{title}|{company}|{location}|{url}".strip().lower().encode("utf-8")
    return "job_" + hashlib.sha1(raw).hexdigest()[:14]


def normalize_job_record(rec: dict[str, Any]) -> dict[str, str]:
    title = str(rec.get("title") or rec.get("job_title") or "").strip()
    company = str(rec.get("company") or rec.get("company_name") or "").strip()
    location = str(rec.get("location") or rec.get("city_state") or rec.get("city") or "").strip()
    zip_code = str(rec.get("zip") or rec.get("zip_code") or rec.get("postal_code") or "").strip()
    url = str(rec.get("url") or rec.get("job_url") or rec.get("link") or "").strip()
    description = str(rec.get("description") or rec.get("summary") or "").strip()
    source = str(rec.get("source") or rec.get("board") or "").strip()
    if not title or not company:
        return {}
    if not location and rec.get("city") and rec.get("state"):
        location = f"{rec['city']}, {rec['state']}"
    job_id = str(rec.get("job_id") or "").strip() or make_job_id(title, company, location, url)
    return {
        "job_id": job_id,
        "title": title,
        "company": company,
        "location": location,
        "zip_code": re.sub(r"\D", "", zip_code)[:5],
        "url": url,
        "description": description,
        "source": source,
    }


def read_input_records(path: Path) -> list[dict[str, Any]]:
    if path.suffix.lower() == ".json":
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data, list):
            return [x for x in data if isinstance(x, dict)]
        if isinstance(data, dict):
            for key in ("jobs", "items", "records", "data"):
                if isinstance(data.get(key), list):
                    return [x for x in data[key] if isinstance(x, dict)]
            return [data]
        return []
    if path.suffix.lower() in {".csv", ".tsv"}:
        delim = "\t" if path.suffix.lower() == ".tsv" else ","
        rows: list[dict[str, Any]] = []
        with path.open("r", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh, delimiter=delim):
                rows.append(dict(row))
        return rows
    raise ValueError(f"unsupported input file: {path}")


def score_job(
    job: sqlite3.Row,
    profile: dict[str, Any],
    *,
    target_latlon: tuple[float, float] | None,
    zip_cache: dict[str, list[float]],
) -> tuple[float, list[str], float | None]:
    title = str(job["title"] or "")
    description = str(job["description"] or "")
    blob = f"{title}\n{description}".lower()
    role_hits = sum(1 for kw in ROLE_KEYWORDS if kw in blob)

    skill_hits = 0
    for skill in profile.get("skills", []):
        s = str(skill).strip().lower()
        if s and s in blob:
            skill_hits += 1

    title_tokens = token_set(title)
    target_role_tokens: set[str] = set()
    for role in profile.get("target_roles", []):
        target_role_tokens |= token_set(str(role))
    role_overlap = len(title_tokens & target_role_tokens)

    distance_miles: float | None = None
    loc_bonus = 0.0
    radius = float(profile.get("target_radius_miles", 35))
    job_zip = str(job["zip_code"] or "").strip()
    if target_latlon and job_zip:
        job_latlon = zip_to_latlon(job_zip, zip_cache)
        if job_latlon:
            distance_miles = haversine_miles(target_latlon, job_latlon)
            if distance_miles <= radius:
                loc_bonus = max(0.0, 20.0 - (distance_miles / max(radius, 1.0)) * 20.0)
            else:
                loc_bonus = -12.0
    elif job_zip and str(profile.get("target_zip", "")).strip()[:3] == job_zip[:3]:
        loc_bonus = 6.0

    score = (role_hits * 7.0) + (skill_hits * 5.0) + (role_overlap * 6.0) + loc_bonus
    reasons = [
        f"role_hits={role_hits}",
        f"skill_hits={skill_hits}",
        f"title_overlap={role_overlap}",
        f"location_bonus={loc_bonus:.1f}",
    ]
    if distance_miles is not None:
        reasons.append(f"distance_miles={distance_miles:.1f}")
    return score, reasons, distance_miles


def get_jobs(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT job_id, title, company, location, zip_code, url, description, source, updated_at
        FROM jobs
        ORDER BY updated_at DESC
        """
    ).fetchall()
    return list(rows)


def cmd_bootstrap(args: argparse.Namespace) -> int:
    ensure_workspace()
    profile = load_profile()
    profile["target_zip"] = str(args.zip_code or profile.get("target_zip", "75074"))
    profile["target_radius_miles"] = int(args.radius or profile.get("target_radius_miles", 35))
    if args.name:
        profile["name"] = args.name
    if args.email:
        profile["email"] = args.email
    if args.phone:
        profile["phone"] = args.phone
    if args.summary:
        profile["summary"] = args.summary
    save_profile(profile)
    print(f"Workspace ready: {WORKDIR}")
    print(f"Profile saved: {PROFILE_PATH}")
    return 0


def cmd_ingest(args: argparse.Namespace) -> int:
    ensure_workspace()
    path = Path(args.input).resolve()
    records = read_input_records(path)
    cleaned = [normalize_job_record(r) for r in records]
    cleaned = [r for r in cleaned if r]
    now = utc_now()
    inserted = 0
    with sqlite3.connect(DB_PATH) as conn:
        for rec in cleaned:
            conn.execute(
                """
                INSERT INTO jobs (job_id, title, company, location, zip_code, url, description, source, created_at, updated_at)
                VALUES (:job_id, :title, :company, :location, :zip_code, :url, :description, :source, :now, :now)
                ON CONFLICT(job_id) DO UPDATE SET
                  title=excluded.title,
                  company=excluded.company,
                  location=excluded.location,
                  zip_code=excluded.zip_code,
                  url=excluded.url,
                  description=excluded.description,
                  source=excluded.source,
                  updated_at=:now
                """,
                {**rec, "now": now},
            )
            inserted += 1
    print(f"Ingested records: {inserted}")
    print(f"DB: {DB_PATH}")
    return 0


def shortlist_rows(limit: int) -> list[dict[str, Any]]:
    profile = load_profile()
    zip_cache = load_zip_cache()
    target_zip = str(profile.get("target_zip", "")).strip()
    target_latlon = zip_to_latlon(target_zip, zip_cache) if target_zip else None
    with sqlite3.connect(DB_PATH) as conn:
        jobs = get_jobs(conn)

    ranked: list[dict[str, Any]] = []
    for job in jobs:
        score, reasons, distance_miles = score_job(
            job,
            profile,
            target_latlon=target_latlon,
            zip_cache=zip_cache,
        )
        ranked.append(
            {
                "job_id": job["job_id"],
                "title": job["title"],
                "company": job["company"],
                "location": job["location"],
                "zip_code": job["zip_code"],
                "url": job["url"],
                "score": round(score, 2),
                "distance_miles": round(distance_miles, 1) if distance_miles is not None else "",
                "reason": "; ".join(reasons),
                "description": job["description"] or "",
            }
        )
    ranked.sort(key=lambda x: float(x["score"]), reverse=True)
    save_zip_cache(zip_cache)
    return ranked[:limit]


def cmd_shortlist(args: argparse.Namespace) -> int:
    ensure_workspace()
    rows = shortlist_rows(args.limit)
    if not rows:
        print("No jobs available. Run ingest first.")
        return 1
    with SHORTLIST_PATH.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(
            fh,
            fieldnames=[
                "job_id",
                "title",
                "company",
                "location",
                "zip_code",
                "score",
                "distance_miles",
                "url",
                "reason",
            ],
        )
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out.pop("description", None)
            writer.writerow(out)
    print(f"Saved shortlist: {SHORTLIST_PATH}")
    for idx, row in enumerate(rows, start=1):
        print(
            f"{idx:02d}. {row['title']} @ {row['company']} | {row['location']} | "
            f"score={row['score']} dist={row['distance_miles']}"
        )
    return 0


def render_resume(profile: dict[str, Any], job: dict[str, Any], keywords: list[str]) -> str:
    lines: list[str] = []
    lines.append(f"# {profile.get('name', 'Candidate')}")
    lines.append("")
    lines.append(f"- Email: {profile.get('email', '')}")
    lines.append(f"- Phone: {profile.get('phone', '')}")
    lines.append(f"- Target Area: ZIP {profile.get('target_zip', '')}")
    lines.append("")
    lines.append("## Professional Summary")
    lines.append(str(profile.get("summary", "")).strip())
    lines.append("")
    lines.append("## Role Target")
    lines.append(f"{job['title']} at {job['company']}")
    lines.append("")
    lines.append("## Impact Highlights")
    for item in profile.get("experience_highlights", []):
        lines.append(f"- {item}")
    if keywords:
        lines.append(
            f"- Tailored to role keywords: {', '.join(keywords[:8])}."
        )
    lines.append("")
    lines.append("## Core Skills")
    for skill in profile.get("skills", []):
        lines.append(f"- {skill}")
    lines.append("")
    return "\n".join(lines).strip() + "\n"


def render_cover_letter(profile: dict[str, Any], job: dict[str, Any], keywords: list[str]) -> str:
    name = profile.get("name", "Candidate")
    summary = str(profile.get("summary", "")).strip()
    kw_line = ", ".join(keywords[:6]) if keywords else "sales execution and mechanical expertise"
    lines = [
        f"Subject: Application for {job['title']} ({job['company']})",
        "",
        f"Hello Hiring Team at {job['company']},",
        "",
        (
            f"I am applying for the {job['title']} role. {summary} "
            f"My background combines hands-on technical work with direct customer-facing sales execution."
        ),
        "",
        (
            "I can contribute immediately in areas your posting emphasizes, including "
            f"{kw_line}. I am comfortable in high-accountability environments and consistent follow-up rhythms."
        ),
        "",
        "I would value the chance to discuss how I can contribute to your team.",
        "",
        f"Thank you,\n{name}",
    ]
    return "\n".join(lines).strip() + "\n"


def render_screening_answers(profile: dict[str, Any], job: dict[str, Any], keywords: list[str]) -> str:
    answers = [
        ("Why are you a fit for this role?", (
            f"I combine hands-on mechanical experience with direct sales/telesales execution. "
            f"For {job['title']}, I can contribute on day one across {', '.join(keywords[:5]) or 'customer communication and pipeline discipline'}."
        )),
        ("Why do you want this company?", (
            f"{job['company']} aligns with my background in practical problem-solving and customer outcomes. "
            "I prefer teams where accountability and responsiveness matter."
        )),
        ("How do you handle rejection or objections?", (
            "I stay calm, ask clarifying questions, and re-anchor the conversation on the customer's goal. "
            "If timing is wrong, I secure a specific next step and follow through."
        )),
        ("What compensation are you targeting?", (
            "I am open based on role scope and total package, and I prioritize growth opportunity and clear performance expectations."
        )),
    ]
    out = [f"# Screening Drafts: {job['title']} @ {job['company']}", ""]
    for q, a in answers:
        out.append(f"## {q}")
        out.append(a)
        out.append("")
    return "\n".join(out).strip() + "\n"


def cmd_generate(args: argparse.Namespace) -> int:
    ensure_workspace()
    profile = load_profile()
    rows = shortlist_rows(args.limit)
    if args.job_id:
        rows = [r for r in rows if r["job_id"] == args.job_id]
    if not rows:
        print("No matching shortlisted jobs found.")
        return 1

    generated = 0
    for row in rows:
        keywords = top_keywords(f"{row['title']}\n{row.get('description', '')}", limit=10)
        folder = OUTPUT_DIR / slugify(f"{row['job_id']}-{row['company']}-{row['title']}")
        folder.mkdir(parents=True, exist_ok=True)

        resume_path = folder / "resume_tailored.md"
        cover_path = folder / "cover_letter.md"
        qna_path = folder / "screening_answers.md"

        resume_path.write_text(render_resume(profile, row, keywords), encoding="utf-8")
        cover_path.write_text(render_cover_letter(profile, row, keywords), encoding="utf-8")
        qna_path.write_text(render_screening_answers(profile, row, keywords), encoding="utf-8")
        generated += 1
    print(f"Generated materials for {generated} job(s) in: {OUTPUT_DIR}")
    return 0


def cmd_apply(args: argparse.Namespace) -> int:
    ensure_workspace()
    now = utc_now()
    with sqlite3.connect(DB_PATH) as conn:
        exists = conn.execute("SELECT 1 FROM jobs WHERE job_id = ?", (args.job_id,)).fetchone()
        if not exists:
            print(f"Unknown job_id: {args.job_id}")
            return 1
        conn.execute(
            "INSERT INTO applications (job_id, status, note, created_at) VALUES (?, ?, ?, ?)",
            (args.job_id, args.status, args.note or "", now),
        )
    print(f"Logged application status for {args.job_id}: {args.status}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    ensure_workspace()
    with sqlite3.connect(DB_PATH) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT a.created_at, a.job_id, j.title, j.company, a.status, a.note
            FROM applications a
            JOIN jobs j ON j.job_id = a.job_id
            ORDER BY a.created_at DESC
            LIMIT ?
            """,
            (args.limit,),
        ).fetchall()
    if not rows:
        print("No application activity yet.")
        return 0
    for row in rows:
        print(
            f"{row['created_at']} | {row['job_id']} | {row['title']} @ {row['company']} | "
            f"{row['status']} | {row['note']}"
        )
    return 0


def cmd_practice(args: argparse.Namespace) -> int:
    ensure_workspace()
    n = max(1, min(args.questions, 100))
    random.seed(args.seed)
    selected = [random.choice(PRACTICE_BANK) for _ in range(n)]
    ts = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    quiz_path = PRACTICE_DIR / f"quiz_{ts}.md"
    key_path = PRACTICE_DIR / f"quiz_{ts}_answer_key.md"

    quiz_lines = [f"# Practice Quiz ({n} Questions)", ""]
    key_lines = [f"# Answer Key ({n} Questions)", ""]
    for idx, item in enumerate(selected, start=1):
        quiz_lines.append(f"{idx}. {item['q']}")
        for c_idx, choice in enumerate(item["choices"], start=1):
            letter = "ABCD"[c_idx - 1]
            quiz_lines.append(f"   {letter}) {choice}")
        quiz_lines.append("")

        key_lines.append(f"{idx}. {item['answer']} - {item['why']}")
    quiz_path.write_text("\n".join(quiz_lines).strip() + "\n", encoding="utf-8")
    key_path.write_text("\n".join(key_lines).strip() + "\n", encoding="utf-8")
    print(f"Quiz written: {quiz_path}")
    print(f"Answer key written: {key_path}")
    print("Use this for practice only, not live assessments.")
    return 0


def parse_answer_blob(blob: str) -> dict[int, str]:
    out: dict[int, str] = {}
    for part in re.split(r"[,\s]+", blob.strip()):
        if not part or ":" not in part:
            continue
        left, right = part.split(":", 1)
        try:
            idx = int(left.strip())
        except Exception:
            continue
        ans = right.strip().upper()[:1]
        if ans in {"A", "B", "C", "D"}:
            out[idx] = ans
    return out


def cmd_grade(args: argparse.Namespace) -> int:
    path = Path(args.key).resolve()
    if not path.exists():
        print(f"Missing key file: {path}")
        return 1
    key_text = path.read_text(encoding="utf-8")
    truth: dict[int, str] = {}
    for line in key_text.splitlines():
        m = re.match(r"^(\d+)\.\s+([ABCD])\b", line.strip())
        if m:
            truth[int(m.group(1))] = m.group(2)
    if not truth:
        print("No answer key entries found.")
        return 1

    guesses = parse_answer_blob(args.answers)
    correct = 0
    for idx, answer in truth.items():
        if guesses.get(idx) == answer:
            correct += 1
    score = (correct / len(truth)) * 100.0
    print(f"Score: {correct}/{len(truth)} ({score:.1f}%)")
    missed = [idx for idx, answer in truth.items() if guesses.get(idx) != answer]
    if missed:
        print("Missed:", ", ".join(str(i) for i in missed))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Local job-hunt copilot for sourcing, tailoring, and practice.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_bootstrap = sub.add_parser("bootstrap", help="Initialize workspace/profile.")
    p_bootstrap.add_argument("--name")
    p_bootstrap.add_argument("--email")
    p_bootstrap.add_argument("--phone")
    p_bootstrap.add_argument("--zip-code", default="75074")
    p_bootstrap.add_argument("--radius", type=int, default=35)
    p_bootstrap.add_argument("--summary")
    p_bootstrap.set_defaults(func=cmd_bootstrap)

    p_ingest = sub.add_parser("ingest", help="Ingest jobs from CSV/TSV/JSON.")
    p_ingest.add_argument("--input", required=True)
    p_ingest.set_defaults(func=cmd_ingest)

    p_short = sub.add_parser("shortlist", help="Rank jobs by fit and distance.")
    p_short.add_argument("--limit", type=int, default=50)
    p_short.set_defaults(func=cmd_shortlist)

    p_gen = sub.add_parser("generate", help="Generate tailored application docs.")
    p_gen.add_argument("--limit", type=int, default=20)
    p_gen.add_argument("--job-id")
    p_gen.set_defaults(func=cmd_generate)

    p_apply = sub.add_parser("apply", help="Log application status.")
    p_apply.add_argument("--job-id", required=True)
    p_apply.add_argument("--status", required=True, choices=["saved", "applied", "interview", "offer", "rejected"])
    p_apply.add_argument("--note")
    p_apply.set_defaults(func=cmd_apply)

    p_status = sub.add_parser("status", help="Show recent application status updates.")
    p_status.add_argument("--limit", type=int, default=30)
    p_status.set_defaults(func=cmd_status)

    p_practice = sub.add_parser("practice", help="Generate an assessment practice quiz.")
    p_practice.add_argument("--questions", type=int, default=20)
    p_practice.add_argument("--seed", type=int, default=42)
    p_practice.set_defaults(func=cmd_practice)

    p_grade = sub.add_parser("grade", help="Grade quiz answers from answer key.")
    p_grade.add_argument("--key", required=True, help="Path to quiz answer key markdown.")
    p_grade.add_argument("--answers", required=True, help="Format: '1:B,2:C,3:A'")
    p_grade.set_defaults(func=cmd_grade)
    return parser


def main(argv: list[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
