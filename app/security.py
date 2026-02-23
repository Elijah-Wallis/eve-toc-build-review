from __future__ import annotations

import hmac
import ipaddress
from typing import Mapping


def is_ip_allowed(*, remote_ip: str, cidrs: str) -> bool:
    """
    Allowlist check for WebSocket connections.

    - If cidrs is empty/blank -> allow all.
    - cidrs is a comma-separated list of CIDR strings (e.g. "10.0.0.0/8,192.168.1.0/24").
    - If cidrs is non-empty but contains no valid networks -> deny (safer default).
    """
    cidrs = (cidrs or "").strip()
    if not cidrs:
        return True

    remote_ip = (remote_ip or "").strip()
    try:
        ip = ipaddress.ip_address(remote_ip)
    except ValueError:
        return False

    networks: list[ipaddress._BaseNetwork] = []
    for raw in cidrs.split(","):
        raw = raw.strip()
        if not raw:
            continue
        try:
            networks.append(ipaddress.ip_network(raw, strict=False))
        except ValueError:
            continue

    if not networks:
        return False

    return any(ip in net for net in networks)


def verify_shared_secret(*, headers: Mapping[str, str], header: str, secret: str) -> bool:
    """
    Optional shared-secret header gate.

    - If secret is empty/blank -> allow.
    - Header lookup is case-insensitive.
    """
    secret = (secret or "").strip()
    if not secret:
        return True

    header = (header or "").strip()
    if not header:
        return False

    # Case-insensitive lookup.
    val = None
    for k, v in (headers or {}).items():
        if str(k).lower() == header.lower():
            val = v
            break
    if val is None:
        return False

    return hmac.compare_digest(str(val), secret)


def resolve_client_ip(
    *,
    remote_ip: str,
    headers: Mapping[str, str],
    trusted_proxy_enabled: bool,
    trusted_proxy_cidrs: str,
) -> str:
    """
    Resolve effective client IP for allowlisting.

    - Default: use direct socket peer address.
    - If trusted proxy mode is enabled AND the direct peer is in trusted_proxy_cidrs,
      honor X-Forwarded-For and use the left-most valid IP.
    """
    direct = (remote_ip or "").strip()
    if not trusted_proxy_enabled:
        return direct
    if not direct:
        return direct
    if not is_ip_allowed(remote_ip=direct, cidrs=trusted_proxy_cidrs):
        return direct

    xff = ""
    for k, v in (headers or {}).items():
        if str(k).lower() == "x-forwarded-for":
            xff = str(v or "")
            break
    if not xff:
        return direct

    first = xff.split(",")[0].strip()
    try:
        ipaddress.ip_address(first)
    except ValueError:
        return direct
    return first


def verify_query_token(
    *,
    query_params: Mapping[str, str],
    token_param: str,
    expected_token: str,
) -> bool:
    """
    Optional query-token gate.

    - If expected_token is empty/blank -> allow.
    - Comparison is constant-time.
    """
    expected = (expected_token or "").strip()
    if not expected:
        return True
    param = (token_param or "").strip()
    if not param:
        return False
    actual = str((query_params or {}).get(param, ""))
    return hmac.compare_digest(actual, expected)
