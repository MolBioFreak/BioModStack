"""Remote MSA provider descriptors and the saved-credential store.

``PROVIDER_DESCRIPTORS`` is the single list of remote MSA providers. Adding a
provider is one new entry here (plus its transport in ``biomodstack_msa_api``).

Credential lookup order for a provider that needs a key:

1. the environment variable named by ``key_file_env`` (an absolute path to a
   private key file owned by the deployment) -- source ``environment``;
2. the key saved from the MSA hub, one file per provider under
   ``<BMS config dir>/msa-providers/<id>.key`` -- source ``saved``.

An environment setting always wins, even when the file it names is unusable,
so a deployment-owned configuration is never silently replaced. Saved files are
written atomically with mode 0600 inside a 0700 directory. This module returns
paths and booleans only: a key is never returned, logged, or copied into job
parameters or provenance. The transport opens the file itself when it signs a
request.
"""
from __future__ import annotations

import os
import re
import stat
import tempfile
from dataclasses import dataclass
from pathlib import Path

STORE_DIRNAME = "msa-providers"
MAX_KEY_BYTES = 4096
_KEY_PATTERN = re.compile(r"[!-~]+")


@dataclass(frozen=True)
class ProviderDescriptor:
    id: str
    label: str
    needs_key: bool
    key_file_env: str | None = None
    # Name of the environment variable that overrides the service endpoint, for
    # providers whose endpoint is configurable. None means the endpoint is fixed.
    endpoint_env: str | None = None


PROVIDER_DESCRIPTORS: tuple[ProviderDescriptor, ...] = (
    ProviderDescriptor(id="colabfold_api", label="ColabFold API", needs_key=False),
    ProviderDescriptor(id="neurosnap_api", label="Neurosnap API", needs_key=True,
                       key_file_env="BMS_NEUROSNAP_API_KEY_FILE"),
)
PROVIDER_IDS: tuple[str, ...] = tuple(item.id for item in PROVIDER_DESCRIPTORS)


class CredentialError(ValueError):
    """A credential or endpoint value that cannot be saved."""


def get_descriptor(provider_id: str) -> ProviderDescriptor:
    for descriptor in PROVIDER_DESCRIPTORS:
        if descriptor.id == provider_id:
            return descriptor
    raise KeyError(provider_id)


def store_dir() -> Path:
    from biomodstack_runtime_profile import get_biomodstack_config_dir
    return get_biomodstack_config_dir() / STORE_DIRNAME


def saved_key_path(provider_id: str) -> Path:
    return store_dir() / f"{get_descriptor(provider_id).id}.key"


def saved_endpoint_path(provider_id: str) -> Path:
    return store_dir() / f"{get_descriptor(provider_id).id}.endpoint"


def _private_file_ok(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and 1 <= info.st_size <= MAX_KEY_BYTES
            and info.st_uid == os.getuid()
            and not info.st_mode & 0o077 and os.access(path, os.R_OK))


def _write_private(path: Path, data: bytes) -> None:
    directory = path.parent
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=directory)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


def environment_key_file(provider_id: str) -> Path | None:
    """Path named by the provider's environment variable, or None when unset."""
    name = get_descriptor(provider_id).key_file_env
    value = os.environ.get(name) if name else None
    if not value:
        return None
    path = Path(value)
    if not path.is_absolute():
        raise ValueError(f"{name} must be an absolute protected credential path")
    return path


def credential_file(provider_id: str) -> Path | None:
    """Key file the transport should read: environment first, then saved."""
    path = environment_key_file(provider_id)
    if path is not None:
        return path
    if not get_descriptor(provider_id).needs_key:
        return None
    saved = saved_key_path(provider_id)
    return saved if _private_file_ok(saved) else None


def credential_state(provider_id: str) -> dict:
    """``{"configured": bool, "source": "environment" | "saved" | None}``."""
    descriptor = get_descriptor(provider_id)
    if not descriptor.needs_key:
        return {"configured": False, "source": None}
    try:
        from_environment = environment_key_file(provider_id)
    except ValueError:
        return {"configured": False, "source": "environment"}
    if from_environment is not None:
        return {"configured": _private_file_ok(from_environment), "source": "environment"}
    if _private_file_ok(saved_key_path(provider_id)):
        return {"configured": True, "source": "saved"}
    return {"configured": False, "source": None}


def save_credential(provider_id: str, api_key: str, endpoint: str | None = None) -> None:
    descriptor = get_descriptor(provider_id)
    if not descriptor.needs_key:
        raise CredentialError(f"{descriptor.label} does not use an API key")
    key = (api_key or "").strip()
    if not key:
        raise CredentialError("API key must not be empty")
    if len(key.encode("utf-8", "ignore")) > MAX_KEY_BYTES or not _KEY_PATTERN.fullmatch(key):
        raise CredentialError("API key must be printable characters without spaces")
    endpoint_value = (endpoint or "").strip()
    if endpoint_value:
        if descriptor.endpoint_env is None:
            raise CredentialError(f"{descriptor.label} does not have a configurable endpoint")
        if not re.fullmatch(r"https://[A-Za-z0-9.-]+(:\d+)?(/[!-~]*)?", endpoint_value):
            raise CredentialError("Endpoint must be an https URL")
    _write_private(saved_key_path(provider_id), key.encode("ascii"))
    endpoint_path = saved_endpoint_path(provider_id)
    if endpoint_value:
        _write_private(endpoint_path, endpoint_value.encode("ascii"))
    else:
        endpoint_path.unlink(missing_ok=True)


def clear_credential(provider_id: str) -> bool:
    """Remove the saved key and endpoint. True when a saved key existed."""
    path = saved_key_path(provider_id)
    existed = path.exists() or path.is_symlink()
    path.unlink(missing_ok=True)
    saved_endpoint_path(provider_id).unlink(missing_ok=True)
    return existed


def saved_endpoint(provider_id: str) -> str | None:
    """Endpoint for a provider that allows one: environment first, then saved."""
    descriptor = get_descriptor(provider_id)
    if descriptor.endpoint_env is None:
        return None
    value = os.environ.get(descriptor.endpoint_env)
    if value:
        return value
    try:
        return saved_endpoint_path(provider_id).read_text(encoding="ascii").strip() or None
    except (OSError, UnicodeError):
        return None
