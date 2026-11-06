"""Bounded native evaluator protocols; no weights, inference or network required.

Reuse COMPLEXA_TEST_IMAGE and run_image from the existing image qualification.
COMPLEXA_TEST_PDB supplies a small protein-only PDB (qualification used 1UBQ
without HETATM waters). Databases below are explicitly private test databases,
not substitutes for a selected novelty corpus.
"""
import os
from pathlib import Path
import shutil

import pytest

from test_complexa_runtime_packaging import image, run_image
from scripts.bms_complexa.colabfold_cli import native_data_args


@pytest.mark.parametrize('data,env,expected', [
    ('/hf', {'CACHE_DIR': '/hf', 'AF2_DIR': '/weights/alphafold/params'}, '/weights/alphafold'),
    ('None', {'AF2_DIR': '/weights/alphafold/params'}, '/weights/alphafold'),
    ('/hf', {'CACHE_DIR': '/hf', 'COMPLEXA_COLABFOLD_DATA': '/custom data'}, '/custom data'),
    ('/explicit', {'CACHE_DIR': '/hf', 'AF2_DIR': '/weights/alphafold/params'}, '/explicit'),
    ('/hf', {'CACHE_DIR': '/hf'}, '/hf'),
    ('/hf', {'CACHE_DIR': '/hf', 'AF2_DIR': '/unrecognized-layout'}, '/hf'),
])
def test_only_native_data_argument_is_translated(data, env, expected):
    args = ['query', 'out', '--msa-mode', 'single_sequence', '--data', data,
            '--num-relax', '1', '--use-gpu-relax']
    original = list(args)
    actual = native_data_args(args, env)
    assert actual == original[:5] + [expected] + original[6:]
    assert args == original


@pytest.fixture
def protein(tmp_path):
    source = os.environ.get('COMPLEXA_TEST_PDB')
    if not source:
        pytest.skip('COMPLEXA_TEST_PDB is required for native scientific-tool protocols')
    shutil.copyfile(source, tmp_path / 'protein.pdb')
    return '/qualification/protein.pdb'


def test_native_colabfold_single_sequence_protocol(image, tmp_path):
    # Exercise the actual native caller, adding only --msa-only to bound this
    # test below model inference. There is deliberately no fabricated PDB result.
    code = r'''
import json, os, subprocess
from pathlib import Path
import tensorflow
from colabfold.alphafold.models import load_models_and_params
from proteinfoundation.metrics import folding_models
os.environ['CACHE_DIR']='/qualification/hf'
os.environ['AF2_DIR']='/qualification/af2/params'
seq='MQIFVKTLTGKTITLEVEPSDTIENVKAKIQDKEGIPPDQQRLIFAGKQLEDGRTLSDYNIQKESTLHLVLRLRGG'
real_run=subprocess.run
commands=[]
def msa_only(command, **kwargs):
    commands.append(command)
    return real_run(command+' --msa-only', **kwargs)
folding_models.subprocess.run=msa_only
paths=folding_models.run_colabfold([seq],'/qualification/output',keep_outputs=True)
assert paths == [None]  # MSA-only is not a successful fold.
assert '--msa-mode single_sequence --data /qualification/hf' in commands[0]
a3m=Path('/qualification/output/structures/seq_1.a3m').read_text()
assert seq in a3m
assert not list(Path('/qualification/output').rglob('*.pdb'))
assert not Path('/qualification/hf/params').exists()
assert os.environ['CACHE_DIR']=='/qualification/hf'
print('NATIVE_SINGLE_SEQUENCE_PROTOCOL_OK')
'''
    assert 'NATIVE_SINGLE_SEQUENCE_PROTOCOL_OK' in run_image(image, tmp_path, 'python', '-c', code)


def test_native_foldseek_cluster_and_search(image, tmp_path, protein):
    code = r'''
import os, subprocess
from pathlib import Path
from proteinfoundation.metrics.diversity import diversity_foldseek
from proteinfoundation.metrics.novelty import novelty
p='/qualification/protein.pdb'
assert diversity_foldseek([p,p],tmp_path='/qualification/cluster') == (0.5,1,2)
os.environ['DATA_PATH']='/qualification/data'
db=Path('/qualification/data/foldseek_db/pdb_db/pdb')
db.parent.mkdir(parents=True)
subprocess.run([os.environ['FOLDSEEK_EXEC'],'createdb',p,str(db)],check=True)
assert novelty(p,'pdb','/qualification/search') > 0.99
print('NATIVE_FOLDSEEK_PROTOCOL_OK')
'''
    assert 'NATIVE_FOLDSEEK_PROTOCOL_OK' in run_image(image, tmp_path, 'env', 'OMP_NUM_THREADS=2', 'python', '-c', code)


def test_native_mmseqs_cluster(image, tmp_path, protein):
    code = r'''
from proteinfoundation.metrics.diversity import diversity_sequence_mmseqs
p='/qualification/protein.pdb'
assert diversity_sequence_mmseqs([p,p],tmp_path='/qualification/cluster') == (0.5,1,2)
print('NATIVE_MMSEQS_PROTOCOL_OK')
'''
    assert 'NATIVE_MMSEQS_PROTOCOL_OK' in run_image(image, tmp_path, 'env', 'OMP_NUM_THREADS=2', 'python', '-c', code)

