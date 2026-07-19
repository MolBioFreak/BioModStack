from __future__ import annotations

import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from pydantic import ValidationError

from .errors import ProfileStoreError
from .models import BioXpProfile


class BioXpProfileStore:
    """Single lock-protected, atomic JSON profile authority."""

    def __init__(self, path: Path, *, legacy_path: Path | None = None) -> None:
        self.path = Path(path)
        self.legacy_path = Path(legacy_path) if legacy_path is not None else None
        self.lock_path = self.path.with_suffix(self.path.suffix + ".lock")

    @contextmanager
    def _locked(self) -> Iterator[None]:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(self.lock_path, os.O_CREAT | os.O_RDWR, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            yield
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def exists(self) -> bool:
        return self.path.exists()

    def load(self) -> BioXpProfile | None:
        with self._locked():
            return self._load_unlocked()

    def _load_unlocked(self) -> BioXpProfile | None:
        if not self.path.exists():
            return None
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            return BioXpProfile.model_validate(raw)
        except (OSError, json.JSONDecodeError, ValidationError, TypeError) as exc:
            raise ProfileStoreError("BioXP profile is malformed or unreadable") from exc

    def save(self, profile: BioXpProfile) -> BioXpProfile:
        with self._locked():
            self._save_unlocked(profile)
        return profile

    def _save_unlocked(self, profile: BioXpProfile) -> None:
        payload = profile.model_dump_json(indent=2) + "\n"
        temporary_name: str | None = None
        try:
            descriptor, temporary_name = tempfile.mkstemp(prefix=".profile-", suffix=".tmp", dir=self.path.parent)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary_name, self.path)
            temporary_name = None
            os.chmod(self.path, 0o600)
            directory_fd = os.open(self.path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as exc:
            raise ProfileStoreError(f"Could not atomically persist BioXP profile: {exc}") from exc
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def forget(self) -> None:
        with self._locked():
            try:
                self.path.unlink(missing_ok=True)
            except OSError as exc:
                raise ProfileStoreError(f"Could not remove BioXP profile: {exc}") from exc

    def migrate_legacy(self, *, display_name: str = "BioXP3200") -> BioXpProfile | None:
        """Explicit one-shot migration; normal ``load`` never reads legacy state."""
        if self.legacy_path is None:
            return None
        with self._locked():
            if self.path.exists() or not self.legacy_path.exists():
                return None
            try:
                value = self.legacy_path.read_text(encoding="utf-8").strip()
                if not value:
                    raise ProfileStoreError("Legacy BioXP linkage file is empty")
                if value.startswith("{"):
                    raw = json.loads(value)
                    if not isinstance(raw, dict):
                        raise ProfileStoreError("Legacy BioXP profile must be a JSON object")
                    profile = BioXpProfile(
                        display_name=str(raw.get("display_name") or display_name),
                        api_url=str(raw.get("api_url") or raw.get("robot_api_url") or ""),
                    )
                else:
                    profile = BioXpProfile(display_name=display_name, api_url=value)
                self._save_unlocked(profile)
                self.legacy_path.unlink()
                return profile
            except (OSError, ValueError, ValidationError) as exc:
                raise ProfileStoreError(f"Could not migrate legacy BioXP linkage profile: {exc}") from exc
