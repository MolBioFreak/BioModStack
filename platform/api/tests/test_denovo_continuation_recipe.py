"""Offline atom materialization -> ordered recipe -> native converter acceptance."""
import io
import json
import os
from pathlib import Path

from Bio.PDB import MMCIFIO
from services import binder_source_materialization as sources
from scripts.protenix_chemistry import validate_chemistry
from scripts.prepare_protenix_msa import build_native_protenix_input
from test_binder_source_native_identity import specimen


def emitted_recipes(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'to_allowed_relative', lambda p: 'inputs/retained/' + Path(p).name)
    results = {}
    for name, residues in [('simple', ['ALA', 'GLY']), ('modified', ['ALA', 'SEP', 'GLY'])]:
        raw = 'MODEL        2\n' + ''.join(f'ATOM  {i:5d}  CA  {res} A{i:4d}    {i:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           C  \n' for i, res in enumerate(residues, 1)) + 'TER\nENDMDL\nEND\n'
        dest = tmp_path / name; dest.mkdir()
        results[name] = sources.materialize_source_bytes(raw.encode(), '.pdb', dest, output_format='native', model_number=2)
    _, columns = specimen()
    columns['_atom_site.pdbx_PDB_model_num'] = ['1' if x == '3' else '2' for x in columns['_atom_site.pdbx_PDB_model_num']]
    for key in columns:
        if key.endswith('.model_num'):
            columns[key] = ['1' if x == '3' else '2' for x in columns[key]]
    columns.update({'_struct_conn.id': ['bond1'], '_struct_conn.conn_type_id': ['covale'],
                    '_struct_conn.ptnr1_label_asym_id': ['A'], '_struct_conn.ptnr1_label_seq_id': ['2'], '_struct_conn.ptnr1_label_atom_id': ['CA'],
                    '_struct_conn.ptnr2_label_asym_id': ['D'], '_struct_conn.ptnr2_label_seq_id': ['1'], '_struct_conn.ptnr2_label_atom_id': ['CA']})
    writer = MMCIFIO(); writer.set_dict(columns); text = io.StringIO(); writer.save(text)
    dest = tmp_path / 'mixed'; dest.mkdir()
    results['mixed'] = sources.materialize_source_bytes(text.getvalue().encode(), '.cif', dest, output_format='native', model_number=2)
    for name, names, chains, models in [
        ('legacyMulti', ['ALA', 'GLY', 'MET', 'LYS', 'DA'], ['A', 'A', 'authorB', 'authorB', 'DNA'], ['1', '1', '2', '2', '2']),
        ('legacyAuthor', ['ALA', 'GLY', 'DA'], ['AA', 'AA', 'DNA'], ['2', '2', '2']),
    ]:
        count = len(names)
        cols = {k: [v[0]] * count for k, v in columns.items() if k.startswith('_atom_site.')}
        cols.update({'data_': name, '_atom_site.id': [str(i + 1) for i in range(count)],
                     '_atom_site.label_comp_id': names, '_atom_site.auth_asym_id': chains,
                     '_atom_site.label_asym_id': chains, '_atom_site.pdbx_PDB_model_num': models,
                     '_atom_site.label_seq_id': ['1', '2', '1', '2', '1'][:count],
                     '_atom_site.auth_seq_id': ['1', '2', '9', '10', '1'] if name == 'legacyMulti' else ['1', '1', '1'],
                     '_atom_site.pdbx_PDB_ins_code': ['?'] * count if name == 'legacyMulti' else ['?', 'B', '?'],
                     '_atom_site.label_entity_id': ['2' if c == 'DNA' else '1' for c in chains],
                     '_entity_poly.entity_id': ['1', '2'], '_entity_poly.type': ['polypeptide(L)', 'polydeoxyribonucleotide']})
        writer.set_dict(cols); text = io.StringIO(); writer.save(text)
        dest = tmp_path / name; dest.mkdir()
        results[name] = sources.materialize_source_bytes(text.getvalue().encode(), '.cif', dest, output_format='native')
    return results


