"""Backfill terminal-manifest immutability for databases already at migration 19."""
from __future__ import annotations

from migrations.add_ont_terminal_artifact_manifests import migrate as _migrate_terminal_artifacts


def migrate(db_path: str) -> None:
    _migrate_terminal_artifacts(db_path)