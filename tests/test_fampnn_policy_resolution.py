"""Synthetic, data-only PrepFAMPNN transform receipts and native outputs."""
import importlib.util
import json
from pathlib import Path
import sys
import hashlib

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))


def load():
    path = ROOT / 'scripts/fampnn_policy_resolution.py'
    assert path.exists(), 'post-preparation declaration resolver missing'
    spec = importlib.util.spec_from_file_location('fampnn_policy_resolution', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path):
    source = tmp_path / 'original.pdb'
    prepared = tmp_path / 'prepared'
    prepared.mkdir()
    output = prepared / 'native.pdb'
    source.write_text('ATOM      1  CA  ALA H  10      10.000  10.000  10.000  1.00 20.00           C\nEND\n')
    output.write_text(source.read_text().replace('H  10', 'H   1'))
    declaration = dict(schema_version=1, owner='antibody_denovo', version=1,
        declaration='authorized_sequence_design_region', input_domain=['H:10:'],
        sequence_design=['H:10:'], summary=['H:10:'], fixed=[],
        summary_override=None, mutation_override=[], allow_summary_override=True,
        require_full_coverage=False)
    return source, output, declaration


def test_preflight_resolves_explicit_transform_then_binds_only_observed_candidates(tmp_path):
    module = load()
    source, output, declaration = fixture(tmp_path)
    receipt = module.prep_receipt(source, output, [('H:10:', 'H:1:')])
    scopes = module.resolve_declaration(declaration, {output.stem: receipt}, output.parent)
    assert scopes['inputs']['native']['summary'] == ['H:1:']
    assert scopes['inputs']['native']['mutation_override'] == []
    assert 'producer_candidate_ids' not in str(scopes)
    native = tmp_path / 'samples'
    native.mkdir()
    (native / 'native_sample7.pdb').write_bytes(output.read_bytes())
    policy = module.bind_native_candidates(scopes, native)
    from analyse_fampnn_seq_probs import _validate_policy, _resolve_policy
    _validate_policy(policy)
    summary, authorized, mutations = _resolve_policy(policy, 'native', ['H:1:'])
    assert summary == authorized == {'H:1:'}
    assert mutations == set()
    binding = policy['inputs']['native']['artifact_binding']
    assert binding['producer_candidate_ids'] == ['native_sample7']
    assert binding['source_pdb_sha256'] == hashlib.sha256(output.read_bytes()).hexdigest()


@pytest.mark.parametrize('failure', ['no_provenance', 'changed_bytes', 'missing_pair', 'foreign_override'])
def test_impossible_prep_resolution_rejected_before_native_run(tmp_path, failure):
    module = load()
    source, output, declaration = fixture(tmp_path)
    receipt = module.prep_receipt(source, output, [('H:10:', 'H:1:')])
    if failure == 'no_provenance':
        receipt['transform'] = 'unknown'
    elif failure == 'changed_bytes':
        output.write_text(output.read_text() + 'REMARK changed\n')
    elif failure == 'missing_pair':
        receipt['pairs'] = []
    else:
        declaration['mutation_override'] = ['X:1:']
    with pytest.raises(ValueError):
        module.resolve_declaration(declaration, {output.stem: receipt}, output.parent)


def test_missing_or_foreign_native_candidates_fail_closed(tmp_path):
    module = load()
    source, output, declaration = fixture(tmp_path)
    scopes = module.resolve_declaration(declaration,
        {'native': module.prep_receipt(source, output, [('H:10:', 'H:1:')])}, output.parent)
    native = tmp_path / 'samples'
    native.mkdir()
    with pytest.raises(ValueError):
        module.bind_native_candidates(scopes, native)
    (native / 'foreign_sample0.pdb').write_bytes(output.read_bytes())
    with pytest.raises(ValueError):
        module.bind_native_candidates(scopes, native)


def test_real_prep_main_publishes_residue_object_provenance(tmp_path, monkeypatch):
    from types import SimpleNamespace
    source, output, declaration = fixture(tmp_path)
    # Physical engine double only; exercise the actual preparation main.
    class Info:
        def chain(self, i, value=None):
            return 'H'
        def number(self, i):
            return 10
        def icode(self, i):
            return ' '
    class Pose:
        def pdb_info(self, value=None):
            return Info()
        def total_residue(self):
            return 1
        def chain(self, i):
            return 1
        def dump_pdb(self, path):
            Path(path).write_bytes(source.read_bytes())
    monkeypatch.setitem(sys.modules, 'pyrosetta', SimpleNamespace(init=lambda _: None, pose_from_pdb=lambda _: Pose()))
    spec = importlib.util.spec_from_file_location('prep_fixture', ROOT / 'scripts/prep_fampnn_designs.py')
    prep = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(prep)
    monkeypatch.setattr(sys, 'argv', ['prep', '--input_dir', str(tmp_path), '--out_dir', str(output.parent), '--publish_identity'])
    prep.main()
    receipt = json.loads((output.parent / 'original.fampnn_prep.json').read_text())
    assert receipt['pairs'] == [['H:10:', 'H:10:']]
    assert receipt['prepared_pdb_sha256'] == hashlib.sha256((output.parent / 'original.pdb').read_bytes()).hexdigest()
