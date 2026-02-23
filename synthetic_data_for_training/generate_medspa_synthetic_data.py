from __future__ import annotations

import argparse
import json
import random
from datetime import datetime, timedelta
from pathlib import Path

from faker import Faker
import pandas as pd


DEFAULT_SEED = 42
DEFAULT_NUM_CLINICS = 100
DEFAULT_NUM_PATIENTS = 10_000
DEFAULT_NUM_CONVERSATION_SESSIONS = 15_000
DEFAULT_CAMPAIGN_ID = "ont-default"

# MedSpa Domain Data
MEDSPA_SERVICES = [
    "Botox",
    "Dermal Fillers",
    "Laser Hair Removal",
    "Chemical Peel",
    "Microneedling",
    "CoolSculpting",
    "HydraFacial",
    "Laser Skin Resurfacing",
    "IV Therapy",
    "Tattoo Removal",
]
APPT_STATUSES = [
    "Completed",
    "Completed",
    "Completed",
    "Cancelled",
    "No-Show",
    "Scheduled",
    "Scheduled",
]
DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]
TIMES = ["09:00 AM", "10:30 AM", "11:00 AM", "01:00 PM", "02:30 PM", "04:00 PM"]


def _safe_email(name: str, domain: str) -> str:
    local = "".join(c.lower() if c.isalnum() else "_" for c in name).strip("_")[:28]
    if not local:
        local = "manager"
    return f"{local}@{domain}"


