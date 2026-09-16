"""Offline native transport, NOT scientific inference/PyRosetta acceptance.

The production Nextflow wrapper/modules, preparation, constraint adapter,
metadata converter, filters and seq-prob analyzer execute. Only scientific
inference, geometry restoration and pSCE scoring are explicit test doubles.
"""
import base64
import csv
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

ROOT = Path(__file__).resolve().parents[1]


def adapter():
    spec = importlib.util.spec_from_file_location('generic_constraints', ROOT / 'scripts/prep_fampnn_constraints_generic.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mpnn_adapter():
    spec = importlib.util.spec_from_file_location('prep_mpnn_designs', ROOT/'scripts/prep_mpnn_designs.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('failure', ['missing', 'duplicate', 'foreign'])
def test_native_mpnn_metadata_identity_rejections(tmp_path, failure):
    native=tmp_path/'native'; native.mkdir()
    (native/'candidate.pdb').write_text('EXPLICIT TEST STRUCTURE BYTES')
    payload=dict(design='candidate',sequence='A',score='0.25')
    if failure=='foreign': payload['design']='other'
    if failure!='missing': (native/'arbitrary.json').write_text(json.dumps(payload))
    if failure=='duplicate': (native/'duplicate.json').write_text(json.dumps(payload))
    with pytest.raises(ValueError, match='membership|ambiguous|absent'):
        mpnn_adapter().canonical_results(native,tmp_path/'canonical')
    assert not (tmp_path/'canonical').exists()


def atom(serial, chain, number, *, insertion='', alt='', bfactor=1):
    return f'ATOM  {serial:5d}  CA {alt or " "}ALA {chain}{number:4d}{insertion or " "}   {0:8.3f}{0:8.3f}{0:8.3f}{1:6.2f}{bfactor:6.2f}           C\n'


@pytest.mark.parametrize('selection,error', [
    ({'design_chain': 'X'}, 'absent chain'),
    ({'design_chain': 'A,,B'}, 'malformed'),
    ({'target_chain': 'A'}, 'overlap'),
    ({'fixed_positions': 'A:11-12'}, 'absent'),
    ({'fixed_positions': '10'}, 'malformed'),
    ({'fixed_positions': 'A:11-10'}, 'invalid'),
    ({'design_chain': ''}, 'at least one'),
])
def test_selection_controls(selection, error):
    m = adapter()
    with pytest.raises(ValueError, match=error):
        m.constraints({('A', 10): 'ALA', ('B', 21): 'ALA'},
                      dict(sequence_design_mode='design', design_chain='A') | selection)


@pytest.mark.parametrize('text,error', [
    (atom(1, 'A', 10, insertion='A'), 'cannot represent'),
    (atom(1, 'A', 10, alt='B'), 'alternate-location'),
    (atom(1, 'A', 10) * 2, 'duplicate'),
    ('MODEL        1\n' + atom(1, 'A', 10) + 'ENDMDL\nMODEL        2\n', 'multiple'),
    (atom(1, '1', 10), 'cannot represent'),
    ('END\n', 'no protein'),
])
def test_structure_controls(tmp_path, text, error):
    source = tmp_path / 'input.pdb'; source.write_text(text)
    with pytest.raises(ValueError, match=error):
        adapter().pdb_domain(source)


def test_prepared_identity_and_legacy_empty(tmp_path):
    source = tmp_path / 'input.pdb'; source.write_text(atom(1, 'A', 10))
    prepared = tmp_path / 'prepared'; prepared.mkdir()
    (prepared / source.name).write_text(atom(1, 'B', 10))
    m = adapter()
    assert m.write_constraints(tmp_path, tmp_path/'legacy.csv') == [('input', '', '')]
    with pytest.raises(ValueError, match='changed source'):
        m.write_constraints(tmp_path, tmp_path/'bad.csv',
                            dict(sequence_design_mode='design', design_chain='A'), prepared)


def install_scientific_doubles(tmp_path):
    """Executables intercept only the named scientific engines, never prep."""
    bin_dir = tmp_path / 'bin'; bin_dir.mkdir()
    (bin_dir/'micromamba').write_text('#!/bin/sh\nexit 0\n')
    (bin_dir/'micromamba').chmod(0o755)
    doubles = tmp_path/'scientific_doubles'; doubles.mkdir()
    (doubles/'pyrosetta.py').write_text('''# EXPLICIT TEST DOUBLE: no geometry computation.
from pathlib import Path
class Info:
    def __init__(self, ids): self.ids=ids
    def chain(self, i): return self.ids[i-1][0]
    def number(self, i): return self.ids[i-1][1]
    def icode(self, i): return self.ids[i-1][2]
class Pose:
    def __init__(self, path):
        self.data=Path(path).read_bytes()
        self.ids=list(dict.fromkeys((l[21],int(l[22:26]),l[26]) for l in self.data.decode().splitlines() if l.startswith('ATOM')))
    def pdb_info(self): return Info(self.ids)
    def total_residue(self): return len(self.ids)
    def dump_pdb(self, path): Path(path).write_bytes(self.data)
def init(*args): pass
def pose_from_pdb(path): return Pose(path)
''')
    python = bin_dir/'python'
    python.write_text(f'''#!{sys.executable}
# EXPLICIT SCIENTIFIC INFERENCE / PSCE DOUBLE. Other scripts run unchanged.
import csv,hashlib,json,os,pickle,re,sys
from pathlib import Path
import numpy as np
args=sys.argv[1:]
with Path({str(tmp_path/'calls.jsonl')!r}).open('a') as f:
    real_script=Path({str(ROOT/'scripts')!r})/Path(args[0]).name
    source_hash=hashlib.sha256(real_script.read_bytes()).hexdigest() if args[0].startswith('/scripts/') and args[0] != '/scripts/analyse_fampnn.py' and real_script.is_file() else None
    f.write(json.dumps(dict(argv=args, cwd=str(Path.cwd()), actual_script_sha256=source_hash))+'\\n')
marked = args[0] == '/scripts/fampnn_native_binding.py'
if marked:
    assert args[1:5] == ['--root','/app/fampnn','--','/app/fampnn/fampnn/inference/seq_design.py']
    args=args[4:]
if args[0] == '/app/fampnn/fampnn/inference/seq_design.py':
    values=dict(a.split('=',1) for a in args[1:])
    source=next(Path('.').glob('*.pdb'))
    rows=list(csv.DictReader(Path(values['fixed_pos_csv']).open()))
    lines=[l for l in source.read_text().splitlines() if l.startswith('ATOM')]
    ids=list(dict.fromkeys((l[21],int(l[22:26])) for l in lines))
    row=next(r for r in rows if r['pdb']==source.stem)
    # Model-shaped fixed masks from the actual emitted native selector CSV.
    def mask(field):
        selected=set()
        for token in row[field].split(','):
            if not token: continue
            m=re.fullmatch(r'([A-Za-z])(\\d+)', token); assert m, token
            selected.add((m[1],int(m[2])))
        return [int(i in selected) for i in ids]
    Path({str(tmp_path/'masks.json')!r}).write_text(json.dumps(dict(row=row, ids=ids, sequence=mask('fixed_seq_positions'), sidechain=mask('fixed_sidechains'))))
    out=Path('fampnn_output'); (out/'samples').mkdir(parents=True); (out/'sample_pkls').mkdir()
    for index in range(int(values['num_seqs_per_pdb'])):
        (out/'samples'/f'{{source.stem}}_sample{{index}}.pdb').write_bytes(source.read_bytes())
        (out/'samples'/f'{{source.stem}}_sample{{index}}.fasta').write_text('>EXPLICIT_INFERENCE_STUB\\n'+'A'*len(ids)+'\\n')
    (out/'inference_config.yaml').write_text('test_double: true\\n')
    chains=list(dict.fromkeys(c for c,n in ids))
    pkl=dict(seq_probs=np.eye(21)[[0]*len(ids)],pred_aatype=np.zeros(len(ids),dtype=int),
        seq_mask=np.ones(len(ids)),aatype_override_mask=np.array(mask('fixed_seq_positions')),
        chain_index=np.array([chains.index(c) for c,n in ids]),residue_index=np.array([n for c,n in ids]))
    for index in range(int(values['num_seqs_per_pdb'])):
        pkl_path=out/'sample_pkls'/f'{{source.stem}}_sample{{index}}.pkl'
        pkl_path.write_bytes(pickle.dumps(pkl))
        if marked:
            sys.path[:0]=[{str(ROOT/'scripts')!r}, {str(ROOT/'tests')!r}]
            from fampnn_binding_fixtures import synthetic_receipt
            synthetic_receipt(source, out/'samples'/f'{{source.stem}}_sample{{index}}.pdb', pkl_path.read_bytes())
elif args[0] == '/dl_binder_design/mpnn_fr/dl_interface_design_multi.py':
    source=next(Path('.').glob('*.pdb'))
    Path({str(tmp_path/'mpnn_native_input.pdb')!r}).write_bytes(source.read_bytes())
    out=Path('results'); out.mkdir(exist_ok=True)
    for index in range(int(args[args.index('-seqs_per_struct')+1])):
        name=source.stem+'_seq_'+str(index)
        (out/(name+'.pdb')).write_bytes(source.read_bytes())
        # Both current same-stem native naming and historical prefixed metadata
        # are exercised; production binding uses payload.design, never prefixes.
        json_name=('mpnn_' if int(args[args.index('-seqs_per_struct')+1]) > 1 else '')+name+'.json'
        (out/json_name).write_text(json.dumps(dict(design=name,sequence='AA',score='0.25',test_double=True)))
    (out/'native_array.bin').write_bytes(b'EXPLICIT_NATIVE_STUB_ARRAY')
elif args[0] == '/scripts/analyse_fampnn.py':
    for pdb in Path('results').glob('*.pdb'):
        pdb.with_suffix('.json').write_text(json.dumps(dict(design=pdb.stem,sequence='AAA',fampnn_avg_psce=0.25,fampnn_max_residue_psce=0.5,test_double=True)))
else:
    if args[0].startswith('/scripts/'):
        args[0]=str(Path({str(ROOT/'scripts')!r})/Path(args[0]).name)
    os.execv({sys.executable!r},[{sys.executable!r}]+args)
''')
    python.chmod(0o755)
    return dict(os.environ, PATH=str(bin_dir)+os.pathsep+os.environ['PATH'],
                PYTHONPATH=str(doubles)+os.pathsep+os.environ.get('PYTHONPATH',''))


def run_wrapper(tmp_path, engine, mode, override=None, text=None, marked=False):
    jar = os.environ.get('BMS_TEST_NEXTFLOW_JAR')
    assert jar and Path(jar).is_file(), 'BMS_TEST_NEXTFLOW_JAR must be the cached pinned Nextflow 25.10.1 JAR'
    source=tmp_path/'subject.pdb'
    if text is None:
        text = (atom(1,'A',10)+atom(2,'A',15)) if engine == 'proteinmpnn' else (atom(1,'A',10)+atom(2,'B',21)+atom(3,'Z',77))
    source.write_text(text)
    params=dict(sequence_design_engine=engine,sequence_design_mode=mode,input_pdb=str(source),out_dir=str(tmp_path/'out'),
                design_chain='A',target_chain='B',fixed_positions='',fampnn_fix_target_sidechains=(mode=='binder_design'),
                seqs_per_design=1,fampnn_psce_threshold=0,fampnn_repack_last=False,fampnn_exclude_cys=False,
                fampnn_seq_only=True,fampnn_num_steps=20,fampnn_batch_size=1,fampnn_temperature=0.5,
                fampnn_mutation_top_n=0,fampnn_max_psce=1.0,fampnn_max_residue_psce=1.0,
                fampnn_extra_config='seed=42',mpnn_omitAAs='',mpnn_temperature=0.5,
                mpnn_backbone_noise=0,mpnn_relax_max_cycles=0,mpnn_relax_output=False,
                mpnn_relax_seqs_per_cycle=2,mpnn_relax_convergence_rmsd=0,mpnn_relax_convergence_score=0,
                mpnn_relax_convergence_max_cycles=0,mpnn_checkpoint_type='vanilla',mpnn_checkpoint_model='v_48_010',
                mpnn_max_score=1,mpnn_extra_config='-protein_features=full')
    if mode=='fixed_backbone':
        params.update(design_chain='A',target_chain=None,fixed_positions='A:10')
    params.update(override or {})
    extra=[]
    if marked:
        from services.fampnn_policy_admission import compile_declaration
        declaration=compile_declaration('fampnn',mode,params,{'summary':[{'chain_id':'B','author_number':21}],'mutation':[]})
        declaration_file=tmp_path/'admission-declaration.json'
        declaration_file.write_text(json.dumps(declaration))
        params['core_protein_scientific_contract']=1
        extra=['--fampnn_analysis_declaration_path',str(declaration_file),'--fampnn_analysis_declaration_sha256',hashlib.sha256(declaration_file.read_bytes()).hexdigest()]
    settings=tmp_path/'science-settings.json'
    settings.write_text(json.dumps({k:v for k,v in params.items() if k not in {'input_pdb','out_dir'}}))
    param_file=tmp_path/'params.json'; param_file.write_text(json.dumps(params))
    config=tmp_path/'minimal.config'
    config.write_text('process.executor="local"\nprocess.cpus=1\nprocess.memory="128 MB"\n')
    env=install_scientific_doubles(tmp_path)
    command=['java','-jar',jar,'-C',str(config),'run',str(ROOT/'workflows/protein_sequence_design.nf'),
             '-lib',str(ROOT/'lib'),'-params-file',str(param_file),'--sequence_design_settings_path',str(settings),'-work-dir',str(tmp_path/'work'),'-with-trace',str(tmp_path/'trace.tsv')]+extra
    result=subprocess.run(command,cwd=tmp_path,env=env,text=True,capture_output=True,timeout=150)
    (tmp_path/'nextflow.stdout').write_text(result.stdout)
    (tmp_path/'nextflow.stderr').write_text(result.stderr)
    (tmp_path/'command.json').write_text(json.dumps(dict(command=command,exit_code=result.returncode),indent=2))
    calls=[json.loads(l) for l in (tmp_path/'calls.jsonl').read_text().splitlines()] if (tmp_path/'calls.jsonl').exists() else []
    return result, calls, source


@pytest.mark.parametrize('engine,mode', [('fampnn','design'),('fampnn','fixed_backbone'),('fampnn','binder_design'),('proteinmpnn','design')])
@pytest.mark.parametrize('count', [1, 2])
def test_real_wrapper_native_transport(tmp_path, engine, mode, count):
    result,calls,source=run_wrapper(tmp_path,engine,mode,{'seqs_per_design':count},
        text=('REMARK PDBinfo-LABEL: 15 FIXED\n'+atom(1,'A',10)+atom(2,'A',15)) if engine=='proteinmpnn' else None)
    assert result.returncode==0,result.stdout+result.stderr
    scripts=[row['argv'][0] for row in calls]
    assert '/scripts/add_fixed_labels.py' not in scripts
    assert not any(any(word in name.lower() for word in ['rfd','antibody','boltz','af2','spawn']) for name in scripts)
    stages={r['name'].split(':')[-1].split(' (')[0] for r in csv.DictReader((tmp_path/'trace.tsv').open(),delimiter='\t')}
    assert stages == ({'PrepFAMPNN','RunFAMPNN','FilterFAMPNN','PublishSequenceDesign'} if engine=='fampnn' else {'PrepMPNN','RunMPNN','FilterMPNN','PublishSequenceDesign'})
    out=tmp_path/'out'
    assert (out/'pdb_files/subject_seq_0.pdb').read_bytes()==source.read_bytes()
    assert len(list((out/'pdb_files').glob('*.pdb')))==count
    index=json.loads((out/'results/sequence_design_results.json').read_text())
    from scripts.sequence_design_results import load_result_index
    loaded, validated, index_hash = load_result_index(out, engine, mode)
    assert loaded == index and len(validated) == count
    assert index_hash == hashlib.sha256((out/'results/sequence_design_results.json').read_bytes()).hexdigest()
    assert index['unfiltered_count']==index['selected_count']==count
    assert index['source']['sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert index['settings']==json.loads((tmp_path/'science-settings.json').read_text())
    assert index['settings_bytes']['sha256']==hashlib.sha256((tmp_path/'science-settings.json').read_bytes()).hexdigest()
    assert len(index['candidates'])==count and all(r['selected'] for r in index['candidates'])
    assert {r['name'] for r in index['candidates']}=={f'subject_seq_{n}' for n in range(count)}
    native_json=(out/'pdb_files/subject_seq_0.json').read_bytes()
    label='fampnn' if engine=='fampnn' else 'mpnn'
    assert (out/f'collected/{label}_filtered/subject_seq_0.json').read_bytes()==native_json
    rows=[json.loads(l) for l in (out/'results/metadata_fold_seq.jsonl').read_text().splitlines() if l.strip()]
    assert len(rows)==count
    assert rows[0]['sequence']==('AAA' if engine=='fampnn' else 'AA')
    assert rows[0]['fampnn_avg_psce' if engine=='fampnn' else 'mpnn_score']==0.25
    if engine=='fampnn':
        native=next(r['argv'] for r in calls if r['argv'][0].endswith('/seq_design.py'))
        args=dict(a.split('=',1) for a in native[1:])
        assert {k:args[k] for k in ['psce_threshold','repack_last','exclude_cys','seq_only','num_seqs_per_pdb','batch_size','temperature','timestep_schedule.num_steps','seed']} == dict(psce_threshold='0',repack_last='false',exclude_cys='false',seq_only='true',num_seqs_per_pdb=str(count),batch_size='1',temperature='0.5',seed='42',**{'timestep_schedule.num_steps':'20'})
        masks=json.loads((tmp_path/'masks.json').read_text())
        assert masks['ids']==[['A',10],['B',21],['Z',77]]
        assert masks['sequence']==([1,1,1] if mode=='fixed_backbone' else [0,1,1])
        assert masks['sidechain']==({'fixed_backbone':[0,1,1],'design':[0,0,1],'binder_design':[0,1,1]}[mode])
        assert (out/'run/fampnn/raw/samples/subject_sample0.fasta').read_text().startswith('>EXPLICIT_INFERENCE_STUB')
        assert (out/'run/fampnn/raw/inference_config.yaml').read_text()=='test_double: true\n'
        assert (out/'run/fampnn/raw/sample_pkls/subject_sample0.pkl').is_file()
    else:
        assert (tmp_path/'mpnn_native_input.pdb').read_bytes()==source.read_bytes()
        native=next(r['argv'] for r in calls if r['argv'][0].endswith('/dl_interface_design_multi.py'))
        assert native[native.index('-omit_AAs')+1]==''
        assert native[native.index('-augment_eps')+1]=='0'
        assert native[native.index('-relax_convergence_rmsd')+1]=='0'
        assert native[native.index('-relax_convergence_score')+1]=='0'
        assert native[native.index('-relax_convergence_max_cycles')+1]=='0'
        assert '-relax_output' not in native
        assert '-protein_features=full' in native
        assert (out/'run/mpnn/raw/native_array.bin').read_bytes()==b'EXPLICIT_NATIVE_STUB_ARRAY'
    (tmp_path/'output-hashes.json').write_text(json.dumps({str(p.relative_to(out)):hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(out.rglob('*')) if p.is_file()},indent=2))


@pytest.mark.parametrize('engine,override,text,error', [
    ('fampnn',{'target_chain':'A'},None,'overlap'),
    ('fampnn',{'fixed_positions':'A:99'},None,'absent'),
    ('fampnn',{'fampnn_extra_config':'fixed_pos_csv=other.csv'},None,'cannot override'),
    ('proteinmpnn',{'mpnn_extra_config':'-outpdbdir=/tmp/other'},None,'cannot override'),
    ('proteinmpnn',{},atom(1,'A',10)+atom(2,'B',21),'multi-chain'),
    ('proteinmpnn',{},'REMARK PDBinfo-LABEL: 99 FIXED\n'+atom(1,'A',10),'FIXED'),
])
def test_real_wrapper_rejects_before_inference(tmp_path,engine,override,text,error):
    result,calls,_=run_wrapper(tmp_path,engine,'design',override,text)
    assert result.returncode!=0
    errors=result.stdout+result.stderr+'\n'.join(p.read_text() for p in (tmp_path/'work').rglob('.command.err'))
    assert error in errors
    assert not any(r['argv'][0].endswith(('/seq_design.py','/dl_interface_design_multi.py')) for r in calls)


@pytest.mark.parametrize('engine', ['fampnn', 'proteinmpnn'])
def test_real_wrapper_observed_empty_filter_selection(tmp_path, engine):
    result, calls, source = run_wrapper(tmp_path, engine, 'design', {'fampnn_max_psce': 0, 'mpnn_max_score': 0})
    assert result.returncode == 0, result.stdout + result.stderr
    out = tmp_path/'out'
    assert len(list((out/'pdb_files').glob('*.pdb'))) == 1
    assert not list((out/'collected').rglob('*.pdb'))
    index=json.loads((out/'results/sequence_design_results.json').read_text())
    from scripts.sequence_design_results import load_result_index
    loaded, validated, _ = load_result_index(out, engine, 'design')
    assert loaded == index and len(validated) == 1
    assert index['unfiltered_count']==1 and index['selected_count']==0
    assert all(not r['selected'] for r in index['candidates'])
    publish = next(r for r in calls if r['argv'][0] == '/scripts/sequence_design_results.py')
    staged = Path(publish['cwd'])
    assert not list((staged/'filtered').glob('*'))
    assert list((staged/'filter_logs').glob('*.log'))
    assert (staged/'source'/source.name).read_bytes() == source.read_bytes()


@pytest.mark.parametrize('mode', ['design', 'fixed_backbone', 'binder_design'])
def test_real_admission_declaration_to_marked_native_analyzer(tmp_path, mode):
    result, calls, source = run_wrapper(tmp_path, 'fampnn', mode, marked=True)
    assert result.returncode == 0, result.stdout + result.stderr
    declaration = json.loads((tmp_path/'admission-declaration.json').read_text())
    assert declaration['summary_override'] == ['B:21:']
    assert declaration['mutation_override'] == []
    assert any(r['argv'][0] == '/scripts/fampnn_native_binding.py' for r in calls)
    assert any(r['argv'][0] == '/scripts/fampnn_policy_resolution.py' for r in calls)
    metrics = [json.loads(line) for p in (tmp_path/'out/run/fampnn/seq_prob_metrics').glob('*.jsonl') for line in p.read_text().splitlines()]
    assert len(metrics) == 1
    row = metrics[0]
    assert row['core_protein_scientific_contract'] == 1
    assert row['analysis_policy']['inputs']['subject']['summary_override'] == ['B:21:']
    assert row['analysis_policy']['inputs']['subject']['mutation_override'] == []
    assert row['artifact_binding']['source_pdb']['sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert row['fampnn_top_model_favored_mutations'] == []
    receipt = next((tmp_path/'work').glob('**/fampnn_input/subject.fampnn_prep.json'))
    proof = json.loads(receipt.read_text())
    assert proof['source_domain'] == proof['prepared_domain'] == ['A:10:', 'B:21:', 'Z:77:']
