"""Prevent the pending extension from weakening existing verification admission."""
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]


def test_strict_clone_route_still_requires_authoritative_reference():
    text=(ROOT/'workflows/ngs/wf_clone_validation.nf').read_text()
    assert 'if (!has_reference)' in text
    assert 'requires --reference_fasta for authoritative' in text
    module=(ROOT/'modules/ngs/clone_validation.nf').read_text()
    assert 'if (!referencePath)' in module
    assert 'requires an authoritative full reference' in module


def test_reconstruction_is_explicitly_held_not_claimed_live():
    text=(ROOT/'docs/plans/2026-09-17-plasmid-reconstruction-gate.md').read_text()
    assert 'NOT implemented, registered, deployed' in text
    assert 'dummy sequence' in text
