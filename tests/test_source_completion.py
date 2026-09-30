"""Single-parse round source projection; the frozen caller is the equivalence oracle."""
import importlib
from pathlib import Path
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import run_binder_blind_pose as blind
import run_esmfold2_inference as native
from services.binder_round_inputs import source_components

def legacy_source_components(path, chain_ids, role):
    from paths import get_code_root
    scripts = str(get_code_root() / 'scripts')
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    from run_binder_blind_pose import _source
    path = Path(path)
    components = _source(path, chain_ids, role=role)
    # Retain author identity in the exact sequence extraction order, solely for
    # later comparison. Neither these rows nor coordinates enter prediction.
    rows = {chain: {} for chain in chain_ids}
    if path.suffix.lower() in {'.cif', '.mmcif'}:
        from Bio.PDB.MMCIF2Dict import MMCIF2Dict
        cif = MMCIF2Dict(str(path))
        count = len(cif.get('_atom_site.id', []))
        columns = {name: cif.get('_atom_site.' + name, [''] * count) for name in (
            'auth_asym_id', 'group_PDB', 'label_asym_id', 'label_seq_id', 'pdbx_PDB_ins_code', 'auth_seq_id')}
        def col(name):
            return columns[name]
        for i in range(count):
            chain = col('auth_asym_id')[i]
            if chain not in rows or col('group_PDB')[i] != 'ATOM':
                continue
            key = (col('label_asym_id')[i], col('label_seq_id')[i])
            insertion = col('pdbx_PDB_ins_code')[i]
            rows[chain].setdefault(key, {'chain_id': chain, 'residue_number': int(col('auth_seq_id')[i]),
                'insertion_code': '' if insertion in {'.', '?'} else insertion})
    else:
        from run_esmfold2_inference import _map_residue_to_letter
        for line in path.read_text().splitlines():
            if not line.startswith(('ATOM  ', 'HETATM')):
                continue
            chain, name = line[21:22].strip() or '_', line[17:20].strip().upper()
            if chain not in rows or not _map_residue_to_letter(name, 'protein'):
                continue
            key = (line[22:27].strip(), name)
            rows[chain].setdefault(key, {'chain_id': chain, 'residue_number': int(line[22:26]),
                                       'insertion_code': line[26:27].strip()})
    for component in components:
        residues = list(rows[component['id']].values())
        component['source_residues'] = residues if len(residues) == len(component['sequence']) else None
    return components


def atom(chain, number, insertion, name, *, record='ATOM  ', serial=1):
    return (f'{record}{serial:5d}  CA  {name:3s} {chain}{number:4d}{insertion:1s}   '
            '   1.000   2.000   3.000  1.00 30.00           C\n')


def pdb_document():
    # Encounter order, not sorted author numbering; repeated atoms/models retain
    # the native owner's historical deduplication. Modified proteins are retained,
    # water/nonprotein context is omitted, numeric and blank chain IDs stay exact.
    return ('MODEL        1\n' + atom('B', 42, 'A', 'GLY') + atom('B', 42, '', 'ALA')
            + atom('B', -2, '', 'MSE', record='HETATM') + atom('B', 99, '', 'HOH', record='HETATM')
            + atom('7', 8, 'B', 'TYR') + atom(' ', 5, '', 'SER')
            + 'ENDMDL\nMODEL        2\n' + atom('B', 42, 'A', 'GLY', serial=2) + 'ENDMDL\n')


def cif_document():
    fields = ('id group_PDB auth_asym_id label_asym_id auth_seq_id label_seq_id '
              'label_comp_id pdbx_PDB_model_num pdbx_PDB_ins_code').split()
    return ('data_source\nloop_\n' + ''.join('_atom_site.' + f + '\n' for f in fields)
            + '1 ATOM authorB labelZ 42 3 GLY 1 A\n'
            + '2 ATOM authorB labelZ 42 3 GLY 1 A\n'
            + '3 ATOM authorB labelZ 42 1 ALA 1 ?\n'
            + '4 HETATM authorB labelZ 99 . HOH 1 .\n'
            + '5 ATOM 7 labelA -2 2 TYR 1 B\n'
            + '6 ATOM other other 5 1 SER 2 .\n')


def outcome(fn, path, chains):
    try:
        return ('ok', fn(path, chains, 'binder'))
    except Exception as error:
        return (type(error).__name__, str(error))


