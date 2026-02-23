# Synthetic Data for Training

This folder provides a reproducible synthetic MedSpa dataset generator for local Eve testing and acceptance-gate quality control.

## What it generates

Running the generator script produces five relational CSV files:

- `medspa_clinics.csv`
- `medspa_patients.csv`
- `medspa_appointments.csv`
- `medspa_conversations.csv`
- `medspa_leads.csv`

It also emits `_manifest.json` with deterministic run metadata.

These files are linked by `clinic_id` and `patient_id` so Eve can be evaluated against both structured records and unstructured conversational logs.

## Prerequisites

```bash
pip install faker pandas
```

## Usage

```bash
python generate_medspa_synthetic_data.py
```

Optional output directory:

```bash
python generate_medspa_synthetic_data.py --output-dir ./data
```

Campaign/size controls:

```bash
python generate_medspa_synthetic_data.py \
  --campaign-id ont-smoke-001 \
  --seed 42 \
  --clinics 20 \
  --patients 200 \
  --sessions 100 \
  --output-dir ./data
```

## Notes

- The script is seeded for reproducibility (`--seed`).
- `_manifest.json` includes:
  - `schema_version`
  - `campaign_id`
  - `generator_seed`
  - `generated_at_utc`
  - `counts` (all file row counts)
  - `source_profile`
- Data includes common MedSpa intents (booking, pricing, cancellation/reschedule, post-treatment follow-up).
