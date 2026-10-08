from __future__ import annotations

import asyncio
from ipaddress import ip_address

import pytest


def _load():
    from services.bioxp.target_policy import BioXpTargetPolicy, TargetPolicyError

    return BioXpTargetPolicy, TargetPolicyError


def test_target_policy_rejects_url_confusion_and_unapproved_ports() -> None:
    BioXpTargetPolicy, TargetPolicyError = _load()
    policy = BioXpTargetPolicy(allowed_hosts={"robot"}, allowed_cidrs={"100.64.0.0/10"})

    rejected = [
        "ftp://robot:8123",
        "http://user:secret@robot:8123",
        "http://robot:8123/path",
        "http://robot:8123?query=1",
        "http://robot:8123#fragment",
        "http://robot:8000",
        "http://evil.example:8123",
    ]
    for value in rejected:
        with pytest.raises(TargetPolicyError):
            policy.validate(value, resolve=False)


def test_target_policy_requires_every_dns_answer_to_be_trusted() -> None:
    BioXpTargetPolicy, TargetPolicyError = _load()

    async def mixed_resolver(_: str) -> tuple[str, ...]:
        return ("100.64.0.10", "192.168.1.10")

    policy = BioXpTargetPolicy(
        allowed_hosts={"robot"},
        allowed_cidrs={"100.64.0.0/10"},
        resolver=mixed_resolver,
    )
    with pytest.raises(TargetPolicyError, match="not allowlisted"):
        asyncio.run(policy.validate_for_connection("http://robot:8123"))


def test_target_policy_returns_immutable_canonical_target() -> None:
    BioXpTargetPolicy, _ = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        return ("100.64.0.10",)

    policy = BioXpTargetPolicy(
        allowed_hosts={"robot"},
        allowed_cidrs={"100.64.0.0/10"},
        resolver=resolver,
    )
    target = asyncio.run(policy.validate_for_connection("robot:8123/"))

    assert target.api_url == "http://robot:8123"
    assert target.hostname == "robot"
    assert target.port == 8123
    assert target.resolved_addresses == (ip_address("100.64.0.10"),)
    with pytest.raises(Exception):
        target.port = 9000  # type: ignore[misc]


@pytest.mark.parametrize(
    "answer",
    [
        "0.0.0.1",
        "8.8.8.8",
        "127.0.0.1",
        "192.0.2.1",
        "198.18.0.1",
        "198.51.100.1",
        "203.0.113.1",
        "240.0.0.1",
        "::1",
        "2001:db8::1",
    ],
)
def test_target_policy_rejects_public_reserved_and_loopback_answers(answer: str) -> None:
    BioXpTargetPolicy, TargetPolicyError = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        return (answer,)

    policy = BioXpTargetPolicy(
        allowed_hosts={"robot"},
        allowed_cidrs={"0.0.0.0/0", "::/0"},
        resolver=resolver,
    )
    with pytest.raises(TargetPolicyError, match="prohibited"):
        asyncio.run(policy.validate_for_connection("http://robot:8123"))


def test_target_policy_requires_a_trusted_network_for_every_dns_answer() -> None:
    BioXpTargetPolicy, TargetPolicyError = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        return ("100.64.0.10",)

    policy = BioXpTargetPolicy(allowed_hosts={"robot"}, allowed_cidrs=(), resolver=resolver)
    with pytest.raises(TargetPolicyError, match="trusted network"):
        asyncio.run(policy.validate_for_connection("http://robot:8123"))


def test_target_policy_normalizes_dns_failure() -> None:
    BioXpTargetPolicy, TargetPolicyError = _load()

    async def resolver(_: str) -> tuple[str, ...]:
        raise OSError("temporary resolver failure")

    policy = BioXpTargetPolicy(
        allowed_hosts={"robot"},
        allowed_cidrs={"100.64.0.0/10"},
        resolver=resolver,
    )
    with pytest.raises(TargetPolicyError, match="DNS resolution failed"):
        asyncio.run(policy.validate_for_connection("http://robot:8123"))
