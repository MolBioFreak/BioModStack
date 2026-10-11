"""Owner-level payload reuse; tiny native evidence, no model execution."""
import errno
import hashlib
import json
import os
from pathlib import Path
import shutil

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('placement', ['shared', 'cross_filesystem', 'changing_donor'])
def test_boltz_native_evidence_retains_exact_bytes_without_duplicate_npz(placement, monkeypatch):
    from tests.test_boltz_native_producer_identity import NativeIdentityTest
    from aligned_error_utils import load_aligned_error_artifact
    fixture = NativeIdentityTest()
    fixture.setUp()
    original_link = os.link
    native = {
        name: (fixture.orig / name).read_bytes()
        for name in ['pae_input_model_0.npz', 'plddt_input_model_0.npz', 'confidence_input_model_0.json']
    }
    try:
        if placement == 'cross_filesystem':
            def link(*args, **kwargs):
                raise OSError(errno.EXDEV, 'fixture filesystem boundary')
            monkeypatch.setattr(os, 'link', link)
        elif placement == 'changing_donor':
            def link(source, target, **kwargs):
                result = original_link(source, target, **kwargs)
                if Path(source).name == 'pae_input_model_0.npz':
                    Path(source).write_bytes(b'changed after completed evidence was measured')
                return result
            monkeypatch.setattr(os, 'link', link)
        manifest = fixture.build()
        evidence = manifest['candidates'][0]['boltz_native_identity']
        pairs = [(fixture.orig / name, fixture.pred / name, body) for name, body in native.items()]
        pairs.append((fixture.ledger, fixture.pred / evidence['processed_structure']['artifact_key'], fixture.ledger.read_bytes()))
        identities = {str(target): target.stat().st_ino for _, target, _ in pairs}
        for source, target, body in pairs:
            assert target.read_bytes() == body
            if placement == 'shared':
                assert target.samefile(source)
            elif placement == 'cross_filesystem' or source.name == 'pae_input_model_0.npz':
                assert not target.samefile(source)
        if placement == 'shared':
            assert fixture.build() == manifest
            assert identities == {str(target): target.stat().st_ino for _, target, _ in pairs}
        shutil.rmtree(fixture.native)
        descriptor = evidence['aligned_error']
        received = load_aligned_error_artifact(
            aligned_error_path=fixture.pred / descriptor['artifact_key'],
            aligned_error_format='boltz_pae_npz', structure_path=fixture.structure,
            matrix_key=descriptor['matrix_key'], contract_revision=1,
            candidate_id='input_model_0.pdb', document_id='input_model_0.pdb',
            identity_evidence=descriptor['identity_evidence'])
        np.testing.assert_array_equal(received.matrix, fixture.matrix)
        for _, target, body in pairs:
            assert hashlib.sha256(target.read_bytes()).digest() == hashlib.sha256(body).digest()
    finally:
        fixture.doCleanups()


@pytest.mark.parametrize('readonly', [True, False])
def test_maturation_collection_and_next_stage_reuse_only_closed_publication(tmp_path, monkeypatch, readonly):
    monkeypatch.syspath_prepend(str(ROOT / 'scripts'))
    from collect_maturation_outputs import collect_files
    from maturation_identity import stage
    source_root = tmp_path / 'child'
    source = source_root / 'run/ppiflow/results/candidate_ppiflow.pdb'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'REMARK inert retained coordinate fixture\nEND\n')
    if readonly:
        source.chmod(0o444)
    collect = tmp_path / 'collect'
    collect.mkdir()
    monkeypatch.chdir(collect)
    names = collect_files([str(source_root)], ['*.pdb'], ['run/ppiflow/results'])
    retained = collect / names[0]
    assert source.samefile(retained) is readonly
    binding = tmp_path / 'binding.json'
    binding.write_text(json.dumps([{'path': str(retained), 'meta': {'document_id': 'original'}}]))
    output = tmp_path / 'next-input'
    stage(binding, output)
    assert retained.samefile(output / 'source_000000.pdb')
    assert json.loads((output / 'source_identity.json').read_text())[0]['source_meta'] == {'document_id': 'original'}
    monkeypatch.chdir(tmp_path)
    shutil.rmtree(source_root)
    shutil.rmtree(collect)
    assert (output / 'source_000000.pdb').read_bytes().endswith(b'END\n')
