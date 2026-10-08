from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
from services.frustrampnn.contracts import canonical_json_bytes


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "run_frustrampnn_grouped_batch.py"


def _load_grouped_batch():
    assert SCRIPT.is_file(), "production grouped-batch runner is missing"
    spec = importlib.util.spec_from_file_location("run_frustrampnn_grouped_batch_under_test", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical(value: dict[str, object]) -> bytes:
    return canonical_json_bytes(value)


@pytest.mark.parametrize(("adapter_returncode", "fail_all"), [(1, False), (2, True)])
def test_grouped_runner_executes_one_predict_batch_command_and_finalizes_each_terminal_record(
    tmp_path: Path,
    monkeypatch,
    adapter_returncode: int,
    fail_all: bool,
) -> None:
    monkeypatch.chdir(tmp_path)
    grouped = _load_grouped_batch()
    records = []
    for ordinal, candidate in enumerate(("alpha", "beta")):
        request = tmp_path / f"request-{candidate}.json"
        source = tmp_path / f"source-{candidate}.pdb"
        structure_map = tmp_path / f"map-{candidate}.json"
        request.write_bytes(b"{}\n")
        source.write_bytes(f"HEADER {candidate}\nEND\n".encode())
        structure_map.write_bytes(b"{}\n")
        records.append(
            {
                "record_schema_name": "bms_frustrampnn_scheduler_record",
                "record_schema_version": 2,
                "ordinal": ordinal,
                "candidate_id": candidate,
                "invocation_id": f"invoke-{candidate}",
                "request_relative_path": request.name,
                "request_sha256": hashlib.sha256(request.read_bytes()).hexdigest(),
                "request_size_bytes": request.stat().st_size,
                "source_relative_path": source.name,
                "source_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                "source_size_bytes": source.stat().st_size,
                "structure_map_relative_path": structure_map.name,
                "structure_map_sha256": hashlib.sha256(structure_map.read_bytes()).hexdigest(),
                "structure_map_size_bytes": structure_map.stat().st_size,
            }
        )
    manifest = {
        "schema_name": "bms_frustrampnn_scheduler_batch",
        "schema_version": 3,
        "execution_owner_job_id": "job-1",
        "batching_enabled": True,
        "structures_per_job": 2,
        "settings_sha256": "a" * 64,
        "expected_cardinality": 2,
        "records": records,
    }
    manifest_path = tmp_path / "batch.json"
    manifest_path.write_bytes(_canonical(manifest))

    prepared = []
    for record in records:
        prepared.append(
            grouped.PreparedCandidate(
                record=record,
                request_path=tmp_path / record["request_relative_path"],
                source_path=tmp_path / record["source_relative_path"],
                structure_map_path=tmp_path / record["structure_map_relative_path"],
                staged_pdb_path=tmp_path / f"{record['ordinal']:04d}_{record['candidate_id']}.pdb",
            )
        )
        prepared[-1].staged_pdb_path.write_bytes(prepared[-1].source_path.read_bytes())

    execute_calls: list[tuple[str, ...]] = []
    finalized: list[tuple[str, str]] = []

    def prepare_record(record, **_kwargs):
        return prepared[record["ordinal"]]

    def build_command(**_kwargs):
        return SimpleNamespace(argv=("apptainer", "predict_batch"), physical_gpu_id=4, argv_sha256="b" * 64)

    def execute(invocation, _pinned, **_kwargs):
        execute_calls.append(invocation.argv)
        output = tmp_path / "runtime-output"
        output.mkdir(exist_ok=True)
        (output / "0000_alpha.csv").write_text(
            "frustration_pred,position,wildtype,mutation,chain,pdb\n0.1,1,G,A,A,0000_alpha\n"
        )
        evidence = {
            "schema_name": "frustrampnn_batch_terminal_evidence",
            "schema_version": 1,
            "method_identity": "frustrampnn.FrustraMPNN.predict_batch",
            "upstream_sequential_semantics": grouped.UPSTREAM_SEQUENTIAL_SEMANTICS,
            "model_load_count": 1,
            "record_count": 2,
            "records": [
                {
                    "ordinal": 0,
                    "candidate_id": "alpha",
                    "invocation_id": "invoke-alpha",
                    "pdb_stem": "0000_alpha",
                    "source_sha256": records[0]["source_sha256"],
                    "started_at": "2026-08-27T12:00:00Z",
                    "terminal_at": "2026-08-27T12:00:02Z",
                    "status": "failed" if fail_all else "succeeded",
                    "failure_code": "model_load_failed" if fail_all else None,
                    "diagnostic": "FrustraMPNN model could not be loaded" if fail_all else None,
                    "row_count": None if fail_all else 1,
                    "output_csv": None if fail_all else "0000_alpha.csv",
                    "output_sha256": None if fail_all else hashlib.sha256((output / "0000_alpha.csv").read_bytes()).hexdigest(),
                },
                {
                    "ordinal": 1,
                    "candidate_id": "beta",
                    "invocation_id": "invoke-beta",
                    "pdb_stem": "0001_beta",
                    "source_sha256": records[1]["source_sha256"],
                    "started_at": "2026-08-27T12:00:01Z",
                    "terminal_at": "2026-08-27T12:00:03Z",
                    "status": "failed",
                    "failure_code": "model_load_failed" if fail_all else "upstream_output_omitted",
                    "diagnostic": "FrustraMPNN model could not be loaded" if fail_all else "upstream predict_batch returned no rows for this staged PDB",
                    "row_count": None,
                    "output_csv": None,
                    "output_sha256": None,
                },
            ],
        }
        (output / "frustrampnn_batch_terminal_evidence_v1.json").write_bytes(_canonical(evidence))
        return SimpleNamespace(returncode=adapter_returncode)

    def finalize(candidate, terminal, **_kwargs):
        finalized.append((candidate.record["candidate_id"], terminal["status"]))
        return tmp_path / f"bundle-{candidate.record['candidate_id']}"

    job_root = tmp_path / "job-1"
    job_root.mkdir()
    result = grouped.run_grouped_batch(
        batch_manifest_path=manifest_path,
        job_root=job_root,
        container=tmp_path / "frustrampnn.sif",
        physical_gpu_id=4,
        apptainer="apptainer",
        prepare_record=prepare_record,
        build_command=build_command,
        execute=execute,
        finalize_candidate=finalize,
        pinned_container=SimpleNamespace(proc_path=tmp_path / "frustrampnn.sif"),
    )

    assert execute_calls == [("apptainer", "predict_batch")]
    assert finalized == (
        [("alpha", "failed"), ("beta", "failed")]
        if fail_all else [("alpha", "succeeded"), ("beta", "failed")]
    )
    assert result["record_count"] == 2
    assert result["model_load_count"] == 1
    governed = job_root / "frustrampnn" / "batches" / "grouped_batch_terminal_receipt_v1.json"
    assert governed.is_file()
    receipt = json.loads(governed.read_bytes())
    assert receipt["schema_name"] == "bms.frustrampnn.grouped-batch-terminal.v1"
    assert receipt["execution_owner_job_id"] == "job-1"
    assert receipt["batch_manifest"] == {
        "schema_name": "bms_frustrampnn_scheduler_batch",
        "schema_version": 3,
        "sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "size_bytes": manifest_path.stat().st_size,
        "expected_cardinality": 2,
        "ordered_candidate_ids": ["alpha", "beta"],
        "ordered_invocation_ids": ["invoke-alpha", "invoke-beta"],
    }
    assert receipt["records"] == result["records"]
    assert receipt["record_count"] == 2
    assert receipt["content_sha256"] == hashlib.sha256(
        _canonical({key: value for key, value in receipt.items() if key != "content_sha256"})
    ).hexdigest()


def test_grouped_runner_rejects_reordered_or_unbounded_terminal_records() -> None:
    grouped = _load_grouped_batch()
    batch = {
        "execution_owner_job_id": "job-1",
        "records": [
            {"ordinal": 0, "candidate_id": "alpha", "invocation_id": "invoke-alpha", "source_sha256": "a" * 64},
            {"ordinal": 1, "candidate_id": "beta", "invocation_id": "invoke-beta", "source_sha256": "b" * 64},
        ],
    }
    terminal = [
        {
            "ordinal": ordinal,
            "candidate_id": candidate,
            "invocation_id": invocation,
            "pdb_stem": f"000{ordinal}_{candidate}",
            "source_sha256": source,
            "started_at": "2026-08-27T12:00:00Z",
            "terminal_at": "2026-08-27T12:00:01Z",
            "status": "failed",
            "failure_code": "upstream_output_omitted",
            "diagnostic": "bounded diagnostic",
            "row_count": None,
            "output_csv": None,
            "output_sha256": None,
        }
        for ordinal, candidate, invocation, source in (
            (0, "alpha", "invoke-alpha", "a" * 64),
            (1, "beta", "invoke-beta", "b" * 64),
        )
    ]
    evidence = {
        "schema_name": "frustrampnn_batch_terminal_evidence",
        "schema_version": 1,
        "method_identity": "frustrampnn.FrustraMPNN.predict_batch",
        "upstream_sequential_semantics": grouped.UPSTREAM_SEQUENTIAL_SEMANTICS,
        "model_load_count": 1,
        "record_count": 2,
        "records": list(reversed(terminal)),
    }
    with pytest.raises(grouped.GroupedBatchError, match="order"):
        grouped._validated_ordered_terminals(batch, evidence)
    evidence["records"] = terminal
    evidence["records"][0]["diagnostic"] = "x" * 1025
    with pytest.raises(grouped.GroupedBatchError, match="diagnostic"):
        grouped._validated_ordered_terminals(batch, evidence)
