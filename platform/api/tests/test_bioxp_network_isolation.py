"""Fail-closed network contract for the default BioXP test lane."""

from __future__ import annotations

import multiprocessing
import os
import socket
import subprocess
import sys
import warnings
from pathlib import Path
from typing import Any, cast

import pytest
import pytest_socket
from pytest_socket import SocketBlockedError

import conftest as network_policy


def _collection_phase_inet_is_blocked() -> bool:
    """Probe while this module is imported, before test setup hooks run."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", UserWarning)
        try:
            candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        except SocketBlockedError:
            return True
        candidate.close()
        return False


COLLECTION_PHASE_INET_BLOCKED = _collection_phase_inet_is_blocked()


def _collection_phase_subprocess_is_blocked() -> bool:
    """Prove a child cannot escape before test setup hooks run."""
    try:
        subprocess.run(
            [sys.executable, "-c", "pass"],
            check=True,
            capture_output=True,
            text=True,
        )
    except RuntimeError as exc:
        return "BioXP subprocess execution is forbidden" in str(exc)
    return False


COLLECTION_PHASE_SUBPROCESS_BLOCKED = _collection_phase_subprocess_is_blocked()


def test_collection_phase_blocks_inet_before_test_setup() -> None:
    assert COLLECTION_PHASE_INET_BLOCKED is True


def test_collection_phase_blocks_subprocess_before_test_setup() -> None:
    assert COLLECTION_PHASE_SUBPROCESS_BLOCKED is True


def test_default_lane_is_inside_an_os_network_namespace() -> None:
    assert network_policy.default_network_namespace_active() is True
    routes = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()
    assert all(line.startswith("Iface") for line in routes)
    assert Path("/proc/net/if_inet6").read_text(encoding="utf-8") == ""


def test_default_bioxp_test_lane_blocks_child_processes() -> None:
    with pytest.raises(network_policy.BioXPSubprocessBlockedError):
        subprocess.run(
            [sys.executable, "-c", "pass"],
            check=True,
            capture_output=True,
            text=True,
        )


def test_environment_only_does_not_enable_child_processes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("BIOXP_LIVE_TESTS", "1")
    with pytest.raises(network_policy.BioXPSubprocessBlockedError):
        subprocess.run([sys.executable, "-c", "pass"], check=False)


def test_os_system_subprocess_bypass_is_blocked() -> None:
    with pytest.raises(network_policy.BioXPSubprocessBlockedError):
        os.system("true")


@pytest.mark.parametrize("fork_name", ["fork", "forkpty"])
def test_direct_fork_bypasses_are_blocked(fork_name: str) -> None:
    fork = getattr(os, fork_name, None)
    if fork is None:
        pytest.skip(f"os.{fork_name} is unavailable")
    with pytest.raises(network_policy.BioXPSubprocessBlockedError):
        fork()


def test_multiprocessing_fork_bypass_is_blocked() -> None:
    if "fork" not in multiprocessing.get_all_start_methods():
        pytest.skip("multiprocessing fork context is unavailable")
    process = multiprocessing.get_context("fork").Process(target=lambda: None)
    with pytest.raises(network_policy.BioXPSubprocessBlockedError):
        process.start()


def test_offline_git_exception_rejects_environment_override() -> None:
    git_executable = network_policy._GIT_EXECUTABLE
    assert git_executable is not None
    repository_root = Path(__file__).resolve().parents[3]
    script_name = "tmp_" + "bioxp_current_grep.py"
    with pytest.raises(network_policy.BioXPSubprocessBlockedError):
        subprocess.run(
            [git_executable, "grep", "-l", "-F", "--", script_name],
            cwd=repository_root,
            capture_output=True,
            text=True,
            check=False,
            env=dict(os.environ),
        )


@pytest.mark.live_bioxp
def test_exact_live_opt_in_still_blocks_child_processes() -> None:
    with pytest.raises(network_policy.BioXPSubprocessBlockedError):
        subprocess.run([sys.executable, "-c", "pass"], check=False)


def test_default_bioxp_test_lane_blocks_ipv4() -> None:
    with pytest.warns(UserWarning, match="A test tried to use socket.socket"):
        with pytest.raises(SocketBlockedError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_default_bioxp_test_lane_blocks_ipv6() -> None:
    with pytest.warns(UserWarning, match="A test tried to use socket.socket"):
        with pytest.raises(SocketBlockedError):
            socket.socket(socket.AF_INET6, socket.SOCK_STREAM)


@pytest.mark.parametrize(
    "resolver,args",
    [
        ("getaddrinfo", ("robot", 8123)),
        ("gethostbyname", ("robot",)),
        ("gethostbyname_ex", ("robot",)),
        ("gethostbyaddr", ("127.0.0.1",)),
        ("getnameinfo", (("127.0.0.1", 8123), 0)),
    ],
)
def test_default_bioxp_test_lane_blocks_dns(resolver: str, args: tuple[object, ...]) -> None:
    with pytest.warns(UserWarning, match=rf"A test tried to use socket\.{resolver}"):
        with pytest.raises(SocketBlockedError):
            getattr(socket, resolver)(*args)


def test_default_bioxp_test_lane_allows_unix_socket() -> None:
    candidate = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    candidate.close()


@pytest.mark.parametrize("value", ["true", "TRUE", "yes", "0", "1"])
def test_environment_value_alone_never_enables_an_unmarked_test(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("BIOXP_LIVE_TESTS", value)
    with pytest.warns(UserWarning, match="A test tried to use socket.socket"):
        with pytest.raises(SocketBlockedError):
            socket.socket(socket.AF_INET, socket.SOCK_STREAM)


def test_direct_pytest_socket_enable_bypass_is_rejected() -> None:
    with pytest.raises(pytest.UsageError, match="live_bioxp"):
        pytest_socket.enable_socket()


@pytest.mark.parametrize(
    "marker,fixtures",
    [
        ("enable_socket", ()),
        ("allow_hosts", ()),
        (None, ("socket_enabled",)),
    ],
)
def test_collection_rejects_alternate_pytest_socket_opt_ins(
    marker: str | None,
    fixtures: tuple[str, ...],
) -> None:
    class Item:
        nodeid = "tests/test_forbidden.py::test_forbidden"
        fixturenames = fixtures

        @staticmethod
        def get_closest_marker(name: str) -> object | None:
            return object() if name == marker else None

    with pytest.raises(pytest.UsageError, match="bypasses are forbidden"):
        network_policy.pytest_collection_modifyitems(cast(Any, [Item()]))


@pytest.mark.parametrize("option", ["force_enable_socket", "allow_hosts"])
def test_configuration_rejects_socket_bypass_options(option: str) -> None:
    class Config:
        @staticmethod
        def getoption(name: str) -> object:
            return "127.0.0.1" if name == option else None

    with pytest.raises(pytest.UsageError, match="forbidden"):
        network_policy.pytest_configure(cast(Any, Config()))


@pytest.mark.live_bioxp
def test_explicit_live_opt_in_allows_only_inet_socket_construction() -> None:
    """Exercise the exact opt-in without permitting endpoint operations."""
    assert network_policy.default_network_namespace_active()
    candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    with pytest.warns(UserWarning, match="INET socket connect is forbidden"):
        with pytest.raises(SocketBlockedError, match="permits creation only"):
            candidate.connect(("192.0.2.1", 9))
    with pytest.warns(UserWarning, match="INET socket connect_ex is forbidden"):
        with pytest.raises(SocketBlockedError, match="permits creation only"):
            candidate.connect_ex(("192.0.2.1", 9))
    with pytest.warns(UserWarning, match="INET socket bind is forbidden"):
        with pytest.raises(SocketBlockedError, match="permits creation only"):
            candidate.bind(("127.0.0.1", 0))
    with pytest.warns(UserWarning, match="INET socket sendto is forbidden"):
        with pytest.raises(SocketBlockedError, match="permits creation only"):
            candidate.sendto(b"blocked", ("192.0.2.1", 9))
    candidate.close()

    with pytest.warns(UserWarning, match="A test tried to use socket.getaddrinfo"):
        with pytest.raises(SocketBlockedError):
            socket.getaddrinfo("robot", 8123)
