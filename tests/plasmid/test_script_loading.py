import subprocess
import sys
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[2]

@pytest.mark.parametrize('name',['verify_construct.py','build_construct_topology_evidence.py','compare_plasmid_consensus.py'])
def test_runpy_can_load_script_without_repo_in_python_path(tmp_path,name):
    result=subprocess.run([sys.executable,'-I','-c','import runpy,sys; runpy.run_path(sys.argv[1])',str(ROOT/'scripts'/name)],
        cwd=tmp_path,capture_output=True,text=True,timeout=10)
    assert result.returncode==0,result.stderr
