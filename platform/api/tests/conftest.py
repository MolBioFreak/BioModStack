"""Repository-wide pytest safety policy.

INET and DNS access are denied when this module is imported, before pytest
collects test modules.  Only a test marked ``live_bioxp`` *and* an exact
``BIOXP_LIVE_TESTS=1`` operator opt-in may temporarily enable networking.
"""

from __future__ import annotations

import os
import shlex
import shutil
import socket
import subprocess
import sys
import warnings
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
import pytest_socket
from pytest_socket import SocketBlockedError


API_ROOT = Path(__file__).resolve().parents[1]
if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

_REPO_ROOT = API_ROOT.parents[1]
_LIVE_BIOXP_ENV = "BIOXP_LIVE_TESTS"
_RUNTIME_INTEGRATION_ENV = "BMS_RUNTIME_INTEGRATION_TESTS"
_RUNTIME_INTEGRATION_MARKER = "runtime_integration"
_NETNS_ENV = "_BIOXP_PYTEST_NETNS"
_NETNS_PARENT_ENV = "_BIOXP_PYTEST_PARENT_NETNS"
_FORBIDDEN_SOCKET_MARKERS = ("enable_socket", "allow_hosts")
_FORBIDDEN_SOCKET_FIXTURES = ("socket_enabled",)
_GIT_EXECUTABLE = shutil.which("git")
_TEMP_SCRIPT_NAMES = frozenset(
    {
        "tmp_bioxp_current_grep.py",
        "tmp_bioxp_nomove_diag.sh",
        "tmp_bioxp_rca_inspect.py",
        "tmp_bioxp_z_minus50k.py",
        "tmp_bioxp_z_nonmotion_reset_retry.py",
        "tmp_bioxp_z_rehome_minus15k.py",
        "tmp_bioxp_z_zero_minus15k.py",
    }
)
_ALLOWED_GIT_GREP_POPEN_KWARGS = frozenset({"cwd", "text", "stdout", "stderr"})
_BLOCKED_RUNTIME_EXECUTABLES = frozenset(
    {"compose", "docker", "docker-compose", "nerdctl", "podman", "systemctl", "systemd-run"}
)

def _current_network_namespace() -> str:
    try:
        return os.readlink("/proc/self/ns/net")
    except OSError as exc:
        raise pytest.UsageError(
            f"cannot inspect the required BioXP network namespace: {exc}"
        ) from exc


def _has_inet_routes() -> bool:
    """Return whether the current namespace exposes a usable IPv4/IPv6 route."""
    try:
        ipv4_lines = Path("/proc/net/route").read_text(encoding="utf-8").splitlines()
        ipv6_interfaces = Path("/proc/net/if_inet6").read_text(
            encoding="utf-8"
        ).splitlines()
    except OSError as exc:
        raise pytest.UsageError(
            f"cannot inspect BioXP network-namespace routes: {exc}"
        ) from exc
    ipv4_routes = [line for line in ipv4_lines if line and not line.startswith("Iface")]
    return bool(ipv4_routes or ipv6_interfaces)


def default_network_namespace_active() -> bool:
    """Return whether this process proves it entered the route-free default netns."""
    parent_namespace = os.environ.get(_NETNS_PARENT_ENV)
    return (
        os.environ.get(_NETNS_ENV) == "1"
        and bool(parent_namespace)
        and _current_network_namespace() != parent_namespace
        and not _has_inet_routes()
    )


def _enter_default_network_namespace() -> None:
    """Launch pytest in an unprivileged network namespace before collection."""
    if default_network_namespace_active():
        return
    unshare = shutil.which("unshare")
    if unshare is None:
        raise pytest.UsageError(
            "default BioXP tests require unshare for process-tree network isolation"
        )
    environment = os.environ.copy()
    environment[_NETNS_ENV] = "1"
    environment[_NETNS_PARENT_ENV] = _current_network_namespace()
    argv = [
        unshare,
        "--user",
        "--map-root-user",
        "--net",
        sys.executable,
        "-m",
        "pytest",
        *sys.argv[1:],
    ]
    try:
        completed = subprocess.run(
            argv,
            env=environment,
            check=False,
            text=True,
            capture_output=True,
        )
    except OSError as exc:
        raise pytest.UsageError(
            f"could not enter the required BioXP network namespace: {exc}"
        ) from exc
    stdout = sys.__stdout__ or sys.stdout
    stderr = sys.__stderr__ or sys.stderr
    stdout.write(completed.stdout)
    stderr.write(completed.stderr)
    stdout.flush()
    stderr.flush()
    return_code = (
        completed.returncode if completed.returncode >= 0 else 128 - completed.returncode
    )
    os._exit(return_code)


