# Self-Improve SOP

## Purpose

Run a safe, evidence-first improvement cycle for OpenClaw without breaking VIC contracts.

## Modes

- `off`: disabled.
- `propose`: analyze failures and produce recommendations only.
- `apply`: allowed to apply only when gates are green; otherwise auto-falls back to propose.

## Run

```bash
python3 scripts/self_improve_cycle.py --mode propose
```

Optional hard-gate run:

```bash
python3 scripts/self_improve_cycle.py --mode propose --hard-gates
```

## Evidence

Artifacts are written to:

- `docs/self_improve/last_run.md`
- `docs/self_improve/history/<timestamp>.json`
- `docs/self_improve/history/<timestamp>.md`
- `docs/self_improve/history/shell_exec.jsonl`

## Review Checklist

1. Confirm failed tests and clusters are accurate.
2. Confirm skill-capture suggestions map to real recurring failures.
3. Confirm no forbidden shell commands were executed.
4. Confirm VIC and acceptance gates remain green before any apply-mode rollout.

## Apply Rules

- Apply mode is blocked automatically if any gate is red.
- No autonomous deploy is allowed in this flow.
- If a regression appears, revert and capture a new skill from the incident.

## Rollback

1. Disable with `SELF_IMPROVE_MODE=off`.
2. Re-run hard gates:
   - `bash scripts/ci_hard_gates.sh`
3. Use evidence bundle in `docs/self_improve/history/` to identify the failing change.
