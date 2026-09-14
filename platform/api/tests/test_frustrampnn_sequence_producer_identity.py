from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
NEXTFLOW_IMAGE = "nextflow/nextflow:25.10.1"


def _docker_available() -> bool:
    docker = shutil.which("docker")
    if docker is None:
        return False
    return subprocess.run(
        [docker, "image", "inspect", NEXTFLOW_IMAGE],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    ).returncode == 0


def _write_executable(path: Path, text: str) -> None:
    path.write_text(text, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_sequence_boundary(run_root: Path, names: list[str]) -> list[dict[str, object]]:
    run_root.mkdir(parents=True)
    for directory in ("bin", "fakepy", "out", "work"):
        (run_root / directory).mkdir()
    _write_executable(
        run_root / "bin" / "boltz",
        """#!/bin/sh
set -eu
mkdir -p boltz_results_yamls/predictions/submission
cat > boltz_results_yamls/predictions/submission/model_0.pdb <<'EOF'
ATOM      1  N   GLY A   1      11.000  12.000  13.000  1.00 20.00           N
ATOM      2  CA  GLY A   1      12.000  12.000  13.000  1.00 20.00           C
ATOM      3  C   GLY A   1      13.000  12.000  13.000  1.00 20.00           C
ATOM      4  O   GLY A   1      14.000  12.000  13.000  1.00 20.00           O
END
EOF
""",
    )
    (run_root / "fakepy" / "yaml.py").write_text(
        "def dump(value, *args, **kwargs):\n    return 'version: 1\\nsequences: []\\n'\n",
        encoding="utf-8",
    )
    tuples = ",\n        ".join(
        f"tuple('ACDEFGHIK', {json.dumps(name)})" for name in names
    )
    (run_root / "main.nf").write_text(
        f"""nextflow.enable.dsl = 2
import groovy.json.JsonOutput
include {{ structure_prediction_wf }} from '/workspace/modules/structure_prediction.nf'
workflow {{
    inputs = Channel.of(
        {tuples}
    )
    structure_prediction_wf(inputs)
    structure_prediction_wf.out.canonical_structures
        .map {{ producer_meta, predicted -> producer_meta }}
        .collect()
        .view {{ records -> 'CANONICAL_RECORDS:' + JsonOutput.toJson(records) }}
}}
""",
        encoding="utf-8",
    )
    (run_root / "nextflow.config").write_text(
        """params.out_dir = '/run/out'
params.code_root = '/workspace'
params.pred_method = 'boltz'
params.boltz_use_msa = false
params.boltz_num_samples = 1
process.executor = 'local'
env {
    PATH = "/run/bin:${System.getenv('PATH')}"
    PYTHONPATH = '/run/fakepy'
}
docker.enabled = false
singularity.enabled = false
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-e",
            "NXF_OFFLINE=true",
            "-e",
            "NXF_DISABLE_CHECK_LATEST=true",
            "-v",
            f"{REPO_ROOT}:/workspace:ro",
            "-v",
            f"{run_root}:/run:rw",
            "-w",
            "/run",
            NEXTFLOW_IMAGE,
            "nextflow",
            "run",
            "/run/main.nf",
            "-c",
            "/run/nextflow.config",
            "-offline",
            "-w",
            "/run/work",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    payloads = [
        line.split("CANONICAL_RECORDS:", 1)[1]
        for line in completed.stdout.splitlines()
        if "CANONICAL_RECORDS:" in line
    ]
    assert payloads, output
    assert len(set(payloads)) == 1, output
    records = json.loads(payloads[0])
    assert isinstance(records, list)
    return records


def _run_stubbed_nonboltz_boundary(
    run_root: Path, predictor: str, names: list[str]
) -> list[dict[str, object]]:
    run_root.mkdir(parents=True)
    for directory in ("bin", "out", "work"):
        (run_root / directory).mkdir()
    _write_executable(
        run_root / "bin" / "python3",
        """#!/usr/bin/python3
import pathlib, sys
args = sys.argv[1:]
if not args or args[0] == '-' or any(pathlib.Path(arg).name == 'run_inference.py' for arg in args):
    sys.stdin.read()
    target = pathlib.Path('output/submission/model.pdb')
elif any(pathlib.Path(arg).name == 'run_protenix_inference.py' for arg in args):
    target = pathlib.Path('predictions/submission/model.cif')
    target.parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path('predictions/submission/model_full_data.json').write_text('{}\\n')
elif any(pathlib.Path(arg).name == 'bms_gpu_run_telemetry.py' for arg in args):
    target = pathlib.Path('esmfold2_results/model.cif')
    target.parent.mkdir(parents=True, exist_ok=True)
    pathlib.Path('esmfold2_results/model.metrics.json').write_text('{}\\n')
    pathlib.Path('esmfold2_results/model.telemetry.json').write_text('{}\\n')
    pathlib.Path('esmfold2_results/manifest.json').write_text('{}\\n')
    pathlib.Path('esmfold2_results/summary.tsv').write_text('metric\\tvalue\\n')
else:
    raise SystemExit(0)
target.parent.mkdir(parents=True, exist_ok=True)
target.write_text('EQUAL STRUCTURE BYTES\\n')
""",
    )
    _write_executable(
        run_root / "bin" / "find",
        """#!/bin/sh
case "$*" in
  *"*full_data*.json"*) printf '%s\\n' predictions/submission/model_full_data.json ;;
  *"*.cif"*) printf '%s\\n' predictions/submission/model.cif ;;