def test_emitted_recipe_and_native_converter(tmp_path, monkeypatch):
    results = emitted_recipes(tmp_path, monkeypatch)
    assert results['simple']['native_recipe']['complex_components'][0]['sequence'] == 'AG'
    modified = results['modified']['native_recipe']['complex_components'][0]
    assert modified['sequence'] == 'ASG'
    assert modified['modifications'] == [{'ptmPosition': 2, 'ptmType': 'CCD_SEP'}]
    recipe = results['mixed']['native_recipe']
    assert [c['type'] for c in recipe['complex_components']] == ['protein', 'ligand', 'dna', 'rna']
    assert recipe['complex_components'][0]['sequence'] == 'ASGMPX'
    assert recipe['unresolved'][0]['component_id'] == 'ZZZ'
    assert recipe['entries'][0]['positions'][1]['auth_seq_id'] == '43'
    assert recipe['entries'][0]['positions'][1]['pdbx_PDB_ins_code'] == 'B'
    assert recipe['covalent_bonds'] == [dict(left_entity=1, left_position=2, left_copy=1, left_atom='CA', right_entity=3, right_position=1, right_copy=1, right_atom='CA')]
    params = {k: recipe[k] for k in ('complex_components', 'covalent_bonds')}
    validate_chemistry(params)
    native = build_native_protenix_input(seeds=[0], complexes=[('selected', {'components': params['complex_components'], 'covalent_bonds': params['covalent_bonds']})])[0]
    assert native['covalent_bonds'] == params['covalent_bonds']
    assert native['sequences'][0]['proteinChain']['modifications'] == modified['modifications'] + [{'ptmPosition': 4, 'ptmType': 'CCD_MSE'}, {'ptmPosition': 5, 'ptmType': 'CCD_HYP'}]
    target = os.environ.get('BMS_CONTINUATION_FIXTURE_OUTPUT')
    if target:
        Path(target).write_text(json.dumps(results, indent=2) + '\n')
    checked = Path(__file__).parents[2] / 'frontend/tests/vitest/fixtures/deNovoContinuationRecipes.json'
    assert json.loads(checked.read_text()) == {k: v['native_recipe'] for k, v in results.items()}


def test_recipe_enrichment_failure_does_not_refuse_native_bytes(tmp_path, monkeypatch):
    from services import protenix_chemistry
    monkeypatch.setattr(sources, 'to_allowed_relative', str)
    def unavailable(*args):
        raise ValueError('unresolved chemistry fixture')
    monkeypatch.setattr(protenix_chemistry, 'recipe_from_source_positions', unavailable)
    raw, _ = specimen()
    result = sources.materialize_source_bytes(raw, '.cif', tmp_path, output_format='native')
    assert Path(result['path']).read_bytes() == raw
    assert result['native_recipe']['unresolved'][0] == {'inspection_error': 'unresolved chemistry fixture'}
    assert result['native_recipe']['unresolved'][1:] == result['source_positions']


def test_pdb_nucleic_polymers_are_not_protein_unknowns(tmp_path, monkeypatch):
    monkeypatch.setattr(sources, 'to_allowed_relative', str)
    raw = ''.join(f'ATOM  {i:5d}  P   {res:>3} {chain}{1:4d}    {i:8.3f}{0:8.3f}{0:8.3f}  1.00 20.00           P  \n' for i, (res, chain) in enumerate([('DA', 'D'), ('U', 'R')], 1)) + 'END\n'
    result = sources.materialize_source_bytes(raw.encode(), '.pdb', tmp_path, output_format='native')
    assert result['native_recipe']['complex_components'] == [dict(id='D', type='dna', count=1, sequence='A'), dict(id='R', type='rna', count=1, sequence='U')]


def test_unknown_and_ambiguous_observations_remain_evidence():
    from services.protenix_chemistry import recipe_from_source_positions
    positions = [dict(model_number=2, label_asym_id='A', auth_asym_id='author', label_seq_id=str(i),
                      component_id=code, polymer_membership=True, canonical_amino_acid=letter)
                 for i, (code, letter) in enumerate([('ALA', 'A'), ('ZZZ', None), ('GLY', 'G')], 1)]
    bond = dict(conn_type_id='covale', ptnr1_label_asym_id='A', ptnr2_label_asym_id='A', ptnr1_label_atom_id='CA', ptnr2_label_atom_id='CA')
    result = recipe_from_source_positions(positions, {'struct_conn': [bond]})
    assert result['complex_components'] == [dict(id='A', type='protein', count=1, sequence='AXG')]
    assert result['covalent_bonds'] == []
    assert result['unresolved'] == [positions[1], {'bond': bond, 'model_number': 2}]
    assert result['entries'][0]['positions'] == positions


