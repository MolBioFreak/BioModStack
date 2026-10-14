"""Task publication transport regressions; synthetic bytes, not folding proof."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts import write_structure_producer_manifest as producer


def native_tree(root, *, seeds=(42, 43, 44), samples=5, suffix='cif', confidence=True):
    for seed in seeds:
        for sample in range(samples):
            path = root / f'complex/seed_{seed}/predictions/complex_sample_{sample}.{suffix}'
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f'native coordinates {seed} {sample}\n'.encode())
            if confidence:
                for kind, name in producer.protenix_confidence_names(path.with_suffix('.cif').name).items():
                    path.with_name(name).write_text(json.dumps({'seed': seed, 'sample': sample, 'kind': kind}))


def publish(root, task, *, sample='invocation-A', sequence=False, suffix='cif', published_root='pdb_files/predictions'):
    native = producer.build_manifest(predictions_root=root, producer_method='protenix',
                                     producer_sample=sample, formats=['mmcif' if suffix == 'cif' else 'pdb'])
    manifest = native
    if sequence:
        metadata = dict(producer_artifact_id=sample, producer_artifact_key=sample,
            producer_sample=sample, producer_sequence='G', producer_fold=None,
            producer_rank=None, producer_submission_id=sample, producer_submission_name=sample,
            original_submission_identity={'id': sample, 'name': sample})
        manifest = producer.sequence_manifest(native, metadata)
    raw = (json.dumps(manifest, sort_keys=True, separators=(',', ':')) + '\n').encode()
    digest = hashlib.sha256(raw).hexdigest()
    producer.write_publication(manifest_bytes=raw, native_candidates=native['candidates'],
        manifest=manifest, predictions_root=root, publication_dir=task/'producer_publication',
        published_structure_root=published_root)
    receipt = json.loads((task/'producer_publication'/digest/'publication.json').read_bytes())
    assert (task/'producer_publication'/digest/'producer_candidates.json').read_bytes() == raw
    return native, manifest, digest, receipt


@pytest.mark.parametrize('sequence', [False, True], ids=['complex', 'sequence'])
@pytest.mark.parametrize('published_root', ['pdb_files/predictions', 'validation/protenix_v2/candidate-A'])
def test_every_seed_sample_and_exact_confidence_is_staged_and_bound(tmp_path, sequence, published_root):
    root = tmp_path/'native'
    native_tree(root)
    before = {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()}
    native, manifest, digest, receipt = publish(root, tmp_path/'task', sequence=sequence, published_root=published_root)
    assert len(receipt['bindings']) == 15
    assert len({b['published_relative_path'] for b in receipt['bindings']}) == 15
    assert receipt['schema_version'] == 1
    assert receipt['producer_manifest']['sha256'] == digest
    for candidate, declared, binding in zip(native['candidates'], manifest['candidates'], receipt['bindings'], strict=True):
        relative = Path(candidate['producer_output_key'])
        assert binding['producer_output_key'] == declared['producer_output_key']
        assert binding['published_relative_path'] == f'{published_root}/{digest}/{relative.as_posix()}'
        staged = tmp_path/'task/structure_publication'/digest/relative
        assert staged.read_bytes() == before[relative]
        assert binding['sha256'] == hashlib.sha256(staged.read_bytes()).hexdigest()
        assert binding['size_bytes'] == staged.stat().st_size
        for kind, name in producer.protenix_confidence_names(relative.name).items():
            descriptor = binding['confidence'][kind]
            companion = staged.with_name(name)
            assert companion.read_bytes() == before[relative.with_name(name)]
            assert descriptor['published_relative_path'] == f'{published_root}/{digest}/{relative.with_name(name).as_posix()}'
            assert descriptor['sha256'] == hashlib.sha256(companion.read_bytes()).hexdigest()
            assert descriptor['size_bytes'] == companion.stat().st_size
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    assert len(list((tmp_path/'task/structure_publication').rglob('*.cif'))) == 15
    assert len(list((tmp_path/'task/structure_publication').rglob('*.json'))) == 30


def test_same_native_paths_in_distinct_invocations_do_not_collide(tmp_path):
    root = tmp_path/'native'
    native_tree(root, seeds=(42,), samples=1)
    a = publish(root, tmp_path/'task-A', sample='A')
    b = publish(root, tmp_path/'task-B', sample='B')
    assert a[2] != b[2]
    assert a[3]['bindings'][0]['producer_output_key'] == b[3]['bindings'][0]['producer_output_key']
    paths_a = {row['published_relative_path'] for row in a[3]['bindings']}
    paths_b = {row['published_relative_path'] for row in b[3]['bindings']}
    assert paths_a.isdisjoint(paths_b)


def test_missing_optional_confidence_does_not_become_a_new_admission_gate(tmp_path):
    root = tmp_path/'native'
    native_tree(root, seeds=(42,), samples=1, confidence=False)
    _, _, _, receipt = publish(root, tmp_path/'task')
    assert receipt['bindings'][0]['confidence'] == {}


def test_finalized_pdb_keeps_all_seeds_and_existing_optional_confidence_inventory(tmp_path):
    root = tmp_path/'native'
    native_tree(root, suffix='pdb')
    _, _, digest, receipt = publish(root, tmp_path/'task', suffix='pdb')
    staged = tmp_path/'task/structure_publication'/digest
    assert len(receipt['bindings']) == 15
    assert len(list(staged.rglob('*.pdb'))) == 15
    assert len(list(staged.rglob('*.json'))) == 30
    assert all('confidence' not in row for row in receipt['bindings'])


def test_changed_native_structure_retains_existing_custody_check(tmp_path):
    root = tmp_path/'native'
    native_tree(root, seeds=(42,), samples=1)
    manifest = producer.build_manifest(predictions_root=root, producer_method='protenix',
                                      producer_sample='A', formats=['mmcif'])
    (root/manifest['candidates'][0]['producer_output_key']).write_bytes(b'changed')
    with pytest.raises(ValueError, match='source changed'):
        producer.write_publication(manifest_bytes=json.dumps(manifest).encode(),
            native_candidates=manifest['candidates'], manifest=manifest, predictions_root=root,
            publication_dir=tmp_path/'task/producer_publication', published_structure_root='pdb_files/predictions')
