#!/usr/bin/env python3
"""Run portable candidate checks; never claim native or biological qualification."""
from __future__ import annotations
import argparse
import hashlib
import json
import shutil
import subprocess
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir',type=Path,required=True,
        help='external evidence directory, outside the source checkout')
    parser.add_argument('--require-release-qualification',action='store_true',
        help='fail because this runner does not perform release qualification')
    args=parser.parse_args(argv)
    root=Path(__file__).resolve().parents[1];out=args.output_dir.resolve()
    if out.is_relative_to(root):
        parser.error('generated evidence must be outside the source checkout')
    out.mkdir(parents=True,exist_ok=True)
    xml=out/'junit.xml'
    if xml.exists():
        parser.error('output already contains a test result; use a new evidence directory')
    command=[sys.executable,'-m','pytest','-q','--confcutdir=tests/plasmid','tests/plasmid',f'--junitxml={xml}']
    completed=subprocess.run(command,cwd=root,capture_output=True,text=True)
    (out/'pytest.stdout.txt').write_text(completed.stdout)
    (out/'pytest.stderr.txt').write_text(completed.stderr)
    totals={key:0 for key in ('tests','failures','errors','skipped')}
    if xml.is_file():
        for suite in ET.parse(xml).iter('testsuite'):
            for key in totals: totals[key]+=int(suite.attrib.get(key,0))
    diff=subprocess.run(['git','diff','--check'],cwd=root,capture_output=True,text=True)
    (out/'diff-check.txt').write_text(diff.stdout+diff.stderr)
    head=subprocess.run(['git','rev-parse','HEAD'],cwd=root,capture_output=True,text=True)
    source_paths=[*sorted((root/'tests/plasmid').glob('*.py')),
        *[root/'scripts'/name for name in ('verify_construct.py','build_construct_topology_evidence.py',
        'build_fastq_support_tables.py','plasmid_evidence.py','plasmid_circular.py',
        'validate_clone_input_model.py','compare_plasmid_consensus.py')]]
    for path in source_paths:
        compile(path.read_text(),str(path),'exec')
    result={
        'generated_at':datetime.now(timezone.utc).isoformat(),
        'local_commit':head.stdout.strip(),'python':sys.version.split()[0],
        'test_command':command,'test_exit_code':completed.returncode,
        'test_totals':totals,'diff_check_exit_code':diff.returncode,
        'source_sha256':{str(p.relative_to(root)):hashlib.sha256(p.read_bytes()).hexdigest() for p in source_paths},
        'scope':'portable_python_regressions_and_source_wiring_checks',
        'native_tools_available':{name:bool(shutil.which(name)) for name in ('samtools','nextflow','apptainer')},
        'not_run':['full_repository_tests','locked_runtime_execution','nextflow_execution',
                   'frontend_build_and_browser_acceptance','empirical_method_calibration',
                   'source_bound_runtime_record_refresh','production_deployment'],
        'release_qualified':False,
    }
    (out/'candidate-test-report.json').write_text(json.dumps(result,indent=2,sort_keys=True)+'\n')
    print(completed.stdout,end='')
    print('Candidate checks only. Native execution and scientific release qualification NOT RUN.')
    if completed.returncode or diff.returncode: return 1
    if args.require_release_qualification: return 2
    return 0

if __name__=='__main__':
    raise SystemExit(main())