@pytest.mark.parametrize('suffix', ['.pdb', '.cif', '.mmcif'])
def test_source_completion_exact_projection_and_single_parse(tmp_path, monkeypatch, suffix):
    path = tmp_path / ('source' + suffix)
    path.write_text(pdb_document() if suffix == '.pdb' else cif_document())
    chains = ['7', 'B', '_'] if suffix == '.pdb' else ['7', 'authorB']
    reads, parses = [], []
    read_text, parser = Path.read_text, blind.MMCIF2Dict
    cif_module = importlib.import_module('Bio.PDB.MMCIF2Dict')
    def counted_read(self, *args, **kwargs):
        if self == path:
            reads.append(str(self))
        return read_text(self, *args, **kwargs)
    def counted_parser(*args, **kwargs):
        parses.append(1)
        return parser(*args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', counted_read)
    monkeypatch.setattr(blind, 'MMCIF2Dict', counted_parser)
    monkeypatch.setattr(cif_module, 'MMCIF2Dict', counted_parser)
    old = legacy_source_components(path, chains, 'binder')
    assert len(reads) == (2 if suffix == '.pdb' else 1)
    assert len(parses) == (0 if suffix == '.pdb' else 2)
    reads.clear(); parses.clear()
    new = source_components(path, chains, 'binder')
    assert new == old
    assert len(reads) == 1
    assert len(parses) == (0 if suffix == '.pdb' else 1)
    assert [c['id'] for c in new] == chains
    component = new[1]
    assert component['sequence'] == ('GAM' if suffix == '.pdb' else 'GA')
    assert component['source_residues'][:2] == [
        {'chain_id': chains[1], 'residue_number': 42, 'insertion_code': 'A'},
        {'chain_id': chains[1], 'residue_number': 42, 'insertion_code': ''}]
    default = blind._source(path, chains, role='binder')
    assert default == [{k: v for k, v in c.items() if k != 'source_residues'} for c in new]


@pytest.mark.parametrize('case', ['empty_chains', 'duplicate_chains', 'missing_chain',
    'empty_document', 'unsupported_suffix', 'bad_author', 'bad_model',
    'multiple_instances', 'residue_conflict', 'missing_column', 'short_column'])
def test_source_completion_invalid_behavior_matches_old(tmp_path, case):
    path = tmp_path / ('source.pdb' if case == 'bad_author' else 'source.cif')
    document, chains = cif_document(), ['authorB']
    if case == 'empty_chains': chains = []
    elif case == 'duplicate_chains': chains *= 2
    elif case == 'missing_chain': chains = ['missing']
    elif case == 'empty_document': document = 'data_empty\n'
    elif case == 'unsupported_suffix': path = path.with_suffix('.txt')
    elif case == 'bad_author':
        document, chains = atom('B', 42, 'A', 'GLY').replace('  42A', 'oopsA'), ['B']
    elif case == 'bad_model': document = document.replace('GLY 1 A', 'GLY 2 A')
    elif case == 'multiple_instances': document = document.replace('labelZ 42 1', 'labelQ 42 1')
    elif case == 'residue_conflict': document = document.replace('2 ATOM authorB labelZ 42 3 GLY', '2 ATOM authorB labelZ 42 3 ALA')
    elif case == 'missing_column': document = document.replace('_atom_site.auth_seq_id', '_atom_site.missing_auth_seq_id')
    elif case == 'short_column': document = document.replace('_atom_site.pdbx_PDB_ins_code\n', '')
    path.write_text(document)
    old, new = outcome(legacy_source_components, path, chains), outcome(source_components, path, chains)
    assert old[0] != 'ok'
    assert new == old


def test_source_completion_unmatched_projection_remains_none(tmp_path, monkeypatch):
    path = tmp_path / 'source.pdb'
    path.write_text(atom('B', 42, 'A', 'ALA'))
    # A deliberately unequal native sequence/projection exercises the historical
    # null comparison fallback, not an admission rule or real sampling.
    monkeypatch.setitem(native.PROTEIN_3TO1, 'ALA', 'AA')
    old = legacy_source_components(path, ['B'], 'binder')
    assert source_components(path, ['B'], 'binder') == old
    assert old[0]['source_residues'] is None
