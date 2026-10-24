"""Exact author keys -> reversible native preparation; CPU transport, not sampling."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from scripts.resolve_redesign_regions import (
    ResidueKey, parse_author_ranges, parse_manual_ranges, parse_pdb_residues_from_lines,
    pick_interface_shell, residue_keys_from_indices, lower_structure_lines,
)


def specimen(path):
    keys = [('A', 10, ''), ('A', 12, ''), ('A', 12, 'A'), ('A', 12, 'B'), ('A', 20, ''),
            ('B', 5, ''), ('B', 5, 'A')]
    text = ''
    for model in (1, 2):
        text += f'MODEL     {model:4d}\n'
        for i, (chain, number, ic) in enumerate(keys):
            for j, atom in enumerate(('N', 'CA', 'C', 'O')):
                x = model * 100 + i * 5 + j
                text += f'ATOM  {i*4+j+1:5d} {atom:>4s} GLY {chain}{number:4d}{ic or " "}   {x:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{20:6.2f}          {atom[0]:>2s}\n'
        text += 'ENDMDL\n'
    path.write_text(text + 'END\n')
    return keys


def run_resolver(tmp_path, coordinate='A12A-12B', sequence='A12B', script=None):
    source = tmp_path/'original.pdb'
    specimen(source)
    seed, manifest = tmp_path/'seed.pdb', tmp_path/'region.json'
    cmd = [sys.executable, str(script or ROOT/'scripts/resolve_redesign_regions.py'),
           '--input_pdb', str(source), '--model_number', '2', '--design_chains', 'A',
           '--context_chains', 'B', '--redesign_ranges', coordinate,
           '--sequence_redesign_ranges', sequence, '--output_seed_pdb', str(seed),
           '--output_manifest', str(manifest)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    return source, seed, manifest, result


@pytest.mark.parametrize('token,key', [('A12', ResidueKey('A',12)), ('A12A',ResidueKey('A',12,'A')),
                                      ('A12B',ResidueKey('A',12,'B')), ('12B',ResidueKey('A',12,'B')),
                                      ('A:12B',ResidueKey('A',12,'B'))])
def test_author_singletons_are_not_integer_aliases(token, key):
    available = {ResidueKey('A',12,ic) for ic in ('','A','B')} | {ResidueKey('A',20)}
    assert parse_author_ranges(token,'A',available) == {key}


def test_range_gaps_and_reverse_endpoints():
    available = {ResidueKey('A',12,ic) for ic in ('','A','B')} | {ResidueKey('A',20)}
    assert parse_author_ranges('A12A-12B','A',available) == {ResidueKey('A',12,'A'), ResidueKey('A',12,'B')}
    assert parse_author_ranges('A20-12B','A',available) == {ResidueKey('A',12,'B'), ResidueKey('A',20)}
    assert parse_manual_ranges('A12-20','A',{12,20}) == {12,20}  # historical integer API


def test_nonfirst_model_native_seed_contigs_and_separate_masks(tmp_path):
    source,seed,path,result = run_resolver(tmp_path)
    assert result.returncode == 0, result.stderr
    manifest = json.loads(path.read_text())
    assert manifest['model_number'] == 2
    assert manifest['contig_spec'] == '[A1-5]'
    assert manifest['native_rfd3'] == {'select_fixed_atoms':{'A1-2,A5':'ALL','A3-4':[]}, 'select_unfixed_sequence':'A4'}
    assert manifest['movable_positions_spec'] == 'A3-4'
    assert manifest['fixed_positions_spec'] == 'A1-2,A5,B1-2'
    assert manifest['sequence_redesign_positions_spec'] == 'A4'
    assert manifest['sequence_fixed_positions_spec'] == 'A1-3,A5,B1-2'
    assert manifest['movable_residue_count'] == 2
    assert manifest['fixed_residue_count'] == 5
    assert manifest['source_regions']['movable'] == ['A12A','A12B']
    assert manifest['source_regions']['sequence_redesign'] == ['A12B']
    mapping = manifest['source_mapping']
    assert mapping['original_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert mapping['model'] == '2'
    atoms = [line for line in seed.read_text().splitlines() if line.startswith('ATOM')]
    assert len(atoms) == 20
    assert all(line[26] == ' ' and float(line[30:38]) >= 200 for line in atoms)
    source_model2 = source.read_text().split('MODEL        2\n')[1].split('ENDMDL')[0].splitlines(True)
    lowered = lower_structure_lines(source_model2,mapping)
    for original, derived in zip(source_model2, lowered):
        assert original[:21] == derived[:21] and original[27:] == derived[27:]
    # Save only when explicitly requested by an evidence run; normal tests are disposable.
    if os.environ.get('BMS_INSERTION_EVIDENCE'):
        import shutil
        out=Path(os.environ['BMS_INSERTION_EVIDENCE']);out.mkdir(exist_ok=True,parents=True)
        for file in (source,seed,path,path.with_suffix('.native.cif')):shutil.copy2(file,out/file.name)


def test_preselected_single_model_pdb_keeps_original_model_serial(tmp_path):
    source=tmp_path/'selected.pdb'
    source.write_text('ATOM      1  CA AGLY A  12B      9.000   2.000   3.000  0.50 20.00           C  \nEND\n')
    manifest=tmp_path/'region.json';seed=tmp_path/'seed.pdb'
    subprocess.run([sys.executable,str(ROOT/'scripts/resolve_redesign_regions.py'),
        '--input_pdb',str(source),'--model_number','2','--design_chains','A',
        '--redesign_ranges','A12B','--output_seed_pdb',str(seed),'--output_manifest',str(manifest)],check=True)
    result=json.loads(manifest.read_text())
    assert result['model_number']==2
    assert result['source_mapping']['model']=='1'  # selected derivative PDB, not original CIF
    atom=result['source_mapping']['atoms'][0]
    assert atom['source']['insertion_code']=='B' and atom['source']['altloc']=='A'
    assert seed.read_text().splitlines()[0][16]=='A'
    assert float(seed.read_text().splitlines()[0][30:38])==9.


def test_sequence_subset_compares_complete_author_keys(tmp_path):
    _,_,_,result = run_resolver(tmp_path,'A12B','A12A')
    assert result.returncode != 0
    assert 'subset of coordinate-edit' in result.stderr


def test_interface_shell_does_not_expand_number_aliases(tmp_path):
    source=tmp_path/'original.pdb';specimen(source)
    from scripts.resolve_redesign_regions import select_structure_lines
    lines,_=select_structure_lines(source,2)
    residues=parse_pdb_residues_from_lines(lines)['A']
    indices=pick_interface_shell(residues,[residues[3]],cutoff=.01,padding=0)
    assert residue_keys_from_indices(residues,indices)=={ResidueKey('A',12,'B')}


def test_legacy_integer_request_seed_and_manifest_bytes_unchanged(tmp_path):
    """Compare actual current writer with pinned historical script, not a mock."""
    source=tmp_path/'plain.pdb'
    source.write_text(''.join(f'ATOM  {i:5d}   CA GLY A{i:4d}    {float(i):8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{20:6.2f}           C\n' for i in (1,2,5))+'END\n')
    baseline=tmp_path/'baseline.py'
    baseline.write_bytes(subprocess.check_output(['git','show','625ebdda840a76f70de7050260fab2082dbd2f99:scripts/resolve_redesign_regions.py'],cwd=ROOT))
    seed=tmp_path/'seed.pdb';manifest=tmp_path/'region.json'
    args=['--input_pdb',str(source),'--design_chains','A','--redesign_ranges','A1-2',
          '--sequence_redesign_ranges','A2','--output_seed_pdb',str(seed),'--output_manifest',str(manifest)]
    subprocess.run([sys.executable,str(baseline),*args],check=True)
    expected=(seed.read_bytes(),manifest.read_bytes())
    subprocess.run([sys.executable,str(ROOT/'scripts/resolve_redesign_regions.py'),*args],check=True)
    assert (seed.read_bytes(),manifest.read_bytes())==expected


def test_actual_receiving_policy_keeps_author_siblings_separate(tmp_path):
    from services.fampnn_policy_admission import _local_region
    if 'parse_author_ranges' not in (ROOT/'platform/api/services/fampnn_policy_admission.py').read_text():
        pytest.skip('parent-owned shared admission patch not applied in this leaf')
    source=tmp_path/'original.pdb';specimen(source)
    params=dict(input_pdb=str(source),model_number=2,design_chains='A',context_chains='B',
        region_mode='manual_ranges',redesign_ranges='A12A-12B',sequence_redesign_ranges='A12B')
    domain,authorized=_local_region(params)
    assert authorized==['A:12:B']
    assert domain==['A:10:','A:12:','A:12:A','A:12:B','A:20:','B:5:','B:5:A']


def test_actual_merge_and_policy_correspondence(tmp_path):
    """Requires the parent-owned applicable shared patch in the integration tree."""
    if 'local_redesign_source_mapping' not in (ROOT/'scripts/fampnn_policy_resolution.py').read_text():
        pytest.skip('parent-owned shared integration patch not applied in this leaf')
    source,seed,manifest,result=run_resolver(tmp_path)
    assert result.returncode==0,result.stderr
    incoming=tmp_path/'incoming';incoming.mkdir()
    # Explicit transport fixture: not native sampled coordinates.
    (incoming/'fixture.pdb').write_bytes(seed.read_bytes())
    (incoming/'fixture.json').write_text('{}')
    out=tmp_path/'merged'
    subprocess.run([sys.executable,str(ROOT/'scripts/merge_redesigned_complexes.py'),
                    '--input-dir',str(incoming),'--complex-pdb',str(source),'--manifest',str(manifest),'--output-dir',str(out)],check=True)
    merged=out/'fixture.pdb'
    rows=parse_pdb_residues_from_lines(merged.read_text().splitlines())
    assert [r.key for r in rows['B']]==[ResidueKey('B',1),ResidueKey('B',2)]
    assert [r.atom_coords for r in rows['B']]==[[(225.+j,0.,0.) for j in range(4)],[(230.+j,0.,0.) for j in range(4)]]
    sys.path.insert(0,str(ROOT/'scripts'))
    from fampnn_policy_resolution import prep_receipt, resolve_declaration
    domain=[f'{r.key.chain}:{r.key.resnum}:{r.key.icode}' for group in rows.values() for r in group]
    receipt=prep_receipt(merged,merged,[(v,v) for v in domain]) # actual identity-publisher, no PyRosetta claim
    author_domain=['A:10:','A:12:','A:12:A','A:12:B','A:20:','B:5:','B:5:A']
    declaration=dict(schema_version=1,owner='protein_local_redesign',version=1,
        declaration='sequence_redesign_positions_spec',input_domain=author_domain,sequence_design=['A:12:B'],
        summary=['A:12:B'],fixed=[v for v in author_domain if v!='A:12:B'],summary_override=None,
        mutation_override=None,allow_summary_override=True,require_full_coverage=False)
    policy=resolve_declaration(declaration,{'fixture':receipt},out)
    assert policy['inputs']['fixture']['sequence_design']==['A:4:']
    assert policy['inputs']['fixture']['mutation_override']==['A:4:']
    # Real downstream constraint and ProteinMPNN role writers, not fabricated masks.
    csv=tmp_path/'fampnn.csv'
    subprocess.run([sys.executable,str(ROOT/'scripts/prep_fampnn_constraints_from_spec.py'),
                    '--input_dir',str(out),'--manifest',str(manifest),'--out_csv',str(csv),'--fix_sidechains'],check=True)
    assert 'A1-3,A5,B1-2' in csv.read_text()
    from add_fixed_labels_from_spec import parse_position_spec, add_labels
    labeled=tmp_path/'labeled.pdb'
    add_labels(merged,labeled,parse_position_spec('A4'))
    remark=json.loads(labeled.read_text().splitlines()[0].split(' ',2)[2])
    assert ['A',4,''] not in remark['fixed_positions']
    assert len(remark['fixed_positions'])==6
    if os.environ.get('BMS_INSERTION_EVIDENCE'):
        import shutil
        evidence=Path(os.environ['BMS_INSERTION_EVIDENCE'])
        for file in (merged,merged.with_suffix('.json'),csv,labeled):shutil.copy2(file,evidence/file.name)
        (evidence/'resolved-policy.json').write_text(json.dumps(policy,indent=2))
