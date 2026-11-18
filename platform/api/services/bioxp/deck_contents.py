"""Operator-entered liquid amounts per deck well for the Live deck tab.

A BMS-side record only: the operator's estimate, kept across restarts. Nothing
here is sent to the robot except as the expected volume of a pressure search.
"""
from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from pathlib import Path

STATION = re.compile(r"[A-Z][A-Z0-9_]{1,47}\Z")
WELL = re.compile(r"(?:[A-H](?:[1-9]|1[0-2])|[1-8])\Z")
MAX_UL = 100000.0


class DeckContentsError(ValueError):
    pass


class DeckContentsStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    def _read(self) -> dict[str, dict[str, float]]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            raise DeckContentsError("Saved deck contents are unreadable") from exc
        wells = raw.get("wells") if isinstance(raw, dict) else None
        return {str(s): {str(w): float(v) for w, v in row.items()} for s, row in wells.items()} if isinstance(wells, dict) else {}

    def _write(self, wells: dict[str, dict[str, float]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, name = tempfile.mkstemp(prefix=".deck-contents-", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump({"schema": "bms.bioxp-deck-contents.v1", "wells": wells}, stream, indent=1, sort_keys=True)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(name, self.path)
        finally:
            if os.path.exists(name):
                os.unlink(name)

    def _locked(self, change):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        descriptor = os.open(str(self.path) + ".lock", os.O_CREAT | os.O_RDWR, 0o600)
        try:
            fcntl.flock(descriptor, fcntl.LOCK_EX)
            wells = self._read()
            if change is not None:
                change(wells)
                self._write({s: row for s, row in wells.items() if row})
                wells = {s: row for s, row in wells.items() if row}
            return {"schema": "bms.bioxp-deck-contents.v1", "wells": wells}
        finally:
            fcntl.flock(descriptor, fcntl.LOCK_UN)
            os.close(descriptor)

    def read(self) -> dict:
        return self._locked(None)

    def set_wells(self, station: str, volumes: dict[str, float | None]) -> dict:
        """Set (number) or clear (None) the listed wells of one station."""
        if not STATION.fullmatch(station):
            raise DeckContentsError("Unknown station name")
        for well, value in volumes.items():
            if not WELL.fullmatch(well):
                raise DeckContentsError(f"Unknown well {well}")
            if value is not None and not 0 <= value <= MAX_UL:
                raise DeckContentsError("Volume must be between 0 and 100000 uL")

        def change(wells):
            row = wells.setdefault(station, {})
            for well, value in volumes.items():
                if value is None:
                    row.pop(well, None)
                else:
                    row[well] = float(value)
        return self._locked(change)

    def clear(self, station: str | None = None) -> dict:
        """Clear one station, or the whole deck when no station is given."""
        if station is not None and not STATION.fullmatch(station):
            raise DeckContentsError("Unknown station name")
        return self._locked(lambda wells: wells.clear() if station is None else wells.pop(station, None))
