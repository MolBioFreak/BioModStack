"""Execute sealed native parser + PDB/PKL export code without model engines."""
import ast
import dataclasses
import json
import os
import pickle
import sys
from pathlib import Path
from types import SimpleNamespace
import __future__

import numpy as np
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
import fampnn_native_binding as native
from test_g09_native_transport import native_constants, atom
from test_fampnn_closeout import cli
from test_fampnn_strict_contract import DIALECT


def source_root():
    root = Path(os.environ['BMS_G09_FAMPNN_SOURCE'])
    assert root.is_dir(), 'Existing sealed FA source required; do not download models'
    return root


def definitions(relative, names, scope):
    text = native.instrument_source(relative, (source_root()/relative).read_bytes())
    tree = ast.parse(text)
    nodes = [n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
    assert {n.name for n in nodes} == set(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), relative, 'exec',
        flags=__future__.annotations.compiler_flag), scope)
    return scope


class Tensor(np.ndarray):
    """CPU tensor protocol double, never a model output or identity resolver."""
    def cpu(self): return self
    def detach(self): return self
    def numpy(self): return np.asarray(self)
    @property
    def device(self): return 'cpu'


def tensor(value, **kwargs):
    return np.asarray(value).view(Tensor)


def test_sealed_probability_hooks_compile_and_reject_changed_source():
    for relative in native.SOURCE_SHA256['fampnn']:
        data = (source_root()/relative).read_bytes()
        compile(native.instrument_source(relative, data), relative, 'exec')
        with pytest.raises(ValueError, match='source identity'):
            native.instrument_source(relative, data + b'\n')


def export_fixture(tmp_path, same_input=False):
    from Bio.PDB import PDBParser, MMCIFParser, Structure
    from Bio.PDB.Atom import DisorderedAtom
    rc = native_constants()
    scope = dict(np=np, dataclasses=dataclasses, _bms=native, residue_constants=rc, Path=Path,
        PDBParser=PDBParser, MMCIFParser=MMCIFParser, Structure=Structure, DisorderedAtom=DisorderedAtom,
        PDB_CHAIN_IDS='ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789', PDB_MAX_CHAINS=62,
        __name__=__name__)
    definitions('fampnn/data/protein.py', ['Protein', 'read_pdb', 'to_pdb', '_chain_end', 'are_atoms_bonded'], scope)
    pdb_batch_files = [tmp_path/'first.pdb', tmp_path/'second.pdb']
    pdb_batch_files[0].write_text(atom('Z', 100, 'A') + atom('Z', 101, serial=2) + atom('B', 8, serial=3))
    pdb_batch_files[1].write_text(atom('T', 9) + atom('H', 3, serial=2))
    if same_input:
        pdb_batch_files[1].unlink()
        pdb_batch_files[1] = pdb_batch_files[0]
    parsed = [scope['read_pdb'](str(path))[0] for path in pdb_batch_files]
    inputs = [native.capture_input(p, vars(data)) for p, data in zip(pdb_batch_files, parsed)]
    # Execute the actual native save_samples_to_pdb and write_batched/to_pdb.
    # All fixture atoms are CA, supported by every token; irrelevant training
    # atom-mask table is a physical-data seam, not an identity replacement.
    rc.STANDARD_ATOM_MASK_WITH_X = np.ones((21, 37), dtype=int)
    rc.non_bb_idxs = [i for i in range(37) if i not in [0, 1, 2, 4]]
    torch = SimpleNamespace(Tensor=Tensor, float32=np.float32, tensor=tensor,
        zeros_like=lambda a, **kw: tensor(np.zeros_like(a, **kw)),
        unique=lambda a: tensor(np.unique(a)), sort=lambda a: SimpleNamespace(values=tensor(np.sort(a))))
    writer = dict(torch=torch, rc=rc, protein=SimpleNamespace(**scope), _bms=native, PDB_CHAIN_IDS=scope['PDB_CHAIN_IDS'])
    definitions('fampnn/data/pdb_utils.py', ['write_to_pdb', 'write_batched_to_pdb'], writer)
    definitions('fampnn/model/sd_model.py', ['save_samples_to_pdb'], writer)
    n = 3
    def batch(key, fill=0):
        arrays = []
        for prot in parsed:
            arr = getattr(prot, key)
            padded = np.full((n,)+arr.shape[1:], fill, dtype=arr.dtype)
            padded[:len(arr)] = arr
            arrays.append(padded)
        return tensor(arrays)
    samples = dict(x_denoised=batch('atom_positions'), residue_index=batch('residue_index'),
        chain_index=batch('chain_index'), seq_mask=tensor([[1,1,1],[1,1,0]]),
        pred_aatype=tensor([[0,20,1],[2,3,0]]), missing_atom_mask=1-batch('atom_mask'),
        psce=tensor(np.zeros((2,n,33))), aatype_override_mask=tensor([[1,0,0],[0,0,0]]),
        seq_probs=tensor(np.eye(21)[[[0,20,1],[2,3,0]]]))
    if same_input:
        samples['seq_mask'][1] = 1
    samples['seq_probs'][0,0] = 0  # fixed zero-total evidence must stay unavailable
    for folder in ['samples','pkls','fastas']:
        (tmp_path/folder).mkdir()
    # Execute native batch export statements, including the real PKL crop and
    # close-before-capture hook. No reimplementation of path/j association.
    text = native.instrument_source('fampnn/inference/seq_design.py',
        (source_root()/'fampnn/inference/seq_design.py').read_bytes())
    import textwrap
    block = text.split('        # Save outputs\n', 1)[1].split('        pbar.update(B)', 1)[0]
    env = dict(_bms=native, cfg=SimpleNamespace(num_seqs_per_pdb=2 if same_input else 1), i=0, B=2,
        pdb_names=[p.stem for p in pdb_batch_files], pdb_batch_files=pdb_batch_files,
        sample_out_dir=str(tmp_path/'samples'), fasta_out_dir=str(tmp_path/'fastas'),
        sample_pkl_dir=str(tmp_path/'pkls'), samples=samples, bms_inputs=inputs,
        SeqDenoiser=SimpleNamespace(save_samples_to_pdb=writer['save_samples_to_pdb']),
        rc=SimpleNamespace(restypes_with_x='ARNDCQEGHILKMFPSTWYVX'), pickle=pickle)
    # Native sequence extraction calls bool() on its CPU mask.
    Tensor.bool = lambda self: tensor(self.astype(bool))
    exec(compile(textwrap.dedent(block), 'native_seq_design_export', 'exec'), env)
    policy = dict(schema_version=1, owner='protein_design', version=1,
        declaration='declared_protein_inputs', dialect=DIALECT,
        require_full_coverage=False, allow_summary_override=True, inputs={})
    for path, context in zip(pdb_batch_files, inputs):
        ids = list(context['mapping'].values())
        policy['inputs'][path.stem] = dict(input_domain=ids, sequence_design=ids, summary=ids,
            summary_override=ids[1:], mutation_override=[], artifact_binding=dict(
                producer_input_id=path.stem, source_pdb_sha256=context['source_pdb_sha256']))
    from fampnn_policy_resolution import bind_native_candidates
    return bind_native_candidates(policy, tmp_path/'samples')