def test_replay_actual_mounted_native_request(tmp_path, monkeypatch):
    """Set BMS_CONTINUATION_WIRE to the captured Axios POST, never synthetic wire."""
    import pytest
    target = os.environ.get('BMS_CONTINUATION_WIRE')
    if not target:
        pytest.skip('Mounted wire replay is supplied by the offline qualification run')
    from schemas import JobCreate
    from services import nextflow
    from scripts.prepare_protenix_msa import load_native_protenix_input
    import paths
    managed = tmp_path / 'inputs'; (managed / 'structure_prediction').mkdir(parents=True)
    ligand_bytes = b'HETATM    1  C1  LIG L   1       0.000   0.000   0.000  1.00 20.00           C  \nEND\n'
    (managed / 'structure_prediction/ligand.pdb').write_bytes(ligand_bytes)
    monkeypatch.setattr(paths, 'get_allowed_roots', lambda: {'inputs': managed})
    request = JobCreate.model_validate_json(Path(target).read_text())
    assert request.model_id == 'protenix'
    assert request.source_structure.model_number == 2
    monkeypatch.setattr(nextflow, 'resolve_nextflow_executable', lambda: 'nextflow')
    monkeypatch.setattr('services.gpu_config.read_scheduler_config', lambda: {})
    monkeypatch.setattr('services.msa_server.read_server_settings', lambda: {})
    for batch in (False, True):
        from routers.jobs import _normalize_frustrampnn_settings
        params = _normalize_frustrampnn_settings(request.model_id, request.mode, request.params)
        # Compiler-only scheduler placement fixture; no GPU query or execution.
        params['gpu_id'] = 0
        if batch:
            params['sequence_batch_entries'] = [{'name': 'variant', 'sequence': params['sequence']}]
            params['sequence_batch_component_id'] = params['primary_chain_id']
        out = tmp_path / str(batch)
        invocation = nextflow.compile_nextflow_invocation('protenix', 'complex', params, str(out))
        invocation.materialize_inputs(out)
        tasks = load_native_protenix_input(invocation.native_parameters)
        assert tasks[0]['covalent_bonds'] == params['covalent_bonds']
        assert tasks[0]['sequences'][0]['proteinChain']['modifications'] == params['complex_components'][0]['modifications']
        assert len(tasks[0]['sequences']) == len(params['complex_components'])
        assert tasks[0]['sequences'][0]['proteinChain']['id'] == ['copyA', 'copyB']
        assert tasks[0]['sequences'][0]['proteinChain']['unpairedMsa'] == '>query\nASGMPX\n'
        from services.msa_preparation import export_protenix_inputs, materialize_protenix_inputs
        bundle = tmp_path / f'msa-{batch}'
        export_protenix_inputs(tasks, bundle, {}, {'backend': 'supplied'})
        received = json.loads(materialize_protenix_inputs(bundle, tmp_path / f'msa-{batch}.json', {}).read_text())
        assert received == tasks
        from scripts.lib.portable_inputs import discover_native_input_references, ENV, SCHEMA
        refs = discover_native_input_references('protenix', 'complex', invocation.native_parameters, invocation.generated_inputs, output_dir=out, allowed_roots=[tmp_path])
        reference = next(r for r in refs if r['role'] == 'ligand')
        source = Path(tasks[0]['sequences'][1]['ligand']['ligand'].removeprefix('FILE_'))
        assert source.read_bytes() == ligand_bytes
        worker = tmp_path / f'worker-{batch}'; worker.mkdir()
        destination = worker / 'ligand.pdb'; destination.write_bytes(source.read_bytes())
        binding = worker / 'bindings.json'
        binding.write_text(json.dumps({'schema': SCHEMA, 'roots': [str(worker)], 'bindings': [{'reference': reference, 'path': str(destination)}]}))
        source.unlink()
        monkeypatch.setenv(ENV, str(binding))
        rebound = load_native_protenix_input(invocation.native_parameters)
        assert rebound[0]['sequences'][1]['ligand']['ligand'] == 'FILE_' + str(destination)
        assert rebound[0]['sequences'][0]['proteinChain'] == tasks[0]['sequences'][0]['proteinChain']
        monkeypatch.delenv(ENV)
