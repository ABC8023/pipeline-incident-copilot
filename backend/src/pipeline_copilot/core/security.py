from __future__ import annotations

import ipaddress
import secrets


def new_session_token() -> str:
    return secrets.token_urlsafe(32)


def validate_loopback_host(host: str) -> str:
    if host.lower() == "localhost":
        return host
    try:
        address = ipaddress.ip_address(host)
    except ValueError as error:
        raise ValueError(f"host {host!r} is not a loopback address") from error
    if not address.is_loopback:
        raise ValueError(f"host {host!r} is not a loopback address")
    return host