esac
""",
    )
    (run_root / "NO_MSA").write_text("", encoding="utf-8")
    tuples = ",\n        ".join(
        f"tuple('ACDEFGHIK', {json.dumps(name)})" for name in names
    )
    (run_root / "main.nf").write_text(
        f"""nextflow.enable.dsl = 2
import groovy.json.JsonOutput
include {{ structure_prediction_wf }} from '/workspace/modules/structure_prediction.nf'
workflow {{
    inputs = Channel.of(
        {tuples}
    )
    structure_prediction_wf(inputs)
    structure_prediction_wf.out.canonical_structures
        .map {{ producer_meta, predicted -> producer_meta }}
        .collect()
        .view {{ records -> 'CANONICAL_RECORDS:' + JsonOutput.toJson(records) }}
}}
""",
        encoding="utf-8",
    )
    (run_root / "nextflow.config").write_text(
        f"""params.out_dir = '/run/out'
params.code_root = '/run'
params.pred_method = {json.dumps(predictor)}
params.rf3_use_msa = false
params.protenix_use_msa = false
process.executor = 'local'
env.PATH = "/run/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
docker.enabled = false
singularity.enabled = false
""",
        encoding="utf-8",
    )
    completed = subprocess.run(
        [
            "docker", "run", "--rm", "--network", "none",
            "-e", "NXF_OFFLINE=true", "-e", "NXF_DISABLE_CHECK_LATEST=true",
            "-v", f"{REPO_ROOT}:/workspace:ro", "-v", f"{run_root}:/run:rw",
            "-w", "/run", NEXTFLOW_IMAGE, "nextflow", "run", "/run/main.nf",
            "-c", "/run/nextflow.config", "-stub-run", "-offline", "-w", "/run/work",
        ],
        text=True,
        capture_output=True,
        check=False,
    )
    output = f"{completed.stdout}\n{completed.stderr}"
    assert completed.returncode == 0, output
    payloads = [
        line.split("CANONICAL_RECORDS:", 1)[1]
        for line in completed.stdout.splitlines()
        if "CANONICAL_RECORDS:" in line
    ]
    assert payloads, output
    assert len(set(payloads)) == 1, output
    records = json.loads(payloads[0])
    assert isinstance(records, list)
    return records


def _preview_invalid_batch(run_root: Path, entries: list[dict[str, object]]) -> str:
    run_root.mkdir(parents=True)
    (run_root / "out").mkdir()
    (run_root / "work").mkdir()
    (run_root / "batch.json").write_text(json.dumps(entries), encoding="utf-8")
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "-e",
            "NXF_OFFLINE=true",
            "-e",
            "NXF_DISABLE_CHECK_LATEST=true",
            "-v",
            f"{REPO_ROOT}:/workspace:ro",
            "-v",
            f"{run_root}:/run:rw",
            "-w",
            "/run",
            NEXTFLOW_IMAGE,
            "nextflow",
            "run",
            "/workspace/workflows/protein_design.nf",
            "-c",
            "/workspace/nextflow.config",
            "-preview",
            "-offline",
            "-profile",
            "boltz,workstation_ryzen7960x",
            "-w",
            "/run/work",
            "--code_root",
            "/workspace",
            "--out_dir",
            "/run/out",
            "--job_id",
            "invalid-sequence-batch",
            "--pred_method",
            "boltz",
            "--run_frustrampnn",
            "false",
            "--sequence_batch_json_path",
            "/run/batch.json",
        ],
        text=True,
        capture_output=True,
        check=False,
        env=os.environ.copy(),
    )
    return f"{completed.stdout}\n{completed.stderr}"


@pytest.mark.runtime_integration
def test_boltz_sequence_boundary_preserves_duplicate_sequence_submission_identity_and_reordering(
    tmp_path: Path,
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned image {NEXTFLOW_IMAGE}")

    forward = _run_sequence_boundary(tmp_path / "forward", ["sample-A", "sample-B"])
    reverse = _run_sequence_boundary(tmp_path / "reverse", ["sample-B", "sample-A"])

    assert len(forward) == 2
    assert len(reverse) == 2
    assert {record["producer_artifact_id"] for record in forward} == {
        "sample-A",
        "sample-B",
    }
    assert {record["producer_artifact_key"] for record in forward} == {
        "sample-A",
        "sample-B",
    }
    assert {record["producer_sample"] for record in forward} == {
        "sample-A",
        "sample-B",
    }
    assert {
        record["producer_artifact_id"] for record in forward
    } == {record["producer_artifact_id"] for record in reverse}


@pytest.mark.runtime_integration
@pytest.mark.parametrize("predictor", ["rf3", "protenix", "esmfold2"])
def test_nonboltz_sequence_boundaries_preserve_equal_byte_submission_identity_and_reordering(
    tmp_path: Path, predictor: str
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned image {NEXTFLOW_IMAGE}")

    forward = _run_stubbed_nonboltz_boundary(
        tmp_path / "forward", predictor, ["sample-A", "sample-B"]
    )
    reverse = _run_stubbed_nonboltz_boundary(
        tmp_path / "reverse", predictor, ["sample-B", "sample-A"]
    )

    assert len(forward) == 2
    assert len(reverse) == 2
    assert {record["producer_artifact_id"] for record in forward} == {
        "sample-A", "sample-B"
    }
    assert {record["producer_submission_id"] for record in forward} == {
        "sample-A", "sample-B"
    }
    assert {record["producer_method"] for record in forward} == {predictor}
    assert {record["producer_artifact_id"] for record in forward} == {
        record["producer_artifact_id"] for record in reverse
    }


@pytest.mark.runtime_integration
@pytest.mark.parametrize(
    "entries",
    [
        [
            {"id": "sample-A", "name": "first", "sequence": "ACDE"},
            {"id": "sample-A", "name": "second", "sequence": "ACDE"},
        ],
        [{"id": "", "name": "sample-A", "sequence": "ACDE"}],
    ],
    ids=["duplicate-entry-id", "empty-entry-id"],
)
def test_protein_design_rejects_indistinguishable_sequence_batch_entries_before_prediction(
    tmp_path: Path, entries: list[dict[str, object]]
) -> None:
    if not _docker_available():
        pytest.skip(f"missing pinned image {NEXTFLOW_IMAGE}")

    output = _preview_invalid_batch(tmp_path / "preview", entries)

    assert "protein_design:invalid_sequence_batch_identity" in output
    assert "PROTEIN_DESIGN:structure_prediction_wf" not in output


# Only scientific inference is doubled; the task runs the real manifest writer.
def _run_native_manifest_boundary(tmp_path: Path, *, complex_input=False, fault="", method="protenix", publication=False, designed=False, pdb=False):
    import sys
    jar_path = os.environ.get("BMS_TEST_NEXTFLOW_JAR")
    if not jar_path:
        pytest.skip("BMS_TEST_NEXTFLOW_JAR is required for pinned Nextflow integration")
    jar = Path(jar_path)
    assert jar.name == "nextflow-25.10.1-one.jar"
    module = REPO_ROOT / "modules/structure_prediction.nf"
    declaration = next(line.strip() for line in (REPO_ROOT / "modules/protenix.nf").read_text().splitlines()
                       if "emit: typed_cifs" in line)
    if complex_input:
        declaration = next(line.strip() for line in (REPO_ROOT / "modules/protenix.nf").read_text().splitlines()
                           if "emit: canonical_structures" in line)
        declaration = declaration.replace('input_sample', 'producer_meta').replace('canonical_structures', 'typed_cifs')
        declaration = declaration.replace("${protenixComplexFinalizesGeometry(params) ? 'pdb' : 'cif'}", "pdb" if pdb else "cif")
    elif method == "esmfold2":
        declaration = 'tuple val(producer_meta), path("esmfold2_results/**/*.cif"), emit: typed_cifs'
    root_name = "esmfold2_results" if method == "esmfold2" else "predictions"
    native = tmp_path / "native.py"
    native.write_text("""import base64, json, pathlib, subprocess, sys