def test_native_parser_export_policy_analyzer_roundtrip(tmp_path):
    policy = export_fixture(tmp_path)
    result = cli(tmp_path, policy)
    assert result.returncode == 0, result.stderr
    rows = [json.loads(line) for line in (tmp_path/'out.jsonl').read_text().splitlines()]
    assert len(rows) == 2
    first, second = rows
    assert first['native_export']['records'][0]['source'] == 'Z:100:A'
    assert first['native_export']['records'][0]['candidate'] == 'A:101:'
    assert first['residue_evidence'][0]['scored'] is False
    assert first['residue_evidence'][1]['aa'] == 'X'
    assert first['selected_count'] == 2 and first['scored_selected_count'] == 2
    assert first['resolved_mutation_membership'] == []
    assert second['total_residue_count'] == 2  # native padding crop, not length matching
    assert second['native_export']['input_id'] == 'second'
    assert second['residue_evidence'][0]['identity'] == 'T:9:'


@pytest.mark.parametrize('required', [False, True])
def test_native_missing_declared_sample_with_surviving_sibling(tmp_path, required):
    policy = export_fixture(tmp_path)
    policy['require_full_coverage'] = required
    (tmp_path/'pkls/second_sample0.pkl').unlink()
    result = cli(tmp_path, policy)
    if required:
        assert result.returncode != 0 and 'coverage' in result.stderr
        assert not (tmp_path/'out.jsonl').exists()
    else:
        assert result.returncode == 0, result.stderr
        rows = [json.loads(line) for line in (tmp_path/'out.jsonl').read_text().splitlines()]
        assert len(rows) == 2
        assert rows[1]['seq_probs_reason'] == 'missing_declared_sample_pkl'
        assert rows[1]['present_count'] is None


@pytest.mark.parametrize('artifact', ['samples/first_sample0.pdb', 'pkls/first_sample0.pkl'])
@pytest.mark.parametrize('same_input', [False, True])
def test_native_foreign_same_run_artifact_fails(tmp_path, artifact, same_input):
    policy = export_fixture(tmp_path, same_input=same_input)
    positive = cli(tmp_path, policy)
    assert positive.returncode == 0, positive.stderr
    path = tmp_path/artifact
    foreign = artifact.replace('sample0', 'sample1') if same_input else artifact.replace('first_', 'second_')
    path.write_bytes((tmp_path/foreign).read_bytes())
    result = cli(tmp_path, policy)
    assert result.returncode != 0 and 'binding' in result.stderr


@pytest.mark.parametrize('defect', ['undeclared_pkl', 'missing_receipt', 'missing_candidate', 'foreign_input', 'foreign_axis'])
def test_native_inventory_and_receipt_validation(tmp_path, defect):
    policy = export_fixture(tmp_path)
    candidate = tmp_path/'samples/first_sample0.pdb'
    sidecar = native.receipt_path(candidate)
    if defect == 'undeclared_pkl':
        (tmp_path/'pkls/foreign_sample0.pkl').write_bytes((tmp_path/'pkls/first_sample0.pkl').read_bytes())
    elif defect == 'missing_receipt':
        sidecar.unlink()
    elif defect == 'missing_candidate':
        candidate.unlink()
    else:
        receipt = json.loads(sidecar.read_text())
        if defect == 'foreign_input':
            receipt['input_id'] = 'second'
        else:
            receipt['records'][0]['source'] = 'foreign:1:'
        sidecar.write_text(json.dumps(receipt))
    result = cli(tmp_path, policy)
    assert result.returncode != 0 and 'binding' in result.stderr
