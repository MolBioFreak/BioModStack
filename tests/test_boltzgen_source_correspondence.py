"""Pinned installed parser/schema/features/writer, with no model or sampling.

Set BMS_TEST_BOLTZGEN_IMAGE to the existing approved image. Two fresh processes
exercise original and instrumented native code, then compare all emitted CIF bytes.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import boltzgen_source_correspondence as transport


def native_probe(work, instrumented):
    import random
    import numpy as np
    import torch
    import gemmi
    from rdkit import Chem
    if instrumented:
        assert any(isinstance(f, transport.Finder) for f in sys.meta_path), 'wrapper startup hook missing'
    from boltzgen.data.parse.schema import YamlDesignParser
    from boltzgen.data.tokenize.tokenizer import Tokenizer
    from boltzgen.data.data import Input
    from boltzgen.data.feature.featurizer import Featurizer
    from boltzgen.task.predict.data_from_yaml import collate
    from boltzgen.task.predict.writer import DesignWriter, FoldingWriter
    from boltzgen.data.write.mmcif import to_mmcif

    work = Path(work)
    work.mkdir(parents=True, exist_ok=True)
    out = work / ('observed' if instrumented else 'original')
    out.mkdir(exist_ok=True)
    os.environ[transport.ENV] = str(out / 'events')
    np.random.seed(23)
    random.seed(23)
    torch.manual_seed(23)
    # Inert reference molecule: named atoms and fixed conformer, no force field.
    mol = Chem.MolFromSmiles('NCC(=O)O')
    conf = Chem.Conformer(mol.GetNumAtoms())
    for i, (atom, name) in enumerate(zip(mol.GetAtoms(), ['N', 'CA', 'C', 'O', 'OXT'])):
        atom.SetProp('name', name)
        conf.SetAtomPosition(i, (float(i), float(i % 2), 0.0))
    conf.SetProp('name', 'Ideal')
    mol.AddConformer(conf)
    mols = {'GLY': mol}

    def pdb(path, chain, numbers, shift=0):
        lines = []
        for i, (number, insertion) in enumerate(numbers):
            for j, name in enumerate(('N', 'CA', 'C', 'O')):
                lines.append(f'ATOM  {len(lines)+1:5d} {name:^4} GLY {chain}{number:4d}{insertion:1}   '
                             f'{float(i*4+j+shift):8.3f}{float(j%2):8.3f}{0.:8.3f}'
                             f'{1.:6.2f}{20.:6.2f}          {name[0]:>2}  ')
        path.write_text('\n'.join(lines) + '\nTER\nEND\n')

    target = work / 'independent.pdb'
    alternate = work / 'alternate.pdb'
    scaffold = work / 'scaffold.pdb'
    pdb(target, 'A', [(5, ''), (19, ''), (19, 'B'), (42, '')])
    pdb(alternate, 'A', [(8, ''), (21, ''), (21, 'C'), (80, '')], 10)
    pdb(scaffold, 'A', [(100, ''), (108, '')], 25)
    # A real mmCIF seqres gap has no author selector; exercise native missingness.
    from boltzgen.data.parse.mmcif import parse_mmcif
    gap = gemmi.read_structure(str(target))
    gap.setup_entities()
    for entity in gap.entities:
        entity.full_sequence = ['GLY'] * 5
    for residue, label in zip(gap[0][0], (1, 2, 4, 5)):
        residue.label_seq = label
    gap_path = work / 'missing.cif'
    gap.make_mmcif_document().write_file(str(gap_path))
    gap_parsed = parse_mmcif(gap_path, mols=mols, moldir=work, use_assembly=False).data
    assert not all(gap_parsed.residues['is_present'])
    if instrumented:
        assert all(not r[transport.FIELD] for r in gap_parsed.residues if not r['is_present'])
    (out / 'missing.cif').write_text(to_mmcif(gap_parsed))
    # The pinned multi-model polymer parser omits entity_poly_seq on model 2.
    # Preserve that native boundary; do not repair scientific parsing in an
    # observational exporter. File-list state selection below is supported.
    ensemble = gap.clone()
    model = gemmi.Model('2')
    for chain in ensemble[0]:
        model.add_chain(chain.clone())
    ensemble.add_model(model)
    ensemble_path = work / 'multi-model.cif'
    ensemble.make_mmcif_document().write_file(str(ensemble_path))
    try:
        parse_mmcif(ensemble_path, mols=mols, moldir=work, use_assembly=False)
    except TypeError as exc:
        assert 'NoneType' in str(exc)
    else:
        raise AssertionError('pinned native multi-model boundary unexpectedly changed')
    import yaml
    for path in (target, alternate):
        path.with_suffix('.yaml').write_text(yaml.safe_dump({'path': path.name,
            'include': [{'chain': {'id': 'A', 'res_index': '2..4'}}],
            'reset_res_index': [{'chain': {'id': 'A'}}],
            'design_insertions': [{'insertion': {'id': 'A', 'res_index': 3, 'num_residues': 1}}]}))
    parser = YamlDesignParser(work)
    selected = []
    for fuse in (False, True):
        schema = {'entities': [
            {'file': {'path': [target.with_suffix('.yaml').name, alternate.with_suffix('.yaml').name]}},
            {'file': {'path': scaffold.name, **({'fuse': 'A'} if fuse else {})}}]}
        parsed = parser.parse_boltzgen_schema('inert', schema, mols, work, work)
        structure = parsed.structure
        (out / f'parsed-{fuse}.cif').write_text(to_mmcif(structure))
        if instrumented:
            sources = [json.loads(x) if x else None for x in structure.residues[transport.FIELD]]
            assert any(s is None for s in sources), 'inserted residue has no source'
            selected.append(sources)
        tokenized = Tokenizer().tokenize(structure)
        tokenized.tokens['design_mask'] = parsed.design_info.res_design_mask[tokenized.token_to_res]
        data = Input(tokens=tokenized.tokens, bonds=tokenized.bonds,
                     token_to_res=tokenized.token_to_res, structure=structure, msa={}, templates=None)
        feat = Featurizer().process(data, random=np.random.default_rng(17), molecules=mols,
                                   training=False, max_seqs=1, design=True, atom14=False,
                                   compute_frames=False, disulfide_on=False)
        feat.update(id='inert', extra_mols={},
                    chain_design_mask=feat['design_mask'].bool())
        batch = collate([feat])
        # The only science stand-in is the prediction itself: use input coordinates
        # and native features unchanged, no network/checkpoint/model invocation.
        prediction = dict(batch)
        prediction['coords'] = batch['coords'][0]
        prediction['exception'] = False
        prediction.pop(transport.FEATURE, None)
        writer = DesignWriter(out / f'writer-{fuse}', res_atoms_only=False, atom14=False, design=True, write_native=True)
        writer.write_on_batch_end(prediction=prediction, batch=batch, sample_id='actual-event')
        assert writer.failed == 0
        assert (writer.outdir / 'actual-event_0.cif').exists()
        # Also exercise default atom14 conversion with inert coordinates. These
        # deliberately non-model coordinates can yield UNK for the inserted site;
        # its source must remain null, while real target residues retain theirs.
        feat14 = Featurizer().process(data, random=np.random.default_rng(17), molecules=mols,
                                     training=False, max_seqs=1, design=True, atom14=True,
                                     compute_frames=False, disulfide_on=False)
        feat14.update(id='atom14', extra_mols={}, chain_design_mask=feat14['design_mask'].bool())
        batch14 = collate([feat14])
        pred14 = dict(batch14, coords=batch14['coords'][0], exception=False)
        pred14.pop(transport.FEATURE, None)
        writer14 = DesignWriter(out / f'atom14-{fuse}', res_atoms_only=False, atom14=True)
        writer14.write_on_batch_end(prediction=pred14, batch=batch14, sample_id='atom14-event')
        assert writer14.failed == 0
        if instrumented:
            assert transport.mapping_for_bytes(out / 'events', (writer14.outdir / 'atom14-event_0.cif').read_bytes())
        # Exercise the installed subsequent-stage loader, not just re-parsing.
        import pickle
        from boltzgen.task.predict.data_from_generated import FromGeneratedDataset, collate as generated_collate
        mol_dir = work / 'molecules'
        mol_dir.mkdir(exist_ok=True)
        Chem.SetDefaultPickleProperties(Chem.PropertyPickleOptions.AllProps)
        with (mol_dir / 'GLY.pkl').open('wb') as handle:
            pickle.dump(mol, handle)
        extra_dir = work / 'extra-molecules'
        extra_dir.mkdir(exist_ok=True)
        dataset = FromGeneratedDataset([], [], [], mol_dir, mols, Tokenizer(), Featurizer(),
                                       design=True, inverse_fold=True, atom14=False, extra_mol_dir=extra_dir)
        next_feat = dataset.getitem_from_paths(writer.outdir / 'actual-event_0.npz',
                                              writer.outdir / 'actual-event_0.cif', None)
        next_batch = generated_collate([next_feat])
        assert not next_feat['exception']
        next_batch['extra_mols'] = [{}]
        next_prediction = dict(next_batch)
        next_prediction['coords'] = next_batch['coords'][0]
        next_prediction['exception'] = False
        next_prediction.pop(transport.FEATURE, None)
        inverse_writer = DesignWriter(out / f'inverse-{fuse}', res_atoms_only=False,
                                      atom14=False, inverse_fold=True, design=True)
        inverse_writer.write_on_batch_end(prediction=next_prediction, batch=next_batch,
                                          sample_id='derived-event')
        assert inverse_writer.failed == 0
        assert (inverse_writer.outdir / 'derived-event_0.cif').exists()
        if instrumented:
            assert transport.mapping_for_bytes(out / 'events', (inverse_writer.outdir / 'derived-event_0.cif').read_bytes())
        if instrumented:
            next_sources = [json.loads(v) for v in json.loads(next_batch[transport.FEATURE][0]).values() if v]
            assert {s['source_sha256'] for s in next_sources} == {s['source_sha256'] for s in sources if s}
        folding_batch = dict(next_batch, id=['inert'])
        folding_prediction = dict(next_prediction, id=['inert'])
        folding_prediction['exception'] = [False]
        folding_prediction['iptm'] = torch.tensor([0.5])
        folding_prediction['ptm'] = torch.tensor([0.5])
        folding_prediction['plddt'] = torch.ones((1, len(next_feat['res_type']))) * .8
        folding = FoldingWriter(out / f'folding-{fuse}')
        folding.write_on_batch_end(prediction=folding_prediction, batch=folding_batch)
        assert (folding.refold_cif_dir / 'inert.cif').exists()
        if instrumented:
            assert transport.mapping_for_bytes(out / 'events', (folding.refold_cif_dir / 'inert.cif').read_bytes())
        if instrumented:
            mapping = transport.mapping_for_bytes(out / 'events', (writer.outdir / 'actual-event_0.cif').read_bytes())
            assert mapping
            assert {m['source_sha256'] for m in mapping} == {s['source_sha256'] for s in sources if s}
            scaffold_map = next(m for m in mapping if m['source_sha256'] == transport.digest(scaffold.read_bytes()))
            assert {r['source']['auth_seq_id'] for r in scaffold_map['residues']} == {100, 108}
            assert {r['output']['chain_id'] for r in scaffold_map['residues']} == ({'A'} if fuse else {'B'})
            # Reload the emitted CIF via the actual native parser for a later stage.
            from boltzgen.data.parse.mmcif import parse_mmcif
            reloaded = parse_mmcif(writer.outdir / 'actual-event_0.cif', mols=mols, moldir=work)
            retained = [json.loads(x) for x in reloaded.data.residues[transport.FIELD] if x]
            assert {s['source_sha256'] for s in retained} == {m['source_sha256'] for m in mapping}
            converted = out / f'candidate-{fuse}.pdb'
            gemmi.read_structure(str(writer.outdir / 'actual-event_0.cif')).write_pdb(str(converted))
            transport.retain_converted(out / 'events', writer.outdir / 'actual-event_0.cif', converted, converted.stem)
            assert converted.with_suffix('.correspondence.json').exists()
    if instrumented:
        (out / 'selected.json').write_text(json.dumps(selected))
    print('native parser/schema/features/writer complete', instrumented)


def test_pinned_native_transport(tmp_path):
    import pytest
    image = os.environ.get('BMS_TEST_BOLTZGEN_IMAGE')
    if not image:
        pytest.skip('set BMS_TEST_BOLTZGEN_IMAGE for pinned installed inert-native test')
    for observed in ('original', 'observed'):
        command = ['apptainer', 'exec', '--cleanenv', '--no-home',
                   '--bind', f'{ROOT}:/work', '--bind', f'{tmp_path}:/probe',
                   '--env', 'TMPDIR=/probe', '--env', 'MPLCONFIGDIR=/probe/matplotlib',
                   '--env', 'NUMBA_CACHE_DIR=/probe/numba',
                   image, '/opt/venv/bin/python', '/work/tests/test_boltzgen_source_correspondence.py',
                   '/probe', observed]
        if observed == 'observed':
            # Exercise the production wrapper's actual subprocess startup path.
            native = '/opt/venv/bin/python /work/tests/test_boltzgen_source_correspondence.py /probe observed --output /probe/runtime'
            bootstrap = ("import sys; sys.path.insert(0, '/work/scripts'); "
                         "from run_boltzgen_wrapper import run_with_native_identity; "
                         f"code, identity = run_with_native_identity({native!r}); "
                         "assert code == 0, (code, identity)")
            command = command[:-3] + ['-c', bootstrap]
        result = subprocess.run(command, text=True, capture_output=True, timeout=180)
        assert result.returncode == 0, result.stdout + result.stderr
    original = {p.relative_to(tmp_path / 'original'): p.read_bytes()
                for p in (tmp_path / 'original').rglob('*.cif')}
    observed = {p.relative_to(tmp_path / 'observed'): p.read_bytes()
                for p in (tmp_path / 'observed').rglob('*.cif')}
    assert original and original == observed
    selected = json.loads((tmp_path / 'observed/selected.json').read_text())
    for sources in selected:
        selectors = [s['source'] for s in sources if s]
        assert any(s['insertion_code'] in ('B', 'C') for s in selectors)
        assert not any(s['auth_seq_id'] in (5, 8) for s in selectors)
        assert any(s is None for s in sources)
    for row in (tmp_path / 'observed/events').rglob('*.json'):
        event = json.loads(row.read_text())
        assert event['candidate_key'] in ('actual-event_0', 'derived-event_0', 'atom14-event_0', 'inert')
        assert any(r['source'] is None for r in event['residues'])
        for mapping in event['target_residue_mapping']:
            assert mapping['source_sha256'] != event['output_sha256']


def test_missing_and_conflicting_evidence_remain_absent(tmp_path):
    assert transport.mapping_for_bytes(tmp_path, b'x') is None
    row = {'schema': 'boltzgen.source-correspondence.v1', 'output_sha256': transport.digest(b'x'),
           'target_residue_mapping': [{'source_sha256': 'one', 'residues': []}]}
    bucket = tmp_path / transport.digest(b'x')
    bucket.mkdir()
    (bucket / 'a.json').write_text(json.dumps(row))
    row['target_residue_mapping'][0]['source_sha256'] = 'two'
    (bucket / 'b.json').write_text(json.dumps(row))
    assert transport.mapping_for_bytes(tmp_path, b'x') is None


def test_unknown_runtime_does_not_install_partial_hooks(tmp_path, monkeypatch):
    from types import SimpleNamespace
    package = tmp_path / 'boltzgen'
    (package / 'data').mkdir(parents=True)
    (package / 'data/data.py').write_text('# unsupported source')
    monkeypatch.setattr(transport.importlib.machinery.PathFinder, 'find_spec',
                        lambda *args: SimpleNamespace(origin=str(package / '__init__.py')))
    before = list(sys.meta_path)
    assert transport.install() is False
    assert sys.meta_path == before


def test_observer_setup_failure_preserves_native_invocation(monkeypatch):
    import tempfile
    import run_boltzgen_wrapper as wrapper
    import lib.boltzgen_native as native
    calls = []
    def unavailable(*args, **kwargs):
        raise OSError('observer scratch unavailable')
    monkeypatch.setattr(tempfile, 'TemporaryDirectory', unavailable)
    monkeypatch.setattr(native, 'observe_source', lambda: {'state': 'unavailable'})
    monkeypatch.setattr(wrapper.os, 'system', lambda command: calls.append(command) or 0)
    command = 'boltzgen run input.yaml --output output'
    code, _ = wrapper.run_with_native_identity(command)
    assert code == 0 and calls == [command]


def test_explicit_native_token_to_res_not_token_ordinal():
    token = transport._tokens.set({'23': 'source-23', '8': 'source-8'})
    try:
        assert transport.feature_source([0], [8, 23]) == 'source-8'
        assert transport.feature_source([1], [8, 23]) == 'source-23'
        assert transport.feature_source([0, 1], [8, 23]) == ''
        assert transport.feature_source([0], [999]) == ''
    finally:
        transport._tokens.reset(token)


if __name__ == '__main__':
    native_probe(sys.argv[1], sys.argv[2] == 'observed')