def generate_data(
    *,
    output_dir: Path,
    campaign_id: str,
    seed: int,
    num_clinics: int,
    num_patients: int,
    num_sessions: int,
) -> None:
    fake = Faker()
    Faker.seed(seed)
    random.seed(seed)

    print("Generating synthetic MedSpa data... This may take a few moments.")
    print(
        f"Profile: campaign_id={campaign_id} seed={seed} clinics={num_clinics} "
        f"patients={num_patients} sessions={num_sessions}"
    )

    # 1. Generate Clinics
    print("Generating Clinics...")
    clinics = []
    for i in range(1, num_clinics + 1):
        clinics.append(
            {
                "clinic_id": i,
                "clinic_name": f"{fake.last_name()} "
                f"{random.choice(['MedSpa', 'Aesthetics', 'Wellness Center', 'Skin Clinic'])}",
                "address": fake.street_address(),
                "city": fake.city(),
                "state": fake.state_abbr(),
                "zip_code": fake.zipcode(),
                "phone": fake.phone_number(),
                "email": f"info@{fake.domain_name()}",
            }
        )
    df_clinics = pd.DataFrame(clinics)
    clinics_by_id = {int(r["clinic_id"]): dict(r) for r in clinics}

    # 2. Generate Patients
    print("Generating Patients...")
    patients = []
    for i in range(1, num_patients + 1):
        patients.append(
            {
                "patient_id": i,
                "primary_clinic_id": random.randint(1, num_clinics),
                "first_name": fake.first_name(),
                "last_name": fake.last_name(),
                "dob": fake.date_of_birth(minimum_age=18, maximum_age=70).strftime("%Y-%m-%d"),
                "gender": random.choice(["Female", "Female", "Female", "Male", "Non-binary"]),
                "email": fake.email(),
                "phone": fake.phone_number(),
            }
        )
    df_patients = pd.DataFrame(patients)

    # 3. Generate Appointments
    print("Generating Appointments...")
    appointments = []
    appt_id = 1
    for p in patients:
        num_appts = random.randint(0, 6)
        for _ in range(num_appts):
            appt_date = fake.date_between(start_date="-2y", end_date="+3m")
            appointments.append(
                {
                    "appointment_id": appt_id,
                    "patient_id": p["patient_id"],
                    "clinic_id": p["primary_clinic_id"],
                    "service": random.choice(MEDSPA_SERVICES),
                    "appointment_date": appt_date.strftime("%Y-%m-%d %H:%M:%S"),
                    "status": random.choice(APPT_STATUSES),
                    "provider_name": f"{fake.first_name()} {fake.last_name()}, "
                    f"{random.choice(['RN', 'NP', 'MD', 'Esthetician'])}",
                }
            )
            appt_id += 1
    df_appts = pd.DataFrame(appointments)

    # 4. Generate Conversational Logs
    print("Generating Conversational Logs...")
    conversations = []
    log_id = 1

    # Chat templates representing common MedSpa intents
    chat_templates = [
        [
            {"role": "user", "text": "Hi, I want to book a {service} appointment."},
            {
                "role": "agent",
                "text": "Absolutely! I can help you schedule your {service}. What day of the week works best for you?",
            },
            {"role": "user", "text": "Do you have anything next {day}?"},
            {
                "role": "agent",
                "text": "Let me check our calendar for next {day}... Yes, we have a slot at {time}. Does that work?",
            },
            {"role": "user", "text": "Perfect, book it."},
        ],
        [
            {"role": "user", "text": "How much does {service} cost?"},
            {
                "role": "agent",
                "text": "Our {service} treatments typically start at ${price}. Would you like to book a free consultation to get an exact quote?",
            },
            {"role": "user", "text": "No thanks, I'll think about it."},
        ],
        [
            {"role": "user", "text": "I need to cancel my appointment for tomorrow."},
            {
                "role": "agent",
                "text": "I can help with that. Can you please verify your name and phone number?",
            },
            {"role": "user", "text": "It's {first_name} {last_name}, {phone}."},
            {
                "role": "agent",
                "text": "Thank you, {first_name}. I have cancelled your appointment. Let us know when you're ready to reschedule.",
            },
        ],
        [
            {
                "role": "user",
                "text": "I had {service} done yesterday and my skin is a bit red. Is that normal?",
            },
            {
                "role": "agent",
                "text": "Hi {first_name}, mild redness is completely normal for 24-48 hours after {service}. Are you experiencing any severe pain or blistering?",
            },
            {"role": "user", "text": "No, just redness. Thanks for letting me know!"},
            {
                "role": "agent",
                "text": "You're welcome! Please reach out if symptoms worsen or don't subside in a few days.",
            },
        ],
    ]

    for session_id in range(1, num_sessions + 1):
        patient = random.choice(patients)
        template = random.choice(chat_templates)
        service = random.choice(MEDSPA_SERVICES)
        timestamp = fake.date_time_between(start_date="-1y", end_date="now")

        for msg in template:
            text = msg["text"].format(
                service=service,
                day=random.choice(DAYS),
                time=random.choice(TIMES),
                price=random.randint(150, 800),
                first_name=patient["first_name"],
                last_name=patient["last_name"],
                phone=patient["phone"],
            )
            conversations.append(
                {
                    "log_id": log_id,
                    "session_id": session_id,
                    "patient_id": patient["patient_id"],
                    "clinic_id": patient["primary_clinic_id"],
                    "timestamp": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
                    "role": msg["role"],
                    "message": text,
                }
            )
            timestamp += timedelta(minutes=random.randint(1, 5))
            log_id += 1
    df_convos = pd.DataFrame(conversations)

    # 5. Generate Call-ready lead rows.
    print("Generating Lead Queue Rows...")
    leads = []
    for idx, p in enumerate(patients, start=1):
        clinic_id = int(p["primary_clinic_id"])
        clinic = clinics_by_id.get(clinic_id, {})
        clinic_phone = str(clinic.get("phone", ""))
        clinic_email = str(clinic.get("email", ""))
        clinic_name = str(clinic.get("clinic_name", ""))
        manager_first = fake.first_name()
        manager_last = fake.last_name()
        manager_name = f"{manager_first} {manager_last}"
        domain = fake.domain_name()
        manager_email = _safe_email(f"{manager_first}{manager_last}", domain)
        leads.append(
            {
                "lead_id": f"L-{idx:07d}",
                "clinic_id": clinic_id,
                "clinic_phone": clinic_phone,
                "clinic_email": clinic_email,
                "clinic_name": clinic_name,
                "manager_name": manager_name,
                "manager_email": manager_email,
                "campaign_id": campaign_id,
                "campaign_tier": "synthetic",
                "notes": "synthetic_medspa_outbound",
            }
        )
    df_leads = pd.DataFrame(leads)

    output_dir.mkdir(parents=True, exist_ok=True)
    df_clinics.to_csv(output_dir / "medspa_clinics.csv", index=False)
    df_patients.to_csv(output_dir / "medspa_patients.csv", index=False)
    df_appts.to_csv(output_dir / "medspa_appointments.csv", index=False)
    df_convos.to_csv(output_dir / "medspa_conversations.csv", index=False)
    df_leads.to_csv(output_dir / "medspa_leads.csv", index=False)

    source_profile = [
        {
            "name": "medspa_synthetic_generator",
            "version": "2",
            "seed": seed,
            "campaign_id": campaign_id,
            "clinics": num_clinics,
            "patients": num_patients,
            "sessions": num_sessions,
            "generated_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        }
    ]

    manifest = {
        "schema_version": "1.0.0",
        "campaign_id": campaign_id,
        "generator_seed": seed,
        "generated_at_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "counts": {
            "medspa_clinics": len(df_clinics),
            "medspa_patients": len(df_patients),
            "medspa_appointments": len(df_appts),
            "medspa_conversations": len(df_convos),
            "medspa_leads": len(df_leads),
        },
        "source_profile": source_profile,
    }
    (output_dir / "_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")

    print("Success! Generated:")
    print(f"- {len(df_clinics)} Clinics")
    print(f"- {len(df_patients)} Patients")
    print(f"- {len(df_appts)} Appointments")
    print(f"- {len(df_convos)} Conversational Messages ({num_sessions} sessions)")
    print(f"- {len(df_leads)} Leads")
    print(f"Output directory: {output_dir.resolve()}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate synthetic relational MedSpa CSV datasets for Eve training."
    )
    parser.add_argument(
        "--campaign-id",
        default=DEFAULT_CAMPAIGN_ID,
        help="Campaign identifier to embed in the lead artifact",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=DEFAULT_SEED,
        help="Random seed for deterministic generation",
    )
    parser.add_argument("--clinics", type=int, default=DEFAULT_NUM_CLINICS)
    parser.add_argument("--patients", type=int, default=DEFAULT_NUM_PATIENTS)
    parser.add_argument("--sessions", type=int, default=DEFAULT_NUM_CONVERSATION_SESSIONS)
    parser.add_argument(
        "--output-dir",
        default=".",
        help="Directory where CSV files will be written (default: current directory)",
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    generate_data(
        output_dir=Path(args.output_dir),
        campaign_id=str(args.campaign_id).strip() or DEFAULT_CAMPAIGN_ID,
        seed=int(args.seed),
        num_clinics=max(1, int(args.clinics)),
        num_patients=max(0, int(args.patients)),
        num_sessions=max(0, int(args.sessions)),
    )
