"""Focused molecular fidelity tests; run directly in the pinned CPU image too.

No inference, network, MSA search, or checkpoints. Native tests skip only when
Boltz is absent; the evidence run executes them in boltz-community 2.9.5.
"""
import copy
import importlib.util
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
sys.path.insert(0, str(ROOT))
from lib.boltz_fidelity import component_copies, install_native_chemistry_fix
from prep_boltz_yaml import extract_chain_groups, generate_yaml_config
from publish_boltz_samples import publish_samples
import yaml


def convert(payload, directory, **overrides):
    text = (ROOT / 'modules/structure_prediction.nf').read_text()
    block = text.split('process PrepareComplexWithMSA', 1)[1]
    block = block.split("python3 << 'PYEOF'", 1)[1].split('\n', 1)[1].split('\nPYEOF', 1)[0]
    values = {'complex_json': 'input.json', 'complex_name': 'probe', 'msa_files': 'NO_MSA',
              'msaThreads': '1', 'msaTargetShards': '1', 'msaTargetShardMinSizeGb': '1',
              'msaGpuThreshold': '80', 'msaChainTimeoutSeconds': '1',
              'params.target_template_threshold_angstrom ?: 2.0': '2.0', 'params.code_root': str(ROOT)}
    values.update(overrides)
    block = re.sub(r'\$\{([^}]+)\}', lambda m: values.get(m[1], 'false' if any(x in m[1].lower() for x in ['use', 'refresh', 'only', 'anchor', 'fallback']) else ''), block)
    directory.mkdir(parents=True, exist_ok=True)
    (directory / 'input.json').write_text(json.dumps(payload))
    (directory / 'yamls').mkdir(exist_ok=True)
    (directory / 'msa').mkdir(exist_ok=True)
    cwd = Path.cwd()
    try:
        os.chdir(directory)
        with patch.object(subprocess, 'run', side_effect=AssertionError('No search/dispatch allowed')):
            exec(compile(block, 'PrepareComplexWithMSA.py', 'exec'), {})
        return yaml.safe_load((directory / 'yamls/probe.yaml').read_text())
    finally:
        os.chdir(cwd)


def pdb_fixture(path):
    lines, serial = [], 1
    for chain in ['B', 'A']:
        for pos, name in enumerate(['ALA', 'CYS', 'GLY', 'THR'], 1):
            for atom, element, dx in [('N', 'N', 0), ('CA', 'C', 1.3), ('C', 'C', 2.5), ('O', 'O', 3.0)]:
                lines.append(f'ATOM  {serial:5d} {atom:^4s} {name:3s} {chain}{pos:4d}    {pos*3.8+dx:8.3f}{dx:8.3f}{0.0:8.3f}{1.0:6.2f}{20.0:6.2f}          {element:>2s}')
                serial += 1
        lines.append('TER')
    path.write_text('\n'.join(lines) + '\nEND\n')


