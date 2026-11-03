"""Invocation-private native HF refs over selected immutable evaluator snapshots."""
from __future__ import annotations

import json
from pathlib import Path
import shutil


def prepare_hf_view(view: Path, source: str | None, repositories: list[str]) -> Path | None:
    """Link payloads; let HF own refs/main containing the reviewed exact revision.

    Missing optional bindings retain native loader behavior, not a new gate.
    The caller removes this private view before publishing its invocation.
    """
    if view.exists():
        shutil.rmtree(view)
    if not source or not repositories:
        return None
    from huggingface_hub.file_download import _cache_commit_hash_for_specific_revision

    source_root = Path(source).expanduser().absolute()
    manifest = json.loads(Path(__file__).with_name('assets.json').read_text())
    for entry in manifest['selected_oracle_sources']['native_hf']['hf']:
        if entry['repo'] not in repositories:
            continue
        folder = 'models--' + entry['repo'].replace('/', '--')
        target = view / folder
        snapshots = target / 'snapshots'
        snapshots.mkdir(parents=True, exist_ok=True)
        revision = entry['revision']
        (snapshots / revision).symlink_to(source_root / folder / 'snapshots' / revision,
                                          target_is_directory=True)
        _cache_commit_hash_for_specific_revision(str(target), 'main', revision)
    return view if view.exists() else None