meta = json.loads(base64.b64decode(sys.argv[1]))
name = meta if isinstance(meta, str) else meta['producer_sample']
root = pathlib.Path(sys.argv[2])
# Two seeds and two samples; equal bytes intentionally cannot bind identity.
for seed in ((42,) if sys.argv[6] == "publication" else (42, 43)):
    for sample in (0, 1):
        p = root / name / f'seed_{seed}' / 'predictions' / f'{name}_sample_{sample}.{sys.argv[7]}'
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(b'EQUAL NATIVE STRUCTURE BYTES\\n')
if sys.argv[3] == 'esmfold2':
    raise SystemExit(0)
cmd = [sys.executable, sys.argv[4], '--predictions-root', str(root), '--producer-method', sys.argv[3],
       '--format', 'pdb' if sys.argv[7] == 'pdb' else 'mmcif', '--output', 'producer_candidates.json']
if sys.argv[6].startswith('publication'):
    cmd += ['--publication-dir', 'producer_publication', '--published-structure-root', 'pdb_files/predictions']
cmd += ['--producer-sample', name] if isinstance(meta, str) else ['--sequence-metadata-base64', sys.argv[1]]
subprocess.run(cmd, check=True)
p = pathlib.Path('producer_candidates.json')
m = json.loads(p.read_text())
fault = sys.argv[5]
if fault == 'duplicate': m['candidates'][1] = m['candidates'][0]
if fault == 'missing': m['candidates'].pop()
if fault == 'foreign': m['candidates'][0]['producer_output_key'] = 'foreign/model.cif'
if fault == 'sha': m['candidates'][0]['producer_artifact_sha256'] = '0' * 64
if fault == 'format': m['candidates'][0]['source_format'] = 'pdb'
if fault == 'method': m['candidates'][0]['producer_method'] = 'boltz'
if fault == 'sample': m['candidates'][0]['producer_sample'] = 'foreign'
if fault == 'rank': m['candidates'][0]['producer_rank'] = 999
if fault == 'sequence': m['candidates'][0]['producer_sequence'] = 'FOREIGN'
if fault == 'symlink':
    source = next(root.rglob('*.cif'))
    target = pathlib.Path('foreign.cif').absolute()
    target.write_bytes(source.read_bytes())
    source.unlink()
    source.symlink_to(target)
