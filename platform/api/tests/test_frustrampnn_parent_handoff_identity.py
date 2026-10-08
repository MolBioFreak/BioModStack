"""Behavioral parent identity boundary tests; NOT scientific/model acceptance.

Scope of the isolated Nextflow harness is explicitly:
  exact checked-in parent helper functions + exact candidate channel map
  -> real StageFrustraMPNNParentCandidate -> real Python staging script.
Prediction, canonical complex preparation, scheduler submission, and child execution
are deliberately excluded. The identical source bytes are labelled test transport
fixtures, not generated structures or scientific results. No model/GPU is used.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

from services.frustrampnn.identity import deterministic_candidate_id

REPO_ROOT = Path(__file__).resolve().parents[3]
NEXTFLOW_IMAGE = "nextflow/nextflow:25.10.1"
FIXTURE_BYTES = b"REMARK IDENTITY TRANSPORT TEST FIXTURE ONLY; NOT A STRUCTURE\n"
pytestmark = pytest.mark.runtime_integration


@pytest.fixture(scope="module")
def nextflow_image() -> str:
    docker = shutil.which("docker")
    if docker is None or subprocess.run(
        [docker, "image", "inspect", NEXTFLOW_IMAGE],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False,
    ).returncode != 0:
        pytest.skip(f"missing locally installed pinned image {NEXTFLOW_IMAGE}")
    return NEXTFLOW_IMAGE


def _function(source: str, name: str) -> str:
    # Top-level functions end at an unindented closing brace. Do not reproduce
    # their algorithms in the harness: a changed implementation is what runs.
    match = re.search(rf"^def {re.escape(name)}\([^\n]*\) \{{\n.*?^\}}", source, re.M | re.S)
    assert match is not None, f"missing workflow helper {name}"
    return match.group(0)


def _mapping(source: str, start: str) -> str:
    # Both source maps end immediately before this scheduler invocation. The
    # opening closure, body, and closing brace are extracted without rewriting.
    assert source.count(start) == 1
    section = source.split(start, 1)[1]
    closure, separator, _ = section.partition("            SchedulerFrustraMPNNParentFanout(")
    assert separator, "parent mapping/scheduler boundary changed; review harness"
    return closure.rstrip()


def _parent_code(parent: str, *, legacy: bool = False) -> tuple[str, str]:
    source = (REPO_ROOT / "workflows" / f"{parent}.nf").read_text()
    if parent == "structure_prediction":
        helpers = "\n\n".join(_function(source, name) for name in (
            "canonicalJsonValue", "canonicalJsonBytes", "sha256Hex",
            "producerIdentitySha256", "structurePredictionCandidateId",
        ))
        closure = _mapping(
            source,
            "def canonical_candidates = structure_prediction_wf.out.canonical_structures.map ",
        )
        if legacy:
            # Exact pre-repair map mutation: only candidate_id was added here.
            closure, count = re.subn(
                r"^\s*candidate_id: structurePredictionCandidateId\([^\n]+\),\n",
                "", closure, flags=re.M,
            )
            assert count == 1, "legacy mutation no longer targets the repair"
        return helpers, closure
    helpers = _function(source, "parseJsonFile")
    closure = _mapping(source, "scheduler_candidates = deduplicated_candidates.map ")
    if legacy:
        # Exact pre-repair closure; helpers and real downstream process unchanged.
        closure = """{
            candidate_meta, prepared_request, prepared_source, prepared_structure_map ->
            tuple(candidate_meta, prepared_source)
        }"""
    return helpers, closure


def _run(
    root: Path, image: str, *, parent: str, records: list[dict],
    legacy: bool = False, direct_stage: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[dict]]:
    root.mkdir(parents=True, exist_ok=True)
    (root / "source.pdb").write_bytes(FIXTURE_BYTES)
    for index, record in enumerate(records):
        (root / f"request-{index}.json").write_text(json.dumps(record.get("request", {})))
    (root / "records.json").write_text(json.dumps(records))
    if direct_stage:
        helpers, closure = "", "{ meta, source -> tuple(meta, source) }"
    else:
        helpers, closure = _parent_code(parent, legacy=legacy)
    if parent == "complex_prediction" and not direct_stage:
        input_tuple = """tuple(row.meta, file('/run/request-' + index + '.json'),
            file('/run/source.pdb'), file('/run/request-' + index + '.json'))"""
    else:
        input_tuple = "tuple(row.meta, file('/run/source.pdb'))"
    (root / "main.nf").write_text(f"""nextflow.enable.dsl = 2
import groovy.json.JsonOutput
import groovy.json.JsonSlurper
include {{ StageFrustraMPNNParentCandidate }} from '/workspace/modules/frustrampnn_parent_fanout.nf'
{helpers}
workflow {{
    def requiredness = 'required'
    def rows = new JsonSlurper().parse(file('/run/records.json'))
    def inputs = rows.withIndex().collect {{ row, index -> {input_tuple} }}
    candidates = Channel.fromList(inputs).map {closure}
    StageFrustraMPNNParentCandidate(candidates)
    StageFrustraMPNNParentCandidate.out.candidate.view {{ directory ->
        'STAGED_IDENTITY:' + new File(directory.toString(), 'metadata.json').text
    }}
}}
""")
    (root / "nextflow.config").write_text("""params.job_id = 'identity-parent-test'
