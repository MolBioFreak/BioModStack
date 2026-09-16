"""Native-shaped exports exercise source-bound roles, never letter heuristics."""
import sys
from pathlib import Path
import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))


def atom(chain, n):
    return f'ATOM  {n:5d}  CA  ALA {chain}{n:4d}    {0:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C'


def test_native_export_and_preparation_preserve_roles(tmp_path):
    from antibody_fampnn_provenance import native_export, read_export, carry_export, NATIVE_SOURCES
    from fampnn_policy_resolution import prep_receipt
    lines = [atom('H', 1), atom('H', 2), atom('L', 3), atom('T', 4)]
    source = tmp_path / 'native.pdb'
    source.write_text('\n'.join(native_export(lines, ['H','H','L','T'], {'H1':[2],'L1':[3]}, NATIVE_SOURCES)))
    proof = read_export(source)
    assert proof['roles']['heavy'] == ['H:1:', 'H:2:']
    assert proof['cdrs'] == ['H:2:', 'L:3:']
    prepared = tmp_path / 'prepared.pdb'
    prepared.write_text('\n'.join([atom('Z', 1), atom('Z', 2), atom('Y', 3), atom('X', 4)]))
    pairs = [('H:1:','Z:1:'), ('H:2:','Z:2:'), ('L:3:','Y:3:'), ('T:4:','X:4:')]
    carry_export(source, prepared, pairs)
    assert read_export(prepared)['roles']['heavy'] == ['Z:1:', 'Z:2:']
    assert prep_receipt(source, prepared, pairs)['antibody']['cdrs'] == ['Z:2:', 'Y:3:']


def test_output_letters_and_unverified_sources_are_not_role_proof(tmp_path):
    from antibody_fampnn_provenance import native_export, read_export, NATIVE_SOURCES
    source = tmp_path / 'letters.pdb'
    source.write_text(atom('H', 1))
    assert read_export(source) is None
    with pytest.raises(ValueError, match='source'):
        native_export([atom('H', 1)], ['H'], {'H1':[1]}, dict(NATIVE_SOURCES, **{'scripts/rfdiffusion_inference.py':'0'*64}))


def test_source_binding_checks_loaded_module_not_just_checkout(tmp_path, monkeypatch):
    import hashlib
    from types import ModuleType, SimpleNamespace
    import antibody_fampnn_provenance as adapter
    for name in adapter.NATIVE_SOURCES:
        path = tmp_path/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'fixture source')
    monkeypatch.setattr(adapter, 'NATIVE_SOURCES', {name:hashlib.sha256(b'fixture source').hexdigest() for name in adapter.NATIVE_SOURCES})
    util = ModuleType('rfantibody.util')
    util.io = SimpleNamespace(__file__='/different/checkout/io.py', ab_write_pdblines=lambda **kw: [])
    inference = ModuleType('rfantibody.rfdiffusion.inference')
    inference.model_runners = SimpleNamespace(__file__=str(tmp_path/'src/rfantibody/rfdiffusion/inference/model_runners.py'),
        AbSampler=type('Sampler', (), {'sample_init':lambda self: None}))
    monkeypatch.setitem(sys.modules, 'rfantibody.util', util)
    monkeypatch.setitem(sys.modules, 'rfantibody.rfdiffusion.inference', inference)
    with pytest.raises(ValueError, match='loaded'):
        adapter.install_native_export(tmp_path)


def test_existing_export_and_prep_callers_are_wired():
    root = Path(__file__).resolve().parents[1]
    assert 'rfantibody_inference_wrapper.py --bms-role-export' in (root/'modules/rfantibody.nf').read_text()
    assert 'carry_export(pdb_file, output_path' in (root/'scripts/prep_fampnn_designs.py').read_text()
    assert '--require_role_provenance --prepared_dir fampnn_input' in (root/'modules/fampnn.nf').read_text()
    assert 'fampnn_analysis_declaration: analysisContract.declaration' in (root/'workflows/antibody_denovo.nf').read_text()


def test_substituted_export_bytes_rejected(tmp_path):
    from antibody_fampnn_provenance import native_export, read_export, NATIVE_SOURCES
    source = tmp_path / 'native.pdb'
    source.write_text('\n'.join(native_export([atom('H', 1)], ['H'], {'H1':[1]}, NATIVE_SOURCES)))
    source.write_text(source.read_text().replace('ALA', 'GLY'))
    with pytest.raises(ValueError, match='bytes'):
        read_export(source)
