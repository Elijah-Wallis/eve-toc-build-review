# Job Hunt Copilot (Local + Legit)

This workflow helps you apply faster in your local area, tailor materials per role, and improve assessment performance through practice.

It does not support cheating on live hiring assessments.

## Setup

From repo root:

```bash
python3 scripts/job_hunt_copilot.py bootstrap \
  --zip-code 75074 \
  --radius 35 \
  --summary "Hands-on offshore and mechanical professional with sales and telesales experience."
```

Then edit profile details here:

- `data/job_copilot/profile.json`

## 1) Ingest Jobs

Prepare a CSV/JSON export and ingest it:

```bash
python3 scripts/job_hunt_copilot.py ingest --input data/job_copilot/jobs_template.csv
```

Accepted input fields (any subset):

- `title` or `job_title`
- `company` or `company_name`
- `location`
- `zip` or `zip_code`
- `url`
- `description`
- `source`

## 2) Rank by Fit + Local Area

```bash
python3 scripts/job_hunt_copilot.py shortlist --limit 50
```

Output:

- `data/job_copilot/shortlist_latest.csv`

## 3) Generate Tailored Materials

Top N roles:

```bash
python3 scripts/job_hunt_copilot.py generate --limit 20
```

Single role:

```bash
python3 scripts/job_hunt_copilot.py generate --job-id job_xxxxxxxxxxxxxx
```

Output folder:

- `data/job_copilot/output/<job_slug>/`

Each job gets:

- `resume_tailored.md`
- `cover_letter.md`
- `screening_answers.md`

## 4) Track Application Progress

```bash
python3 scripts/job_hunt_copilot.py apply --job-id job_xxx --status applied --note "Applied on company site"
python3 scripts/job_hunt_copilot.py status --limit 30
```

Status values:

- `saved`
- `applied`
- `interview`
- `offer`
- `rejected`

## 5) Practice Assessments (Legit)

Generate practice quiz + answer key:

```bash
python3 scripts/job_hunt_copilot.py practice --questions 20 --seed 42
```

Grade your attempt:

```bash
python3 scripts/job_hunt_copilot.py grade \
  --key data/job_copilot/practice/quiz_YYYYMMDD_HHMMSS_answer_key.md \
  --answers "1:B,2:C,3:A"
```

## Suggested Daily Loop

1. Import new listings.
2. Run shortlist.
3. Generate docs for top roles.
4. Apply + log status.
5. Do one 20-question practice set and track score.
