from __future__ import annotations

import asyncio
import ipaddress
import os
import socket
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from ipaddress import IPv4Address, IPv6Address
from urllib.parse import urlsplit, urlunsplit

from .errors import TargetPolicyError

IPAddress = IPv4Address | IPv6Address
Resolver = Callable[[str], Awaitable[tuple[str, ...]]]

_SANCTIONED_TARGET_NETWORKS = tuple(
    ipaddress.ip_network(value)
    for value in (
        "10.0.0.0/8",
        "100.64.0.0/10",
        "172.16.0.0/12",
        "192.168.0.0/16",
        "fc00::/7",
    )
)

@dataclass(frozen=True, slots=True)
class ValidatedBioXpTarget:
    api_url: str
    scheme: str
    hostname: str
    port: int
    resolved_addresses: tuple[IPAddress, ...] = ()


async def _default_resolver(hostname: str) -> tuple[str, ...]:
    loop = asyncio.get_running_loop()
    records = await loop.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    return tuple(dict.fromkeys(str(record[4][0]) for record in records))


def _csv_values(value: str | None) -> set[str]:
    return {part.strip().lower().rstrip(".") for part in (value or "").split(",") if part.strip()}


class BioXpTargetPolicy:
    """Strict URL and complete-DNS-answer target policy."""

    def __init__(
        self,
        *,
        allowed_hosts: Iterable[str] = (),
        allowed_cidrs: Iterable[str] = (),
        allowed_ports: Iterable[int] = (8123,),
        resolver: Resolver | None = None,
    ) -> None:
        self.allowed_hosts = frozenset(str(host).strip().lower().rstrip(".") for host in allowed_hosts if str(host).strip())
        try:
            self.allowed_cidrs = tuple(ipaddress.ip_network(str(value).strip(), strict=False) for value in allowed_cidrs if str(value).strip())
        except ValueError as exc:
            raise TargetPolicyError(f"Invalid BioXP allowed CIDR: {exc}") from exc
        self.allowed_ports = frozenset(int(port) for port in allowed_ports)
        self.resolver = resolver or _default_resolver

    @classmethod
    def from_environment(cls) -> "BioXpTargetPolicy":
        hosts = _csv_values(os.getenv("BMS_BIOXP_ALLOWED_HOSTS", "robot"))
        cidrs = _csv_values(os.getenv("BMS_BIOXP_ALLOWED_CIDRS"))
        return cls(allowed_hosts=hosts, allowed_cidrs=cidrs)

    def validate(self, value: str, *, resolve: bool = False) -> ValidatedBioXpTarget:
        raw = value.strip()
        if "://" not in raw:
            raw = f"http://{raw}"
        try:
            parsed = urlsplit(raw)
            port = parsed.port
        except ValueError as exc:
            raise TargetPolicyError(f"Invalid BioXP target URL: {exc}") from exc
        scheme = parsed.scheme.lower()
        if scheme not in {"http", "https"}:
            raise TargetPolicyError("BioXP target scheme must be exactly http or https")
        if parsed.username is not None or parsed.password is not None:
            raise TargetPolicyError("BioXP target must not contain user information")
        hostname = (parsed.hostname or "").lower().rstrip(".")
        if not hostname:
            raise TargetPolicyError("BioXP target hostname is required")
        if parsed.path not in {"", "/"}:
            raise TargetPolicyError("BioXP target must not contain a path")
        if parsed.query or parsed.fragment:
            raise TargetPolicyError("BioXP target must not contain a query or fragment")
        effective_port = port or (443 if scheme == "https" else 80)
        if effective_port not in self.allowed_ports:
            raise TargetPolicyError(f"BioXP target port {effective_port} is not allowlisted")
        literal = self._literal_address(hostname)
        host_allowed = hostname in self.allowed_hosts
        literal_allowed = (
            literal is not None
            and self._ordinary_address(literal)
            and self._address_allowlisted(literal)
        )
        if not host_allowed and not literal_allowed:
            raise TargetPolicyError(f"BioXP target host {hostname!r} is not allowlisted")
        formatted_host = f"[{hostname}]" if ":" in hostname else hostname
        canonical = urlunsplit((scheme, f"{formatted_host}:{effective_port}", "", "", ""))
        target = ValidatedBioXpTarget(canonical, scheme, hostname, effective_port)
        if resolve:
            raise TargetPolicyError("Use validate_for_connection() for DNS-checked targets")
        return target

    async def validate_for_connection(self, value: str) -> ValidatedBioXpTarget:
        target = self.validate(value)
        literal = self._literal_address(target.hostname)
        try:
            raw_addresses = (target.hostname,) if literal is not None else await self.resolver(target.hostname)
        except (OSError, asyncio.TimeoutError) as exc:
            raise TargetPolicyError("BioXP target DNS resolution failed") from exc
        if not raw_addresses:
            raise TargetPolicyError("BioXP target did not resolve to any address")
        try:
            addresses = tuple(dict.fromkeys(ipaddress.ip_address(raw) for raw in raw_addresses))
        except ValueError as exc:
            raise TargetPolicyError(f"BioXP target returned an invalid DNS address: {exc}") from exc
        if not self.allowed_cidrs:
            raise TargetPolicyError("BioXP target policy has no trusted network configured")
        for address in addresses:
            if not self._ordinary_address(address):
                raise TargetPolicyError(f"Resolved BioXP address {address} is prohibited")
            if not self._address_allowlisted(address):
                raise TargetPolicyError(f"Resolved BioXP address {address} is not allowlisted")
        return ValidatedBioXpTarget(
            target.api_url,
            target.scheme,
            target.hostname,
            target.port,
            addresses,
        )

    @staticmethod
    def _literal_address(hostname: str) -> IPAddress | None:
        try:
            return ipaddress.ip_address(hostname)
        except ValueError:
            return None

    def _address_allowlisted(self, address: IPAddress) -> bool:
        return any(address in network for network in self.allowed_cidrs)

    @staticmethod
    def _ordinary_address(address: IPAddress) -> bool:
        return any(address in network for network in _SANCTIONED_TARGET_NETWORKS)
