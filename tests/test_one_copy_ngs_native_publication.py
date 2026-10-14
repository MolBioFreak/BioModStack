"""Native samtools bytes + real Nextflow publication, not inference.

The explicit installed image can be older than the selected scientific image;
this exercises file format/placement, not current Dorado or clone qualification.
"""
import os
from pathlib import Path
import shutil
import subprocess

import pytest

ROOT = Path(__file__).resolve().parents[1]


def _run(argv, cwd, env):
    result = subprocess.run(argv, cwd=cwd, env=env, capture_output=True, text=True, errors='replace', timeout=120)
    assert result.returncode == 0, result.stdout + result.stderr
    with (cwd/'runs.log').open('a') as log:
        log.write(result.stdout+result.stderr)
    return result


@pytest.fixture
def native(tmp_path):
    image = os.environ.get('ONE_COPY_NGS_SIF')
    if not image:
        pytest.skip('explicit readonly installed image required')
    (tmp_path / 'reference.fasta').write_text('>ref\nACGTACGT\n')
    (tmp_path / 'input.sam').write_text('@HD\tVN:1.6\tSO:coordinate\n@SQ\tSN:ref\tLN:8\nr1\t0\tref\t1\t60\t4M\t*\t0\t0\tACGT\tIIII\n')
    env = dict(os.environ, HOME=str(tmp_path), APPTAINER_CACHEDIR=str(tmp_path/'cache'), APPTAINER_TMPDIR=str(tmp_path/'tmp'))
    (tmp_path/'tmp').mkdir()
    runtime = shutil.which('apptainer')
    if not runtime:
        pytest.skip('Apptainer executable required')
    cmd = ['unshare', '--user', '--map-root-user', '--net', runtime, 'exec', '--containall', '--bind', f'{tmp_path}:/fixture', image]
    result = _run(cmd + ['bash', '-c', 'cd /fixture; set -e; samtools --version; samtools view -b -o aligned.bam input.sam; samtools index aligned.bam; samtools faidx reference.fasta; samtools quickcheck aligned.bam; samtools view -c aligned.bam'], tmp_path, env)
    assert result.stdout.strip().endswith('1')
    for name in ['aligned.bam', 'aligned.bam.bai', 'reference.fasta', 'reference.fasta.fai']:
        (tmp_path/name).chmod(0o444)
    return tmp_path, env