class Transport(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.d = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_chemistry_count_order_and_explicit_affinity(self):
        payload = {'components': [
            {'type': 'protein', 'id': ['B', 'A'], 'count': 3, 'sequence': 'ACG', 'msa': 'empty', 'cyclic': True, 'modifications': [{'position': 2, 'ccd': 'CSO'}]},
            {'type': 'ligand', 'id': 'L', 'ccd': ['HEM']},
            {'type': 'ion', 'id': 'I', 'ccd': 'ZN'},
            {'type': 'dna', 'id': 'D', 'sequence': 'ACG'},
            {'type': 'rna', 'id': 'R', 'sequence': 'ACG'}],
            'constraints': [{'bond': {'atom1': ['B', 2, 'SG'], 'atom2': ['A', 2, 'SG']}}],
            'templates': [{'pdb': 'authored.pdb', 'chain_id': 'B', 'template_id': 'T'}],
            'properties': [{'affinity': {'binder': 'L'}}]}
        out = convert(payload, self.d)
        p = out['sequences'][0]['protein']
        self.assertEqual(p['id'], ['B', 'A', 'C'])
        self.assertEqual(p['modifications'], payload['components'][0]['modifications'])
        self.assertTrue(p['cyclic'])
        self.assertEqual(out['sequences'][1]['ligand']['ccd'], ['HEM'])
        self.assertEqual(out['sequences'][2]['ligand']['ccd'], 'ZN')
        for k in ('constraints', 'templates', 'properties'):
            self.assertEqual(out[k], payload[k])

    def test_msa_native_and_short_peptide_precedence(self):
        for kind in ('protein', 'peptide'):
            for field, value in [('msa', 'empty'), ('msa', ''), ('msa', '/supplied.a3m'), ('msa_path', '/supplied.a3m')]:
                # Supplied legacy path must exist for protein branch.
                path = self.d / 'supplied.a3m'
                path.write_text('>query\nACG\n')
                value = str(path) if value == '/supplied.a3m' else value
                out = convert({'components': [{'type': kind, 'id': 'A', 'sequence': 'ACG', field: value}]}, self.d / (kind + field + str(len(value))), useMsa='true')
                self.assertEqual(out['sequences'][0]['protein']['msa'], value)
                self.assertNotIn('properties', out)

    def test_prepared_alignment_bound_once(self):
        import hashlib
        import biomodstack_boltz_msa as msa
        payload = {'components': [{'type': 'protein', 'id': ['B', 'A'], 'sequence': 'ACG', 'modifications': [{'position': 2, 'ccd': 'CSO'}], 'cyclic': True}]}
        alignment = self.d / 'supplied.a3m'; raw = b'>query\nACG\n'; alignment.write_bytes(raw)
        artifact = {'mode': 'artifact', 'path': alignment.name, 'sha256': hashlib.sha256(raw).hexdigest(), 'size_bytes': len(raw), 'format': 'a3m', 'sequence_sha256': hashlib.sha256(b'ACG').hexdigest()}
        manifest = {'schema': 'bms.boltz-msa-inputs.v1', 'tasks': [{'name': 'probe', 'native_task': payload,
                    'chains': [{'component_index': 0, 'id': ['B', 'A'], 'sequence': 'ACG', 'alignment': artifact}]}]}
        raw_manifest = json.dumps(manifest).encode(); (self.d/'msa-inputs.json').write_bytes(raw_manifest)
        with patch.object(msa, 'validate_boltz_msa', wraps=msa.validate_boltz_msa) as validate:
            out = convert(payload, self.d/'run', **{"params.boltz_prepared_msa_dir ?: ''": str(self.d),
                          "params.boltz_prepared_msa_sha256 ?: ''": hashlib.sha256(raw_manifest).hexdigest()})
        self.assertEqual(validate.call_count, 1)
        protein = out['sequences'][0]['protein']
        self.assertEqual(protein['msa'], str(alignment))
        self.assertEqual(protein['modifications'], payload['components'][0]['modifications'])
        self.assertTrue(protein['cyclic'])

    def test_pdb_identity_and_order(self):
        path = self.d / 'protein.pdb'
        pdb_fixture(path)
        out = generate_yaml_config(extract_chain_groups(path))
        self.assertEqual([e['protein']['id'] for e in out['sequences']], [['B'], ['A']])
        self.assertTrue(all(e['protein']['sequence'] == 'ACGT' for e in out['sequences']))
        self.assertEqual(generate_yaml_config({'ACGT': ['B', 'A']})['sequences'][0]['protein']['id'], ['B', 'A'])

    def test_published_samples_reach_real_alignment(self):
        import numpy as np
        inputs, native, published, designs, aligned = (self.d / n for n in ('inputs', 'native', 'published', 'designs', 'aligned'))
        inputs.mkdir(); native.mkdir(); designs.mkdir()
        for source in ('design', 'design_model_1'):
            (inputs / f'{source}.yaml').write_text('sequences: []')
            pdb_fixture(designs / f'{source}.pdb')
            folder = native / source; folder.mkdir()
            for rank in (0, 1):
                pdb_fixture(folder / f'{source}_model_{rank}.pdb')
                (folder / f'confidence_{source}_model_{rank}.json').write_text(json.dumps({'confidence_score': 0.8}))
                np.savez(folder / f'pae_{source}_model_{rank}.npz', pae=np.zeros((8, 8)))
        rows = publish_samples(native, published, inputs)
        result = subprocess.run([sys.executable, str(ROOT/'scripts/align_boltz.py'), '--design_dir', str(designs),
                                 '--boltz_dir', str(published), '--output_dir', str(aligned), '--design_type', 'monomer', '--ncpus', '1'], cwd=self.d, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(len(list(aligned.glob('*.pdb'))), 4)
        for row in rows:
            metadata = json.loads((aligned / (row['published_stem'] + '.json')).read_text())
            self.assertEqual(metadata['bms_native_source']['source_id'], row['source_id'])
            self.assertEqual(metadata['bms_native_source']['native_model_index'], row['native_model_index'])

    def test_every_sample_has_exact_original(self):
        inputs, native, output = (self.d / n for n in ('inputs', 'native', 'output'))
        inputs.mkdir(); native.mkdir()
        # Deliberately confusable IDs. No substring association is allowed.
        for source in ('design', 'design_model_1'):
            (inputs / f'{source}.yaml').write_text('sequences: []')
            folder = native / source; folder.mkdir()
            for rank in (0, 1, 2):
                (folder / f'{source}_model_{rank}.pdb').write_text('TEST FIXTURE\n')
                (folder / f'confidence_{source}_model_{rank}.json').write_text(json.dumps({'confidence_score': 0.9-rank/10}))
        rows = publish_samples(native, output, inputs)
        self.assertEqual(len(rows), 6)
        self.assertEqual(len(list(output.glob('*.pdb'))), 6)
        for row in rows:
            meta = json.loads((output / (row['published_stem'] + '.json')).read_text())
            self.assertEqual(meta['bms_native_source']['source_id'], row['source_id'])
            self.assertEqual(meta['confidence_score'], 0.9-row['native_model_index']/10)


@unittest.skipUnless(importlib.util.find_spec('boltz'), 'native Boltz image required')
class Native(Transport):
    def parse(self, payload):
        install_native_chemistry_fix()
        from boltz.data.parse.schema import parse_boltz_schema
        with patch.object(socket.socket, 'connect', side_effect=AssertionError('network disabled')):
            return parse_boltz_schema(self.d / 'probe.yaml', payload, {}, Path(os.environ.get('BMS_TEST_BOLTZ_MOLS', '/mols')), True)

    @unittest.skipUnless(os.environ.get('BMS_TEST_BOLTZ_ARGV'), 'rendered NF argv required')
    def test_actual_native_cli_settings(self):
        install_native_chemistry_fix()
        from boltz.main import cli
        commands = json.loads(Path(os.environ['BMS_TEST_BOLTZ_ARGV']).read_text())
        (self.d / 'yamls').mkdir()
        cwd = Path.cwd()
        try:
            os.chdir(self.d)
            for owner, argv in commands.items():
                context = cli.commands['predict'].make_context('predict', argv[3:])
                self.assertEqual(context.params['seed'], 0, owner)
                self.assertEqual(context.params['recycling_steps'], 0, owner)
                self.assertEqual(context.params['diffusion_samples'], 3, owner)
                self.assertEqual(context.params['sampling_steps'], 7, owner)
        finally:
            os.chdir(cwd)

    def test_actual_chemically_distinct_entities(self):
        payload = {'components': [
            {'type': 'protein', 'id': 'B', 'sequence': 'ACG', 'msa': 'empty', 'modifications': [{'position': 2, 'ccd': 'CSO'}]},
            {'type': 'protein', 'id': ['A', 'C'], 'sequence': 'ACG', 'msa': 'empty'},
            {'type': 'protein', 'id': 'D', 'sequence': 'ACG', 'msa': 'empty', 'cyclic': True}]}
        t = self.parse(convert(payload, self.d))
        names = [[str(r['name']) for r in t.structure.residues[int(c['res_idx']):int(c['res_idx']+c['res_num'])]] for c in t.structure.chains]
        self.assertEqual(names, [['ALA', 'CSO', 'GLY'], ['ALA', 'CYS', 'GLY'], ['ALA', 'CYS', 'GLY'], ['ALA', 'CYS', 'GLY']])
        entities = t.structure.chains['entity_id'].tolist()
        self.assertEqual(entities[1], entities[2]); self.assertNotEqual(entities[0], entities[1]); self.assertNotEqual(entities[1], entities[3])
        self.assertEqual(t.structure.chains['cyclic_period'].tolist(), [0, 0, 0, 3])
        self.assertEqual(t.structure.chains['name'].tolist(), ['B', 'A', 'C', 'D'])
        self.assertEqual(t.sequences, {0: 'ACG', 1: 'ACG', 2: 'ACG'})

    def test_actual_ccd_bonds_affinity_and_msa(self):
        payload = {'components': [{'type':'protein','id':['B','A'],'sequence':'ACG','msa':'empty'}, {'type':'ligand','id':'H','ccd':'HEM'}, {'type':'ligand','id':'L','ccd':['ATP']}],
                   'constraints':[{'bond':{'atom1':['B',2,'SG'],'atom2':['A',2,'SG']}}],
                   'properties':[{'affinity':{'binder':'L'}}]}
        t = self.parse(convert(payload, self.d))
        self.assertIn('HEM', t.structure.residues['name'].tolist())
        self.assertIn(26, t.structure.atoms['element'].tolist())
        self.assertIsNotNone(t.record.affinity)
        self.assertEqual(t.record.affinity.chain_id, 3)
        no_bond = copy.deepcopy(payload); no_bond.pop('constraints')
        control = self.parse(convert(no_bond, self.d))
        self.assertEqual(len(t.structure.bonds), len(control.structure.bonds) + 1)
        bond = t.structure.bonds[-1].tolist()
        self.assertEqual(bond[:2], (0, 1))
        self.assertEqual([str(t.structure.atoms[i]['name']) for i in bond[4:6]], ['SG', 'SG'])
        for intent, expected in [('', 0), ('empty', -1), (str(self.d/'supplied.a3m'), str(self.d/'supplied.a3m'))]:
            out = convert({'components':[{'type':'protein','id':'A','sequence':'ACG','msa':intent}]}, self.d)
            parsed = self.parse(out)
            self.assertEqual(parsed.record.chains[0].msa_id, expected)

    def test_actual_count_and_mixed_polymer_identity(self):
        payload = {'components': [{'type':'protein','id':['B','A'],'count':3,'sequence':'ACG','msa':'empty'},
                   {'type':'dna','id':'D','sequence':'ACG'}, {'type':'rna','id':'R','sequence':'ACG'},
                   {'type':'ion','id':'I','ccd':'ZN'}, {'type':'ligand','id':'L','smiles':'CCO'}]}
        native = self.parse(convert(payload, self.d))
        self.assertEqual(native.structure.chains['name'].tolist(), ['B', 'A', 'C', 'D', 'R', 'I', 'L'])
        self.assertEqual(native.structure.chains['mol_type'].tolist(), [0, 0, 0, 1, 2, 3, 3])

    def test_actual_target_msa_cli(self):
        msa = self.d / 'supplied.a3m'; msa.write_text('>query\nACG\n')
        output = self.d / 'complex.yaml'
        subprocess.run([sys.executable, str(ROOT/'scripts/prep_complex_yaml.py'), '--protein-seq', 'ACG',
                        '--dna-seq', 'ACG', '--msa-path', str(msa), '--output', str(output)], check=True)
        native = self.parse(yaml.safe_load(output.read_text()))
        self.assertEqual(native.record.chains[0].msa_id, str(msa))

    def test_actual_batch_anchor_and_polymer_preparation(self):
        path = self.d / 'design.pdb'; pdb_fixture(path)
        manifest = self.d / 'manifest.json'
        subprocess.run([sys.executable, str(ROOT/'scripts/extract_target_templates.py'),
                        '--pdb_files', str(path), '--target_chains', 'B', '--out_dir', str(self.d/'cifs'), '--manifest', str(manifest)], check=True)
        subprocess.run([sys.executable, str(ROOT/'scripts/prep_boltz_batch.py'),
                        '--pdb_files', str(path), '--msa_path', 'NO_MSA', '--out_dir', str(self.d/'batch'),
                        '--anchor_target', '--target_chains', 'B', '--template_manifest', str(manifest)], check=True)
        out = yaml.safe_load((self.d/'batch/design.yaml').read_text())
        self.assertEqual(len(out['templates']), 1)
        native = self.parse(out)
        self.assertTrue(native.templates)
        self.assertEqual(native.structure.chains['name'].tolist(), ['B', 'A'])

    def test_actual_top_level_template(self):
        path = self.d / 'template.pdb'; pdb_fixture(path)
        payload = {'components':[{'type':'protein','id':'Q','sequence':'ACGT','msa':'empty'}],
                   'templates':[{'pdb':str(path),'chain_id':'Q','template_id':'B1','force':True,'threshold':2.0}]}
        t = self.parse(convert(payload, self.d))
        self.assertTrue(t.templates)
        self.assertTrue(t.record.templates)


if __name__ == '__main__':
    unittest.main(verbosity=2)
