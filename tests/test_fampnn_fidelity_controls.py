"""Actual Groovy process rendering, child construction, and catalog regression.

Uses an explicitly supplied installed Nextflow all-in-one JAR, offline. Never
runs Nextflow workflows, API submission, or scientific engines.
"""
import ast
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))


class Controls(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=os.environ['TMPDIR'])
        self.work=Path(self.temp.name)
    def tearDown(self): self.temp.cleanup()

    def groovy(self, code):
        script=self.work/'probe.groovy';script.write_text(code)
        result=subprocess.run(['java','-cp',os.environ['BMS_TEST_NEXTFLOW_JAR'],'groovy.ui.GroovyMain',str(script)],capture_output=True,text=True)
        self.assertEqual(result.returncode,0,result.stderr)
        return result.stdout

    def test_actual_process_rendered_native_argv(self):
        values=dict(fampnn_seed=0,fampnn_presort_by_length=False,fampnn_timestep_mode='linear',fampnn_timestep_start=0.,fampnn_timestep_end=1.,fampnn_scn_num_steps=7,fampnn_scn_timestep_mode='cosine',fampnn_scn_timestep_start=0.,fampnn_scn_timestep_end=1.,fampnn_scn_step_scale=1.,fampnn_scn_s_churn=0.,fampnn_scn_s_noise=0.,fampnn_scn_s_t_min=0.,fampnn_scn_s_t_max=1.,fampnn_psce_threshold=None,fampnn_temperature=0.,fampnn_seq_only=True,fampnn_repack_last=False,fampnn_exclude_cys=False,fampnn_batch_size=3,seqs_per_design=4,binder_chains='A',out_dir='',code_root=str(ROOT),fampnn_extra_config='+note="$(touch NEVER_EXECUTED)"')
        json_path=self.work/'params.json';json_path.write_text(json.dumps(values))
        scripts={}
        for filename,process in [('modules/fampnn.nf','RunFAMPNN'),('modules/ppiflow.nf','RunMaturationFAMPNN')]:
            source=(ROOT/filename).read_text().split('process '+process+' {',1)[1].split('\nprocess ',1)[0]
            body=source.split('    script:\n',1)[1].split('\n}',1)[0]
            defaults=(ROOT/'modules/ppiflow.nf').read_text().split('// Native INITIAL',1)[0]
            code=(ROOT/'lib/FampnnNativeControls.groovy').read_text()+ '\n'+defaults+f'''\ndef params = new groovy.json.JsonSlurper().parse(new File('{json_path}'))
def csv='constraints.csv'; def batch_id=0; def analysis_chain_id='A,B'; def analysis_contract=[:]
def task=[index:1]; def meta=[:]; def transport_dir='transport'
def rendered = {{ ->
{body}
}}.call()
print rendered
'''
            rendered=self.groovy(code);scripts[process]=rendered
            for native in ['seed=0','presort_by_length=false','scn_diffusion.churn_cfg.s_noise=0.0','psce_threshold=null','temperature=0.0','seq_only=true','repack_last=false','batch_size=3','num_seqs_per_pdb=4']:
                self.assertIn(native,rendered)
            # Exercise the actual argv-only extra-config prelude. Command-like
            # strings remain data, never shell expansion.
            prelude=rendered.split('    # Raw config',1)[1].split('    mkdir -p results',1)[0]
            result=subprocess.run(['bash','-c','PYTHON_BIN='+shlex.quote(sys.executable)+'\n'+prelude+'\nprintf "%s\\0" "${native_extra_args[@]}"'],cwd=self.work,capture_output=True,check=True)
            self.assertEqual(result.stdout,b'+note=$(touch NEVER_EXECUTED)\x00')
            self.assertFalse((self.work/'NEVER_EXECUTED').exists())
        # Same shared advanced mapping in both paths, not just substring counts.
        for rendered in scripts.values():
            tokens=shlex.split(rendered.replace('\\\n',' '))
            for key in ['seed','presort_by_length','scn_diffusion.churn_cfg.s_noise']:
                self.assertEqual(len([v for v in tokens if v.startswith(key+'=')]),1)

    def test_stage_optional_custody_no_new_gate(self):
        p=self.work/'input.pdb';p.write_text('fixture')
        code=(ROOT/'lib/FampnnAnalysisPolicy.groovy').read_text()+f'''
def input=java.nio.file.Path.of('{p}')
assert FampnnAnalysisPolicy.stagePrepared([:],input)==[input]
def custody=input.resolveSibling('input.fampnn_source.json')
java.nio.file.Files.writeString(custody,'{{}}')
assert FampnnAnalysisPolicy.stagePrepared([:],input)==[input,custody]
println 'optional custody staged'
'''
        self.assertIn('optional custody staged',self.groovy(code))

    def test_child_constructor_preserves_controls_and_roles(self):
        import spawn_fampnn_children as owner
        (self.work/'a.pdb').write_text('fixture')
        values=dict(fampnn_seed=0,fampnn_presort_by_length=False,fampnn_psce_threshold=None,fampnn_temperature=0.,fampnn_repack_last=False,fampnn_scn_s_noise=0.,antibody_chains='H',lock_target_chains=False,manual_mutation_fixed_positions_json='{"H":[10]}',core_protein_scientific_contract=1,fampnn_analysis_policy={'not':'public_authority'})
        captured=[]
        with patch.object(owner,'check_existing_children',return_value=(False,[],{})),patch.object(owner,'find_existing_child',return_value=None),patch.object(owner,'child_status_kind',return_value='missing'),patch.object(owner,'component_runtime_enabled',return_value=True),patch.object(owner,'preferred_child_gpu',return_value=None),patch.object(owner,'submit_child_job',side_effect=lambda job,**kw: captured.append(job) or 'inert-child'):
            owner.spawn_fampnn_jobs('parent',str(self.work),1,4,'batch',params_json=json.dumps(values))
        params=captured[0]['params']
        for key,value in values.items():
            if key not in ('core_protein_scientific_contract','fampnn_analysis_policy'): self.assertEqual(params[key],value)
        self.assertNotIn('fampnn_analysis_policy',params)
        self.assertEqual(params['seqs_per_design'],4)

    def test_catalog_mode_parameters_unique(self):
        import yaml
        catalog=yaml.safe_load((ROOT/'platform/api/config/models/fampnn.yaml').read_text())
        for mode in catalog['modes']:
            self.assertEqual(len(mode['params']),len(set(mode['params'])),mode['id'])


if __name__=='__main__': unittest.main(verbosity=2)
