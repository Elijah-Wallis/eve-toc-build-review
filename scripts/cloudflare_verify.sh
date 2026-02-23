#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

ENV_FILE_CF="$ROOT_DIR/.env.cloudflare.local"
if [[ -f "$ENV_FILE_CF" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "$ENV_FILE_CF"
  set +a
fi

: "${CLOUDFLARE_ACCOUNT_ID:?CLOUDFLARE_ACCOUNT_ID missing (expected in .env.cloudflare.local)}"
: "${CLOUDFLARE_EVE_TOC_BUILD_API_TOKEN:?CLOUDFLARE_EVE_TOC_BUILD_API_TOKEN missing (expected in .env.cloudflare.local)}"

# Verify token without printing it.
resp="$(curl -sS "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/tokens/verify" \
  -H "Authorization: Bearer ${CLOUDFLARE_EVE_TOC_BUILD_API_TOKEN}")"

CF_VERIFY_JSON="$resp" python3 - <<'PY'
import json, os, sys
raw = os.environ.get('CF_VERIFY_JSON','')
try:
    j = json.loads(raw)
except Exception:
    print('cloudflare_verify: bad_json')
    sys.exit(1)

ok = bool(j.get('success'))
print('cloudflare_verify:', 'ok' if ok else 'fail')
if not ok:
    errs = j.get('errors') or []
    # Print only codes/messages.
    slim = [{'code': e.get('code'), 'message': e.get('message')} for e in errs][:5]
    print(json.dumps(slim, indent=2))
    sys.exit(1)
PY

# Optional tunnel audit (hostnames + DNS reachability).
audit_resp="$(curl -sS "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/cfd_tunnel" \
  -H "Authorization: Bearer ${CLOUDFLARE_EVE_TOC_BUILD_API_TOKEN}")"

CF_TUNNELS_JSON="$audit_resp" python3 - <<'PY'
import json
import os
import socket
import urllib.request

raw = os.environ.get("CF_TUNNELS_JSON", "")
try:
    payload = json.loads(raw)
except Exception:
    print("cloudflare_tunnel_audit: bad_json")
    raise SystemExit(0)

if not bool(payload.get("success")):
    print("cloudflare_tunnel_audit: api_error")
    raise SystemExit(0)

tunnels = payload.get("result", []) or []
if not tunnels:
    print("cloudflare_tunnel_audit: no_tunnels_found")
    raise SystemExit(0)

def _dns_ok(hostname: str) -> bool:
    try:
        socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
        return True
    except Exception:
        return False

def _fetch_config(account_id: str, token: str, tunnel_id: str) -> dict:
    req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{account_id}/cfd_tunnel/{tunnel_id}/configurations",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))

account_id = os.environ.get("CLOUDFLARE_ACCOUNT_ID", "")
token = os.environ.get("CLOUDFLARE_EVE_TOC_BUILD_API_TOKEN", "")

print("cloudflare_tunnel_audit:")
for tunnel in tunnels:
    name = tunnel.get("name")
    tid = tunnel.get("id")
    print(f"- {name} ({tid})")
    ingress = []
    try:
        cfg = _fetch_config(account_id, token, tid)
        if bool(cfg.get("success")):
            ingress = (cfg.get("result") or {}).get("config", {}).get("ingress", [])
    except Exception:
        ingress = []

    if not ingress:
        print("  status: no_configured_ingress")
        continue
    for rule in ingress:
        hostname = rule.get("hostname")
        service = rule.get("service")
        if hostname:
            print(f"  - {hostname} -> {service} ; dns_ok={_dns_ok(hostname)}")
        elif service and service != "http_status:404":
            print(f"  - fallback service without hostname: {service}")
PY
