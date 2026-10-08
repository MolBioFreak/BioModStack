from __future__ import annotations

from ipaddress import ip_address, ip_network
import os
import re
import secrets

from fastapi import HTTPException, Request, status

_TRUSTED_PROXY_ENV = 'BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS'
_ALLOWED_USERS_ENV = 'BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS'
_IDENTITY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9@._+\-]{0,253}$')
_MAX_POLICY_ENTRIES = 64


def _policy_entries(name: str) -> tuple[str, ...]:
    raw = os.environ.get(name, '')
    entries = tuple(dict.fromkeys(part.strip() for part in raw.split(',') if part.strip()))
    if len(entries) > _MAX_POLICY_ENTRIES:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Native APK update authentication is unavailable.',
        )
    return entries


def _trusted_proxy(client_host: str, policy: tuple[str, ...]) -> bool:
    try:
        client_ip = ip_address(client_host)
    except ValueError:
        return any(secrets.compare_digest(client_host, entry) for entry in policy if '/' not in entry)

    for entry in policy:
        try:
            if '/' in entry and client_ip in ip_network(entry, strict=True):
                return True
            if '/' not in entry and client_ip == ip_address(entry):
                return True
        except ValueError:
            continue
    return False


def require_mobile_apk_tailscale_identity(request: Request) -> str:
    trusted_proxies = _policy_entries(_TRUSTED_PROXY_ENV)
    allowed_users = tuple(value.casefold() for value in _policy_entries(_ALLOWED_USERS_ENV))
    if not trusted_proxies or not allowed_users:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail='Native APK update authentication is unavailable.',
        )

    client_host = request.client.host if request.client is not None else ''
    if not _trusted_proxy(client_host, trusted_proxies):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Native APK update authentication is required.',
        )

    identity = request.headers.get('Tailscale-User-Login', '').strip()
    if not _IDENTITY_PATTERN.fullmatch(identity):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail='Native APK update authentication is required.',
        )

    normalized_identity = identity.casefold()
    if not any(secrets.compare_digest(normalized_identity, allowed) for allowed in allowed_users):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='This Tailscale identity is not authorized for native APK updates.',
        )
    return identity
