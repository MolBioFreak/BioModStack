from __future__ import annotations

from ipaddress import ip_address, ip_network
import os
import re
import secrets

from fastapi import HTTPException, Request, status

_TRUSTED_PROXY_ENV = 'BMS_MOBILE_APK_TRUSTED_PROXY_HOSTS'
_ALLOWED_USERS_ENV = 'BMS_MOBILE_APK_ALLOWED_TAILSCALE_USERS'
_TAILNET_CONTROL_TRUSTED_PROXY_ENV = 'BMS_TAILNET_CONTROL_TRUSTED_PROXY_HOSTS'
_TAILNET_CONTROL_ALLOWED_USERS_ENV = 'BMS_TAILNET_CONTROL_ALLOWED_TAILSCALE_USERS'
_MK1D_RECONNECT_LOCAL_PROXY_SECRET_ENV = 'BMS_MK1D_RECONNECT_LOCAL_PROXY_SECRET'
_IDENTITY_PATTERN = re.compile(r'^[A-Za-z0-9][A-Za-z0-9@._+\-]{0,253}$')
_MAX_POLICY_ENTRIES = 64


def _policy_entries(name: str, *, purpose: str) -> tuple[str, ...]:
    raw = os.environ.get(name, '')
    entries = tuple(dict.fromkeys(part.strip() for part in raw.split(',') if part.strip()))
    if len(entries) > _MAX_POLICY_ENTRIES:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'{purpose} authentication is unavailable.',
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


def _require_tailscale_identity(request: Request, *, purpose: str) -> str:
    trusted_proxies = _policy_entries(_TRUSTED_PROXY_ENV, purpose=purpose)
    allowed_users = tuple(
        value.casefold() for value in _policy_entries(_ALLOWED_USERS_ENV, purpose=purpose)
    )
    if not trusted_proxies or not allowed_users:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'{purpose} authentication is unavailable.',
        )

    client_host = request.client.host if request.client is not None else ''
    if not _trusted_proxy(client_host, trusted_proxies):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f'{purpose} authentication is required.',
        )

    identity = request.headers.get('Tailscale-User-Login', '').strip()
    if not _IDENTITY_PATTERN.fullmatch(identity):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f'{purpose} authentication is required.',
        )

    normalized_identity = identity.casefold()
    if not any(secrets.compare_digest(normalized_identity, allowed) for allowed in allowed_users):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail=f'This Tailscale identity is not authorized for {purpose.lower()}.',
        )
    return identity


def require_mobile_apk_tailscale_identity(request: Request) -> str:
    return _require_tailscale_identity(request, purpose='Native APK update')


def require_tailnet_environment_tailscale_identity(request: Request) -> str:
    purpose = 'Tailnet environment control'
    trusted_proxies = _policy_entries(_TAILNET_CONTROL_TRUSTED_PROXY_ENV, purpose=purpose)
    if not trusted_proxies:
        trusted_proxies = ('127.0.0.1', '::1')
    client_host = request.client.host if request.client is not None else ''
    if not _trusted_proxy(client_host, trusted_proxies):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f'{purpose} authentication is required.',
        )
    identity = request.headers.get('Tailscale-User-Login', '').strip()
    if not _IDENTITY_PATTERN.fullmatch(identity):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f'{purpose} authentication is required.',
        )
    allowed_users = tuple(
        value.casefold()
        for value in _policy_entries(_TAILNET_CONTROL_ALLOWED_USERS_ENV, purpose=purpose)
    )
    if not allowed_users:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f'{purpose} authentication is unavailable.',
        )
    normalized_identity = identity.casefold()
    if not any(
        secrets.compare_digest(normalized_identity, allowed) for allowed in allowed_users
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail='This Tailscale identity is not authorized for tailnet environment control.',
        )
    return identity


def require_mk1d_reconnect_local_bms_web(request: Request) -> None:
    """Admit only the server-owned local bms-web proxy principal.

    Reconnect is not a Tailnet operation: identity and forwarded headers are
    ignored. Production Tailnet ingress blocks the route before bms-web.
    """
    purpose = 'Mk1D reconnect'
    proxy_secret = os.environ.get(_MK1D_RECONNECT_LOCAL_PROXY_SECRET_ENV, '')
    if not proxy_secret:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=f'{purpose} authentication is unavailable.')
    client_host = request.client.host if request.client is not None else ''
    try:
        is_loopback = ip_address(client_host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f'{purpose} authentication is required.')
    supplied_secret = request.headers.get('X-BMS-MK1D-Reconnect-Proxy-Secret', '')
    if not supplied_secret or not secrets.compare_digest(proxy_secret, supplied_secret):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=f'{purpose} authentication is required.')