if fault == 'manifest-root':
    pathlib.Path('foreign').mkdir()
    pathlib.Path('foreign/producer_candidates.json').write_text(json.dumps(m))
if fault: p.write_text(json.dumps(m))
if fault == 'absent-manifest': p.unlink()
""")
    if fault == "manifest-root":
        declaration = declaration.replace('path("producer_candidates.json")', 'path("foreign/producer_candidates.json")')
    transform = ".map { meta, manifest, files -> tuple(meta, manifest, [files[0], files[0], files[2], files[3]]) }" if fault == "duplicate-file" else ""
    invocation = ("complexCanonicalProducerOutputs(Native.out.typed_cifs" + transform + f", '{method}')" if complex_input else
                  "canonicalProducerOutputs(Native.out.typed_cifs" + transform + f", '{method}')")
    inputs = "Channel.of('sample-A', 'sample-B')" if complex_input else "Channel.of(sequenceProducerMetadata('ACDE', 'sample-A'), sequenceProducerMetadata('ACDE', 'sample-B'))"
    if designed:
        inputs += ".map { meta -> meta + [producer_fold: 'fold-7', producer_rank: 3, producer_submission_id: 'original', producer_submission_name: 'Original submission', original_submission_identity: [id:'original', name:'Original submission']] }"
    (tmp_path / "main.nf").write_text(f'''nextflow.enable.dsl=2
import groovy.json.JsonOutput
include {{ canonicalProducerOutputs; complexCanonicalProducerOutputs; sequenceProducerMetadata }} from '{module}'
process Native {{
 input:
 val producer_meta
 output:
 {declaration}
 script:
 def encoded = JsonOutput.toJson(producer_meta).getBytes('UTF-8').encodeBase64().toString()
 """
 {sys.executable} '{native}' '${{encoded}}' '{root_name}' '{method}' '{REPO_ROOT}/scripts/write_structure_producer_manifest.py' '{fault}' '{'publication-many' if fault == 'publication-collision' else ('publication' if publication else '')}' '{'pdb' if pdb else 'cif'}'
 """
}}
workflow {{
 Native({inputs})
 records = {invocation}
 records.map {{ meta, predicted -> meta }}.collect().view {{ 'CANONICAL_RECORDS:' + JsonOutput.toJson(it) }}
}}
''')
    (tmp_path / "nextflow.config").write_text("process.executor='local'\nprocess.cpus=1\nprocess.maxForks=2\ndocker.enabled=false\nsingularity.enabled=false\n")
    completed = subprocess.run([
        "java", "--add-opens=java.base/java.lang=ALL-UNNAMED",
        "--add-opens=java.base/java.util=ALL-UNNAMED", "--add-opens=java.base/java.nio=ALL-UNNAMED",
        "--add-opens=java.base/sun.nio.ch=ALL-UNNAMED", "-jar", str(jar), "-C", str(tmp_path / "nextflow.config"),
        "run", str(tmp_path / "main.nf"), "-offline", "-w", str(tmp_path / "work")],
        cwd=tmp_path, env=dict(os.environ, NXF_OFFLINE="true", NXF_DISABLE_CHECK_LATEST="true"),
        text=True, capture_output=True, timeout=120)
    output = completed.stdout + completed.stderr
    (tmp_path / "runner.log").write_text(output)
    if fault:
        assert completed.returncode != 0, output
        if fault == "publication-collision":
            assert "flat publication has duplicate native destinations" in output
        assert "producer" in output and ("ERROR" in output or "Error" in output), output
        assert "CANONICAL_RECORDS:" not in output, output
        return
    assert completed.returncode == 0, output
    payload = next(line.partition("CANONICAL_RECORDS:")[2] for line in output.splitlines() if "CANONICAL_RECORDS:" in line)
    records = json.loads(payload)
    if method == "esmfold2":
        assert len(records) == 8
        assert {r['producer_method'] for r in records} == {'esmfold2'}
        assert {r['producer_sample'] for r in records} == {'sample-A', 'sample-B'}
        return
    manifests = list((tmp_path / "work").glob("*/*/producer_candidates.json"))
    assert len(manifests) == 2
    if publication:
        for manifest in manifests:
            archived = list((manifest.parent / 'producer_publication').glob('*/producer_candidates.json'))
            assert len(archived) == 1
            assert archived[0].read_bytes() == manifest.read_bytes()
    expected = [row for p in manifests for row in json.loads(p.read_text())["candidates"]]
    assert sorted(records, key=lambda r: r['producer_output_key']) == sorted(expected, key=lambda r: r['producer_output_key'])
    assert len(records) == (4 if publication else 8)
    assert all('/seed_' in r['producer_output_key'] and '/predictions/' in r['producer_output_key'] for r in records)
    assert {r['producer_rank'] for r in records} == ({None} if method == 'boltz' else ({0, 1} if complex_input else ({3} if designed else {None})))


def test_pinned_native_sequence_manifest_preserves_nested_predictions(tmp_path):
    _run_native_manifest_boundary(tmp_path)


def test_pinned_native_sequence_manifest_preserves_designed_identity(tmp_path):
    _run_native_manifest_boundary(tmp_path, designed=True)


def test_pinned_native_multiseed_publication_collision_remains_rejected(tmp_path):
    # Existing flat publication policy is not weakened by this transport fix.
    _run_native_manifest_boundary(tmp_path, fault="publication-collision")


def test_pinned_native_sequence_manifest_full_publication_control(tmp_path):
    _run_native_manifest_boundary(tmp_path, publication=True)


def test_pinned_native_complex_pdb_manifest_control(tmp_path):
    _run_native_manifest_boundary(tmp_path, complex_input=True, pdb=True)


def test_pinned_boltz_complex_producer_control(tmp_path):
    _run_native_manifest_boundary(tmp_path, complex_input=True, method="boltz")


def test_pinned_native_complex_manifest_preserves_nested_predictions(tmp_path):
    _run_native_manifest_boundary(tmp_path, complex_input=True)


def test_pinned_protenix_caller_coverage():
    module = (REPO_ROOT / "modules/structure_prediction.nf").read_text()
    assert module.count("ProtenixPredict.out.typed_cifs, 'protenix'") == 3
    assert module.count("ProtenixFromComplex.out.canonical_structures, 'protenix'") == 2
    assert "esmfold2_outputs = ESMFold2Predict.out.typed_cifs" in module
    assert "esmfold2_outputs = ESMFold2MSAPredict.out.typed_cifs" in module
    for workflow in ("structure_prediction", "protein_design"):
        text = (REPO_ROOT / f"workflows/{workflow}.nf").read_text()
        assert "include { structure_prediction_wf }" in text
        assert "structure_prediction_wf.out.canonical_structures" in text


def test_pinned_esmfold2_producer_control(tmp_path):
    _run_native_manifest_boundary(tmp_path, method="esmfold2")


@pytest.mark.parametrize("fault", ["duplicate", "missing", "foreign", "sha", "format", "method", "sample", "rank", "sequence", "symlink", "manifest-root", "duplicate-file", "absent-manifest"])
def test_pinned_native_sequence_manifest_rejects_unbound_identity(tmp_path, fault):
    _run_native_manifest_boundary(tmp_path, fault=fault)


@pytest.mark.parametrize("fault", ["duplicate", "missing", "foreign", "sha", "format", "method", "sample", "symlink", "manifest-root", "duplicate-file", "absent-manifest"])
def test_pinned_native_complex_manifest_rejects_unbound_identity(tmp_path, fault):
    _run_native_manifest_boundary(tmp_path, complex_input=True, fault=fault)
