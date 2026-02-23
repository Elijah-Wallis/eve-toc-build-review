from __future__ import annotations

from app.config import BrainConfig
from app.security import (
    is_ip_allowed,
    resolve_client_ip,
    verify_query_token,
    verify_shared_secret,
)


def test_is_ip_allowed_allows_all_when_empty() -> None:
    assert is_ip_allowed(remote_ip="1.2.3.4", cidrs="") is True
    assert is_ip_allowed(remote_ip="::1", cidrs="   ") is True


def test_is_ip_allowed_basic_cidr_matching() -> None:
    assert is_ip_allowed(remote_ip="10.1.2.3", cidrs="10.0.0.0/8") is True
    assert is_ip_allowed(remote_ip="11.1.2.3", cidrs="10.0.0.0/8") is False
    assert is_ip_allowed(remote_ip="192.168.1.10", cidrs="192.168.1.0/24") is True
    assert is_ip_allowed(remote_ip="192.168.2.10", cidrs="192.168.1.0/24") is False


def test_is_ip_allowed_invalid_config_denies() -> None:
    # Non-empty config but no valid CIDRs -> deny.
    assert is_ip_allowed(remote_ip="1.2.3.4", cidrs="not_a_cidr") is False


def test_verify_shared_secret_default_allows() -> None:
    assert verify_shared_secret(headers={}, header="X-RETELL-SIGNATURE", secret="") is True


def test_verify_shared_secret_case_insensitive_and_compare_digest() -> None:
    headers = {"x-retell-signature": "abc123"}
    assert (
        verify_shared_secret(headers=headers, header="X-RETELL-SIGNATURE", secret="abc123") is True
    )
    assert (
        verify_shared_secret(headers=headers, header="X-RETELL-SIGNATURE", secret="wrong") is False
    )


def test_resolve_client_ip_ignores_xff_when_trusted_proxy_disabled() -> None:
    ip = resolve_client_ip(
        remote_ip="10.0.0.10",
        headers={"X-Forwarded-For": "1.2.3.4"},
        trusted_proxy_enabled=False,
        trusted_proxy_cidrs="10.0.0.0/8",
    )
    assert ip == "10.0.0.10"


def test_resolve_client_ip_honors_xff_only_for_trusted_proxy() -> None:
    # Trusted proxy path.
    ip = resolve_client_ip(
        remote_ip="10.0.0.10",
        headers={"X-Forwarded-For": "1.2.3.4, 10.0.0.10"},
        trusted_proxy_enabled=True,
        trusted_proxy_cidrs="10.0.0.0/8",
    )
    assert ip == "1.2.3.4"

    # Untrusted proxy path (ignore XFF).
    ip2 = resolve_client_ip(
        remote_ip="192.168.10.10",
        headers={"X-Forwarded-For": "1.2.3.4"},
        trusted_proxy_enabled=True,
        trusted_proxy_cidrs="10.0.0.0/8",
    )
    assert ip2 == "192.168.10.10"


def test_verify_query_token_optional_and_constant_time_compare() -> None:
    assert verify_query_token(query_params={}, token_param="token", expected_token="") is True
    assert (
        verify_query_token(
            query_params={"token": "abc123"},
            token_param="token",
            expected_token="abc123",
        )
        is True
    )
    assert (
        verify_query_token(
            query_params={"token": "wrong"},
            token_param="token",
            expected_token="abc123",
        )
        is False
    )


def test_security_defaults_are_off_and_do_not_block() -> None:
    cfg = BrainConfig()
    assert cfg.ws_allowlist_enabled is False
    assert cfg.ws_shared_secret_enabled is False
    assert cfg.ws_query_token == ""