@pytest.mark.parametrize('module,process,directory,publication', [
    ('modules/ngs/dorado_basecall.nf', 'DoradoDemux', 'demux', 'demux'),
    ('modules/ngs/fastq_align.nf', 'FastqAlign', '', 'align'),
    ('modules/ngs/dorado_align.nf', 'DoradoAlign', '', 'align'),
    ('modules/ngs/fastq_plasmid_qc.nf', 'FastqPlasmidQC', 'construct_verification_input', 'fastq_qc'),
    ('modules/ngs/fastq_dimer_qc.nf', 'FastqDimerAnalysis', '', 'multimer_qc'),
    ('modules/ngs/bam_prepare.nf', 'PrepareReferenceForIGV', '', 'align'),
    ('modules/ngs/clone_validation.nf', 'RunCloneValidation', 'wf_clone_out', 'assembly'),
    ('modules/ngs/clone_validation.nf', 'CloneValidationAdapter', 'verification_input', 'assembly/adapter'),
    ('modules/ngs/clone_validation.nf', 'ComparePlasmidConsensus', 'compared_verification_input', 'assembly/comparison'),
    ('workflows/ngs/ont_pooled_reference_assignment.nf', 'ONTPooledReferenceAssignment', '', 'pooled_reference_assignment'),
])
def test_native_payload_controller_publication_and_resume(native, module, process, directory, publication):
    root, env = native
    jar = os.environ.get('ONE_COPY_NEXTFLOW_JAR')
    if not jar:
        pytest.skip('explicit cached Nextflow JAR required')
    source = (ROOT/module).read_text().split(f'process {process} {{', 1)[1]
    directives = '\n'.join(line for line in source.split('input:', 1)[0].splitlines() if 'publishDir' in line)
    # Execute the exact production publish directives/output closure with real
    # native-produced data; only the scientific producer is outside this fixture.
    output = f'env published_directory, emit: directory\npath "{directory}/**", type: \'file\', hidden: true, emit: files' if directory else 'path "*.bam"\npath "*.bai"\npath "*.fasta"\npath "*.json"'
    prefix = directory+'/' if directory else ''
    script = f'mkdir -p {directory or "."}; ln {root}/aligned.bam {prefix}aligned.bam; ln {root}/aligned.bam.bai {prefix}aligned.bam.bai; ln {root}/reference.fasta {prefix}reference.fasta; printf "{{}}" > {prefix}metadata.json'
    if process == 'FastqAlign':
        script = script.replace(f'ln {root}/reference.fasta {prefix}reference.fasta', f'ln -s {root}/reference.fasta source_alias.fasta; source {ROOT}/scripts/ngs_input_snapshot.sh; ngs_input_snapshot source_alias.fasta {prefix}reference.fasta true true')
    if directory:
        script = f'export published_directory="\\$PWD/{directory}"; ' + script
    main = root/'main.nf'
    main.write_text(f"include {{ ngsResultPublishMode }} from '{ROOT}/modules/ngs/publication'\n" + "assert ngsResultPublishMode(file('absent-work-directory'), params.out_dir) == 'copy'\n" + f'process PublicationFixture {{\n{directives}\ninput:\nval seed\noutput:\n{output}\nscript:\n"""\n{script}\n"""\n}}\nworkflow {{ PublicationFixture(1) }}\n')
    if directory:
        consumer = 'process Observe {\ninput:\npath result_dir\noutput:\npath "observed.txt"\nscript:\n"""sha256sum ${result_dir}/aligned.bam > observed.txt"""\n}\n'
        text = main.read_text().replace('workflow { PublicationFixture(1) }', 'workflow { PublicationFixture(1); Observe(PublicationFixture.out.directory) }')
        main.write_text(consumer + text)
    if process == 'FastqDimerAnalysis':
        checks = "include { dimerPublishName } from '" + str(ROOT/'modules/ngs/fastq_dimer_qc') + "'\n"
        checks += "assert dimerPublishName('dimer_consensus.fasta', [:]) == null\n"
        checks += "assert dimerPublishName('dimer_consensus.fasta', [dimer_output_mode:'debug']) == 'dimer_consensus.fasta'\n"
        checks += "assert dimerPublishName('dimer_consensus.fasta', [dimer_emit_legacy_outputs:true]) == 'dimer_consensus.fasta'\n"
        main.write_text(checks + main.read_text())
    config = root/'nextflow.config'
    config.write_text(f'params.out_dir="{root}/published"\nprocess.executor="local"\n')
    env.update(NXF_HOME=str(root/'nxf'), NXF_OFFLINE='true', NXF_DISABLE_CHECK_LATEST='true', NXF_ANSI_LOG='false')
    cmd = ['unshare', '--user', '--map-root-user', '--net', 'java', '--add-opens=java.base/java.lang=ALL-UNNAMED', '--add-opens=java.base/java.nio=ALL-UNNAMED', '--add-opens=java.base/java.util=ALL-UNNAMED', '-jar', jar, '-trace', 'nextflow.processor.TaskProcessor', '-C', str(config), 'run', str(main), '-dump-hashes', '-with-trace', 'trace.tsv']
    _run(cmd, root, env)
    published = root/'published'/publication/prefix
    payload_name = 'reference.fasta' if process in {'CloneValidationAdapter', 'ComparePlasmidConsensus', 'FastqAlign', 'PrepareReferenceForIGV'} else 'aligned.bam'
    assert not (published/payload_name).is_symlink()
    assert (published/payload_name).stat().st_ino == (root/payload_name).stat().st_ino
    work_bam = next((root/'work').rglob('aligned.bam'))
    assert (published/'metadata.json').stat().st_ino != (work_bam.parent/'metadata.json').stat().st_ino
    payload = (published/payload_name).read_bytes()
    _run(cmd[:-2] + ['-resume', '-with-trace', 'resume.tsv'], root, env)
    import csv
    rows = list(csv.DictReader((root/'resume.tsv').open(), delimiter='\t'))
    assert len(rows) == (2 if directory else 1)
    assert all(row['status'] == 'CACHED' for row in rows)
    shutil.rmtree(root/'work')
    (root/payload_name).unlink()
    assert (published/payload_name).read_bytes() == payload


def test_native_reference_index_reused_without_chmod_or_reindex(native):
    root, env = native
    target = root/'consumer'
    target.mkdir()
    source = root/'reference.fasta'
    index = root/'reference.fasta.fai'
    before = index.stat()
    _run(['bash', '-c', 'set -e; source "$1"; ngs_input_snapshot "$2" reference.fasta true; ngs_reference_index "$2" reference.fasta true', 'fixture', str(ROOT/'scripts/ngs_input_snapshot.sh'), str(source)], target, env)
    assert (target/'reference.fasta.fai').stat().st_ino == before.st_ino
    assert index.stat().st_mode == before.st_mode
    source.unlink()
    index.unlink()
    assert (target/'reference.fasta.fai').read_text() == 'ref\t8\t5\t8\t9\n'


def test_native_reference_cross_filesystem_portable_leaf(native):
    root, env = native
    donor = root/'separate-filesystem'
    donor.mkdir()
    script = r'''
set -euo pipefail
mount -t tmpfs tmpfs "$1"
cp "$2/reference.fasta" "$1/reference.fasta"
cp "$2/reference.fasta.fai" "$1/reference.fasta.fai"
chmod 0444 "$1/reference.fasta" "$1/reference.fasta.fai"
source "$3"
ngs_input_snapshot "$1/reference.fasta" "$2/portable.fasta" true true
ngs_reference_index "$1/reference.fasta" "$2/portable.fasta" true true
[[ ! -L "$2/portable.fasta" && ! -L "$2/portable.fasta.fai" ]]
[[ "$(stat -c %d "$1/reference.fasta")" != "$(stat -c %d "$2/portable.fasta")" ]]
rm "$1/reference.fasta" "$1/reference.fasta.fai"
cmp "$2/reference.fasta" "$2/portable.fasta"
cmp "$2/reference.fasta.fai" "$2/portable.fasta.fai"
'''
    _run(['unshare','--user','--map-root-user','--mount','--net','bash','-c',script,'fixture',str(donor),str(root),str(ROOT/'scripts/ngs_input_snapshot.sh')],root,env)