_enter_default_network_namespace()

# pytest-socket stores the real implementations before any pytest hooks run.
# Use those stable references so the collection guard is not weakened by hook
# ordering or a previous test's teardown.
_TRUE_SOCKET = pytest_socket._true_socket
_TRUE_DNS: dict[str, Any] = {
    "getaddrinfo": pytest_socket._true_getaddrinfo,
    "gethostbyname": pytest_socket._true_gethostbyname,
    "gethostbyname_ex": socket.gethostbyname_ex,
    "gethostbyaddr": socket.gethostbyaddr,
    "getnameinfo": socket.getnameinfo,
}
_TRUE_POPEN = subprocess.Popen
_OS_PROCESS_NAMES = (
    "system",
    "popen",
    "fork",
    "forkpty",
    "posix_spawn",
    "posix_spawnp",
    "spawnl",
    "spawnle",
    "spawnlp",
    "spawnlpe",
    "spawnv",
    "spawnve",
    "spawnvp",
    "spawnvpe",
    "execl",
    "execle",
    "execlp",
    "execlpe",
    "execv",
    "execve",
    "execvp",
    "execvpe",
)
_TRUE_OS_PROCESS = {
    name: getattr(os, name)
    for name in _OS_PROCESS_NAMES
    if hasattr(os, name)
}


class BioXPSubprocessBlockedError(RuntimeError):
    """Raised when a BioXP test tries to create a child process."""


class _LiveInetCreationOnlySocket(_TRUE_SOCKET):
    """Permit INET construction while denying every endpoint-establishing action."""

    def _deny_inet_endpoint(self, operation: str) -> None:
        if self.family in (socket.AF_INET, socket.AF_INET6):
            raise pytest_socket.SocketBlockedError(
                f"INET socket {operation} is forbidden; live_bioxp permits creation only"
            )

    def connect(self, address: Any) -> None:
        self._deny_inet_endpoint("connect")
        return super().connect(address)

    def connect_ex(self, address: Any) -> int:
        self._deny_inet_endpoint("connect_ex")
        return super().connect_ex(address)

    def bind(self, address: Any) -> None:
        self._deny_inet_endpoint("bind")
        return super().bind(address)

    def listen(self, backlog: int = 0) -> None:
        self._deny_inet_endpoint("listen")
        return super().listen(backlog)

    def accept(self) -> tuple[socket.socket, Any]:
        self._deny_inet_endpoint("accept")
        return super().accept()

    def sendto(self, data: Any, *args: Any) -> int:
        self._deny_inet_endpoint("sendto")
        return super().sendto(data, *args)

    def sendmsg(self, buffers: Any, *args: Any) -> int:
        self._deny_inet_endpoint("sendmsg")
        return super().sendmsg(buffers, *args)


def _is_offline_inventory_git_grep(
    popenargs: tuple[Any, ...], kwargs: dict[str, Any]
) -> bool:
    """Allow only the manifest generator's exact, local, read-only Git query."""
    if _GIT_EXECUTABLE is None or len(popenargs) != 1:
        return False
    if frozenset(kwargs) != _ALLOWED_GIT_GREP_POPEN_KWARGS:
        return False
    args = popenargs[0]
    if not isinstance(args, (list, tuple)):
        return False
    command = [str(part) for part in args]
    if len(command) != 6 or command[:5] != [
        _GIT_EXECUTABLE,
        "grep",
        "-l",
        "-F",
        "--",
    ]:
        return False
    if command[5] not in _TEMP_SCRIPT_NAMES:
        return False
    if kwargs.get("text") is not True:
        return False
    if kwargs.get("stdout") != subprocess.PIPE or kwargs.get("stderr") != subprocess.PIPE:
        return False
    try:
        working_directory = Path(str(kwargs["cwd"])).resolve()
    except (KeyError, OSError, ValueError):
        return False
    return working_directory == _REPO_ROOT


