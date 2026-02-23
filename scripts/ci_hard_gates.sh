#!/usr/bin/env bash
set -euo pipefail

# Ensure a writable Python environment for dependency provisioning.
if [[ -z "${VIRTUAL_ENV:-}" ]]; then
  if [[ ! -x ".venv/bin/python3" ]]; then
    python3 -m venv .venv
  fi
  # shellcheck disable=SC1091
  source .venv/bin/activate
fi

python3 -m pip install -e ".[dev,ops]"

python3 - <<'PY'
import importlib.util
import sys

missing = []
for mod in ("websockets", "prometheus_client"):
    if importlib.util.find_spec(mod) is None:
        missing.append(mod)
if missing:
    print(
        "Missing required optional dependencies for CI hard gates: "
        + ", ".join(missing)
        + "\nInstall with: python3 -m pip install -e \".[dev,ops]\"",
        file=sys.stderr,
    )
    raise SystemExit(2)
PY

TEST_DIRS=(tests)
[[ -d tests_expressive ]] && TEST_DIRS+=(tests_expressive)

python3 -m pytest -q "${TEST_DIRS[@]}"
python3 -m pytest -q -k vic_contract
python3 -m pytest -q tests/acceptance/at_vic_100_sessions.py
python3 -m pytest -q tests/acceptance/at_no_leak_30min.py
python3 -m pytest -q tests/acceptance/at_ws_torture_5min.py

if command -v npm >/dev/null 2>&1 && [[ -f "apps/web/package.json" ]]; then
  pushd apps/web >/dev/null
  npm install
  npm run test
  npm run build
  popd >/dev/null
else
  echo "Skipping web hard gates: npm or apps/web not available"
fi
