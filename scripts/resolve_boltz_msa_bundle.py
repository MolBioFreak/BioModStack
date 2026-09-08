"""Resolve controller-packaged native Boltz YAML under its relocated task root.

This is a file-only consumer. Missing alignments are errors, never searches.
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from biomodstack_msa_handoff import validate_a3m


def resolve_yaml(path: Path) -> None:
    import yaml
    payload = yaml.safe_load(path.read_text())
    for entry in payload.get('sequences', []):
        protein = entry.get('protein')
        if protein is None:
            continue
        value = protein.get('msa')
        if not value or value == 'empty':
            raise ValueError('MSA-enabled Boltz requires controller-prepared or supplied alignments')
        alignment = Path(value)
        if not alignment.is_absolute():
            alignment = path.parent / alignment
        validate_a3m(alignment, protein['sequence'])
        protein['msa'] = str(alignment.resolve())
    path.write_text(yaml.safe_dump(payload, sort_keys=False))


if __name__ == '__main__':
    root = Path(sys.argv[1])
    files = sorted(root.rglob('*.yaml')) + sorted(root.rglob('*.yml')) if root.is_dir() else [root]
    if not files:
        raise ValueError('No Boltz YAML input found')
    for path in files:
        resolve_yaml(path)