params.api_python = '/usr/bin/python3'
params.code_root = '/workspace'
process.executor = 'local'
process.cpus = 1
process.maxForks = 2
docker.enabled = false
singularity.enabled = false
""")
    completed = subprocess.run([
        "docker", "run", "--rm", "--network", "none",
        "-e", "NXF_OFFLINE=true", "-e", "NXF_DISABLE_CHECK_LATEST=true",
        "-v", f"{REPO_ROOT}:/workspace:ro", "-v", f"{root}:/run:rw",
        "-w", "/run", image, "nextflow", "run", "/run/main.nf",
        "-c", "/run/nextflow.config", "-offline", "-ansi-log", "false",
        "-w", "/run/work",
    ], text=True, capture_output=True, check=False, timeout=180)
    staged = []
    for metadata in sorted((root / "work").glob("*/*/candidate_*/metadata.json")):
        staged.append(json.loads(metadata.read_text()))
        assert (metadata.parent / "source.pdb").read_bytes() == FIXTURE_BYTES
    return completed, staged


def _output(completed: subprocess.CompletedProcess[str]) -> str:
    return completed.stdout + "\n" + completed.stderr


def _producer_rows() -> list[dict]:
    # Equal artifact names/bytes across predictors and samples must not collapse
    # identities. Only producer metadata distinguishes these transport fixtures.
    return [{"meta": {
        "producer_method": method, "producer_artifact_key": "shared-artifact",
        "producer_sample": sample, "producer_rank": 1,
        "producer_output_key": "predictions/shared/model.pdb",
        "producer_artifact_sha256": "a" * 64, "source_format": "pdb",
    }} for method in ("boltz", "protenix", "rf3", "esmfold2")
        for sample in ("sample-A", "sample-B")]


def _authority(candidate_id: object = "stale-metadata-id") -> dict:
    return {
        "candidate_id": candidate_id, "parent_job_id": "identity-parent-test",
        "parent_workflow_id": "complex_prediction",
        "producer_stage": "complex_prediction:boltz",
        "producer_candidate_key": "frustrampnn/sources/boltz/fixture.pdb",
        "requiredness": "required",
    }


def test_structure_map_stages_canonical_ids_for_predictors_and_samples(
    tmp_path: Path, nextflow_image: str,
) -> None:
    rows = _producer_rows()
    first, staged = _run(tmp_path / "forward", nextflow_image,
                         parent="structure_prediction", records=rows)
    assert first.returncode == 0, _output(first)
    assert len(staged) == len(rows)
    assert len({row["candidate_id"] for row in staged}) == len(rows)
    assert {row["producer_stage"] for row in staged} == {
        "structure_prediction:" + row["meta"]["producer_method"] for row in rows
    }
    for row in staged:
        assert row["parent_workflow_id"] == "structure_prediction"
        assert row["candidate_id"] == deterministic_candidate_id(**{
            key: row[key] for key in (
                "parent_job_id", "parent_workflow_id", "producer_stage", "producer_candidate_key",
            )
        })
    second, reordered = _run(tmp_path / "reverse", nextflow_image,
                             parent="structure_prediction", records=list(reversed(rows)))
    assert second.returncode == 0, _output(second)
    assert sorted(staged, key=lambda row: row["candidate_id"]) == sorted(
        reordered, key=lambda row: row["candidate_id"],
    )


@pytest.mark.parametrize("missing_metadata_id", [False, True], ids=["stale-id", "missing-id"])
def test_complex_map_preserves_prepared_request_id_not_stale_metadata(
    tmp_path: Path, nextflow_image: str, missing_metadata_id: bool,
) -> None:
    # A valid non-UUID identity proves this map preserves rather than recomputes.
    expected = "Prepared.candidate_7-retained"
    metadata = _authority()
    if missing_metadata_id:
        metadata.pop("candidate_id")
    completed, staged = _run(tmp_path, nextflow_image, parent="complex_prediction",
                            records=[{"meta": metadata, "request": {"candidate_id": expected}}])
    assert completed.returncode == 0, _output(completed)
    assert staged == [{**metadata, "candidate_id": expected}]


@pytest.mark.parametrize("parent", ["structure_prediction", "complex_prediction"])
def test_pre_repair_mapping_fails_real_staging_for_missing_candidate_id(
    tmp_path: Path, nextflow_image: str, parent: str,
) -> None:
    metadata = _authority()
    metadata.pop("candidate_id")
    records = (_producer_rows()[:1] if parent == "structure_prediction" else
               [{"meta": metadata, "request": {"candidate_id": "prepared-valid-id"}}])
    completed, staged = _run(tmp_path, nextflow_image, parent=parent,
                            records=records, legacy=True)
    assert completed.returncode != 0, _output(completed)
    assert "terminal candidate authority is invalid" in _output(completed)
    assert staged == []


@pytest.mark.parametrize("candidate_id", [None, "", "../escape", "bad/id", "x" * 129, 42])
def test_complex_map_rejects_malformed_prepared_ids(
    tmp_path: Path, nextflow_image: str, candidate_id: object,
) -> None:
    completed, staged = _run(tmp_path, nextflow_image, parent="complex_prediction",
                            records=[{"meta": _authority(),
                                      "request": {"candidate_id": candidate_id}}])
    assert completed.returncode != 0, _output(completed)
    assert "complex_prediction prepared candidate ID is invalid" in _output(completed)
    assert staged == []


@pytest.mark.parametrize("candidate_id", [None, "../escape", "bad/id", "x" * 129])
def test_real_stage_still_rejects_missing_or_unsafe_ids(
    tmp_path: Path, nextflow_image: str, candidate_id: object,
) -> None:
    completed, staged = _run(tmp_path, nextflow_image, parent="complex_prediction",
                            records=[{"meta": _authority(candidate_id)}], direct_stage=True)
    assert completed.returncode != 0, _output(completed)
    assert "terminal candidate authority is invalid" in _output(completed)
    assert staged == []