def _runtime_control_command(command: Any, *, shell: bool = False) -> bool:
    if isinstance(command, (str, bytes, os.PathLike)):
        raw = os.fsdecode(command)
        try:
            tokens = shlex.split(raw)
        except ValueError:
            tokens = [raw]
    else:
        try:
            tokens = [os.fsdecode(part) for part in command]
        except TypeError:
            tokens = []
    if not tokens:
        return False
    executable = os.path.basename(tokens[0])
    if executable in _BLOCKED_RUNTIME_EXECUTABLES:
        return True
    if shell or executable in {"bash", "dash", "env", "sh", "sudo"}:
        nested_tokens: list[str] = []
        for token in tokens[1:]:
            try:
                nested_tokens.extend(shlex.split(token))
            except ValueError:
                nested_tokens.append(token)
        return any(
            os.path.basename(token) in _BLOCKED_RUNTIME_EXECUTABLES
            for token in nested_tokens
        )
    return False


def _guarded_runtime_popen(*popenargs: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    command = popenargs[0] if popenargs else kwargs.get("args")
    if _runtime_control_command(command, shell=bool(kwargs.get("shell"))):
        raise RuntimeError(
            "runtime integration command blocked; require both "
            f"@pytest.mark.{_RUNTIME_INTEGRATION_MARKER} and {_RUNTIME_INTEGRATION_ENV}=1"
        )
    return _TRUE_POPEN(*popenargs, **kwargs)


def _guarded_popen(*popenargs: Any, **kwargs: Any) -> subprocess.Popen[Any]:
    if _is_offline_inventory_git_grep(popenargs, kwargs):
        return _TRUE_POPEN(*popenargs, **kwargs)
    raise BioXPSubprocessBlockedError(
        "BioXP subprocess execution is forbidden in the default lane; "
        "use @pytest.mark.live_bioxp with exact BIOXP_LIVE_TESTS=1"
    )


def _blocked_os_process(*_args: Any, **_kwargs: Any) -> Any:
    raise BioXPSubprocessBlockedError(
        "BioXP subprocess execution is forbidden in the default lane; "
        "use @pytest.mark.live_bioxp with exact BIOXP_LIVE_TESTS=1"
    )


def _disable_child_processes() -> None:
    subprocess.Popen = _guarded_popen  # type: ignore[assignment]
    for name in _TRUE_OS_PROCESS:
        setattr(os, name, _blocked_os_process)


def _enable_child_processes(*, runtime_integration: bool = False) -> None:
    subprocess.Popen = _TRUE_POPEN if runtime_integration else _guarded_runtime_popen  # type: ignore[assignment]
    for name, implementation in _TRUE_OS_PROCESS.items():
        if runtime_integration or name in {"fork", "forkpty"}:
            setattr(os, name, implementation)
        else:
            setattr(os, name, _blocked_os_process)


class _InetBlockedSocket(_TRUE_SOCKET):
    """Permit AF_UNIX only; fail closed for INET/INET6 and unknown families."""

    def __new__(
        cls,
        family: socket.AddressFamily | int = -1,
        type: socket.SocketKind | int = -1,
        proto: int = -1,
        fileno: int | None = None,
    ) -> "_InetBlockedSocket":
        if hasattr(socket, "AF_UNIX") and family == socket.AF_UNIX:
            return super().__new__(cls, family, type, proto, fileno)
        raise SocketBlockedError()


def _blocked_dns(name: str):
    def blocked(*_args: Any, **_kwargs: Any) -> Any:
        raise SocketBlockedError(f"A test tried to use socket.{name}.")

    return blocked


def _disable_inet_and_dns() -> None:
    socket.socket = _InetBlockedSocket
    for name in _TRUE_DNS:
        setattr(socket, name, _blocked_dns(name))


def _enable_live_inet_creation_only() -> None:
    socket.socket = _LiveInetCreationOnlySocket


def _reject_pytest_socket_bypass(*_args: Any, **_kwargs: Any) -> None:
    raise pytest.UsageError(
        "pytest-socket network opt-ins are forbidden; use @pytest.mark.live_bioxp "
        f"with exact {_LIVE_BIOXP_ENV}=1"
    )


# This executes while the root conftest itself is imported, before test-module
# collection.  Also prevent a test from calling pytest_socket.enable_socket()
# directly to bypass the repository policy.
_disable_inet_and_dns()
_disable_child_processes()
pytest_socket.enable_socket = _reject_pytest_socket_bypass


def pytest_configure(config: pytest.Config) -> None:
    """Reject command-line allowlists that bypass the exact live-test gate."""
    add_marker = getattr(config, "addinivalue_line", None)
    if callable(add_marker):
        add_marker(
            "markers",
            f"{_RUNTIME_INTEGRATION_MARKER}: real systemd/container control; also requires "
            f"{_RUNTIME_INTEGRATION_ENV}=1",
        )
    if config.getoption("force_enable_socket"):
        raise pytest.UsageError(
            "--force-enable-socket is forbidden; use a live_bioxp marker with "
            f"exact {_LIVE_BIOXP_ENV}=1"
        )
    if config.getoption("allow_hosts"):
        raise pytest.UsageError(
            "--allow-hosts is forbidden; BioXP networking requires the live_bioxp gate"
        )


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Reject alternate opt-ins and skip live tests without exact authorization."""
    bypasses: list[str] = []
    for item in items:
        markers = [name for name in _FORBIDDEN_SOCKET_MARKERS if item.get_closest_marker(name)]
        fixtures = [name for name in _FORBIDDEN_SOCKET_FIXTURES if name in getattr(item, "fixturenames", ())]
        if markers or fixtures:
            bypasses.append(
                f"{item.nodeid}: markers={markers or 'none'}, fixtures={fixtures or 'none'}"
            )
    if bypasses:
        raise pytest.UsageError(
            "pytest-socket bypasses are forbidden; use live_bioxp plus exact "
            f"{_LIVE_BIOXP_ENV}=1:\n" + "\n".join(bypasses)
        )

    if os.environ.get(_LIVE_BIOXP_ENV) == "1":
        return
    skip_live = pytest.mark.skip(reason=f"live BioXP test requires exact {_LIVE_BIOXP_ENV}=1")
    for item in items:
        if item.get_closest_marker("live_bioxp") is not None:
            item.add_marker(skip_live)


@pytest.hookimpl(tryfirst=True)
def pytest_runtest_setup(item: pytest.Item) -> None:
    """Set child-process policy before any per-test fixtures execute."""
    is_live = item.get_closest_marker("live_bioxp") is not None
    is_authorized = os.environ.get(_LIVE_BIOXP_ENV) == "1"
    is_bioxp_test = Path(str(item.path)).name.startswith("test_bioxp")
    runtime_marked = item.get_closest_marker(_RUNTIME_INTEGRATION_MARKER) is not None
    runtime_authorized = os.environ.get(_RUNTIME_INTEGRATION_ENV) == "1"
    if runtime_marked and not runtime_authorized:
        raise RuntimeError(
            f"@pytest.mark.{_RUNTIME_INTEGRATION_MARKER} requires exact "
            f"{_RUNTIME_INTEGRATION_ENV}=1"
        )
    if is_bioxp_test or is_live or is_authorized:
        _disable_child_processes()
    else:
        # Safe children inherit the route-free namespace. Real runtime control
        # additionally requires both the marker and explicit environment opt-in.
        _enable_child_processes(runtime_integration=runtime_marked and runtime_authorized)


@pytest.fixture(autouse=True)
def _bioxp_live_network_opt_in(request: pytest.FixtureRequest) -> Iterator[None]:
    """Permit INET construction only during an exactly authorized marked test."""
    is_live = request.node.get_closest_marker("live_bioxp") is not None
    is_authorized = os.environ.get(_LIVE_BIOXP_ENV) == "1"
    if is_live and is_authorized:
        _enable_live_inet_creation_only()
    else:
        _disable_inet_and_dns()
    try:
        yield
    finally:
        _disable_inet_and_dns()


@pytest.fixture(autouse=True)
def _reject_swallowed_bioxp_socket_attempts(request: pytest.FixtureRequest) -> Iterator[None]:
    """Fail when a BioXP unit test catches and hides a blocked attempt."""
    test_path = Path(str(request.node.fspath))
    is_bioxp_test = test_path.name.startswith("test_bioxp")
    is_policy_test = test_path.name == "test_bioxp_network_isolation.py"
    is_live_test = request.node.get_closest_marker("live_bioxp") is not None
    if not is_bioxp_test or is_policy_test or is_live_test:
        yield
        return

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", UserWarning)
        yield

    socket_attempts = [
        str(warning.message)
        for warning in caught
        if str(warning.message).startswith("A test tried to use socket.")
    ]
    assert socket_attempts == [], (
        "BioXP test attempted network access and swallowed the fail-closed "
        f"error: {socket_attempts}"
    )


@pytest.hookimpl(hookwrapper=True, trylast=True)
def pytest_runtest_teardown() -> Iterator[None]:
    """Restore collection guards after all per-test teardown hooks finish."""
    yield
    _disable_inet_and_dns()
    _disable_child_processes()
