"""Startup guards for insecure local-only settings."""

from __future__ import annotations

import logging
import warnings

from sprucer.settings import Settings

log = logging.getLogger("sprucer.security")

DEFAULT_DEV_PASSWORD = "sprucer-dev"
DEFAULT_SESSION_SECRET = "sprucer-dev-session-change-me"


def validate_runtime_settings(settings: Settings) -> list[str]:
    """
    Return human-readable warnings. Raises RuntimeError for configurations that
    must not start (unless explicitly overridden).
    """
    warnings_out: list[str] = []
    host = (settings.host or "").strip().lower()
    exposed = host in {"0.0.0.0", "::", "[::]"}
    mode = (settings.auth_mode or "dev").strip().lower()

    if settings.session_secret == DEFAULT_SESSION_SECRET:
        warnings_out.append(
            "SPRUCER_SESSION_SECRET is still the example default. Set a long random value."
        )

    if mode == "demo":
        warnings_out.append(
            "auth_mode=demo: anonymous per-session sandbox vaults; do not store real data."
        )

    if mode == "dev":
        if settings.dev_password == DEFAULT_DEV_PASSWORD:
            warnings_out.append(
                "SPRUCER_DEV_PASSWORD is still the example default (sprucer-dev)."
            )
        if exposed and not settings.allow_insecure_dev:
            raise RuntimeError(
                "Refusing to start: auth_mode=dev while binding a non-loopback host "
                f"({settings.host}). Use SPRUCER_AUTH_MODE=oidc|api_key, bind "
                "SPRUCER_HOST=127.0.0.1, or set SPRUCER_ALLOW_INSECURE_DEV=1 for lab-only."
            )
        if exposed and settings.allow_insecure_dev:
            warnings_out.append(
                "SPRUCER_ALLOW_INSECURE_DEV=1: shared-password auth is reachable off-loopback."
            )

    if mode == "none" and exposed and not settings.allow_insecure_dev:
        raise RuntimeError(
            "Refusing to start: auth_mode=none on a non-loopback bind. "
            "Set SPRUCER_ALLOW_INSECURE_DEV=1 only for trusted lab networks."
        )

    if mode == "api_key" and not settings.api_key_set():
        raise RuntimeError("SPRUCER_API_KEYS required when auth_mode=api_key")

    if mode == "oidc" and (not settings.oidc_issuer or not settings.oidc_client_id):
        raise RuntimeError("OIDC requires SPRUCER_OIDC_ISSUER and SPRUCER_OIDC_CLIENT_ID")

    for msg in warnings_out:
        log.warning(msg)
        warnings.warn(msg, UserWarning, stacklevel=2)
    return warnings_out
