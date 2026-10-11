"""Actual offline Nextflow parse/graph controls; no scientific execution."""
import os
from pathlib import Path
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[3]


@pytest.mark.parametrize('module,process,args', [
    ('bam_prepare', 'PrepareBamForAnalysis', 'file(params.input)'),
    ('bam_prepare', 'ValidateMappedBam', 'tuple(file(params.input), file(params.input)), file(params.reference)'),
    ('bam_prepare', 'PrepareReferenceForIGV', 'file(params.reference)'),
    ('modkit_pileup', 'ValidateModifiedBaseBam', 'tuple(file(params.input), file(params.input))'),
    ('dorado_align', 'DoradoAlign', 'file(params.input), file(params.reference)'),
    ('fastq_align', 'FastqAlign', 'file(params.input), file(params.reference)'),
    ('fastq_plasmid_qc', 'FastqPlasmidQC', 'tuple(file(params.input), file(params.input)), file(params.reference), file(params.input)'),
    ('dorado_basecall', 'DoradoDemux', 'file(params.input), file(params.input)'),
])
def test_native_nextflow_preview(tmp_path, module, process, args):
    jar = os.environ.get('ONE_COPY_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('explicit cached Nextflow JAR required; this is not native-science acceptance')
    main = tmp_path / 'main.nf'
    main.write_text(f"include {{ {process} }} from '{ROOT}/modules/ngs/{module}'\nworkflow {{ {process}({args}) }}\n")
    config = tmp_path / 'nextflow.config'
    config.write_text(f"params.code_root='{ROOT}'\nparams.out_dir='{tmp_path}/published'\nparams.input='{tmp_path}/input'\nparams.reference='{tmp_path}/reference'\n")
    (tmp_path / 'input').write_bytes(b'preview only')
    (tmp_path / 'reference').write_bytes(b'>ref\nACGT\n')
    env = dict(os.environ, NXF_HOME=str(tmp_path / 'nxf'), NXF_OFFLINE='true',
               NXF_DISABLE_CHECK_LATEST='true', NXF_ANSI_LOG='false')
    result = subprocess.run(['unshare', '--user', '--map-root-user', '--net', 'java',
        '--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.base/java.nio=ALL-UNNAMED',
        '-jar', jar, '-C', str(config), 'run', str(main), '-preview'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stdout + result.stderr
