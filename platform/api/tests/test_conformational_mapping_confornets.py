from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent
FIXTURE_ROOT = (
    API_ROOT / "tests" / "fixtures" / "conformational_mapping" / "confornets" / "complete"
)
FINALIZER = REPO_ROOT / "scripts" / "finalize_confornets_conformational_mapping.py"

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

from services.conformational_mapping.contracts import (  # noqa: E402
    candidate_id,
    canonical_sha256,
    validate_schema,
)
from services.conformational_mapping.request_builder import (  # noqa: E402
    ConformationalMappingRequestError,
    build_confornets_coordinate_plan,
    materialize_trusted_internal_request,
)
from services.conformational_mapping.structure_normalizer import (  # noqa: E402
    StructureMapError,
    validate_coordinate_mmcif,
)


def _fixture_settings(**overrides: object) -> dict[str, object]:
    settings: dict[str, object] = {
        "sequence": "MKT",
        "chain_id": "A",
        "task": "mse",
        "test_case_id": "case-a",
        "benchmark_name": "fixture",
        "references": [
            {
                "reference_id": "open",
                "staged_path": "/staged/open.pdb",
                "content_sha256": "1" * 64,
                "state": "open",
                "source": "authenticated_upload",
            },
            {
                "reference_id": "closed",
                "staged_path": "/staged/closed.pdb",
                "content_sha256": "6" * 64,
                "state": "closed",
                "source": "authenticated_upload",
            },
        ],
        "runs": 1,
        "saved_steps": [5, 10],
        "confornet_count": 1,
        "samples": 2,
        "max_steps": 10,
        "num_recycles": 0,
        "num_diffusion_steps": 20,
        "learning_rate": 0.001,
        "gradient_clip": 10.0,
        "skip_msa": True,
        "compute_confidence": True,
        "save_full_confidence": False,
        "compute_evaluation": True,
        "checkpoint": {"path": "/staged/checkpoint.pt", "sha256": "2" * 64},
        "config": None,
        "transfer_source": None,
        "backend_identity": {
            "backend_version": "fixture-1",
            "backend_commit": "3" * 40,
            "runtime_identity": "python3-controlled-confornets",
            "container_digest": "sha256:" + "4" * 64,
            "model_id": "confornets-fixture",
            "feature_identity_sha256": "5" * 64,
            "repo_path": "/fixture/confornets",
        },
    }
    settings.update(overrides)
    return settings


def _run_finalizer(
    tmp_path: Path,
    *,
    fixture_root: Path = FIXTURE_ROOT,
) -> tuple[subprocess.CompletedProcess[str], Path]:
    output = tmp_path / "finalized"
    result = subprocess.run(
        [
            sys.executable,
            str(FINALIZER),
            "--request",
            str(fixture_root / "request.json"),
            "--native-root",
            str(fixture_root / "native"),
            "--out",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result, output


def _copy_fixture(tmp_path: Path) -> Path:
    copied = tmp_path / "fixture"
    shutil.copytree(FIXTURE_ROOT, copied)
    return copied


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_cm4_001_single_chain_only() -> None:
    plan = build_confornets_coordinate_plan(_fixture_settings(), target_id="target-a")
    assert len(plan) == 8

    with pytest.raises(ConformationalMappingRequestError, match="single-chain protein"):
        build_confornets_coordinate_plan(
            _fixture_settings(sequence="MKT:AAA"), target_id="target-a"
        )


def test_cm4_001b_ca_only_conformer_is_not_complete_coordinate_evidence() -> None:
    full = (FIXTURE_ROOT / "native" / "conformers" / "candidate_000.cif").read_text(
        encoding="utf-8"
    )
    ca_only = "\n".join(
        line for line in full.splitlines() if not line.startswith("ATOM ") or " CA " in line
    ).encode("utf-8")
    with pytest.raises(StructureMapError, match="missing required backbone atoms"):
        validate_coordinate_mmcif(ca_only, expected_sequence="MKT", expected_chain_id="A")


def test_cm4_010b_coordinate_validation_rejects_backbone_element_mismatch() -> None:
    source = FIXTURE_ROOT / "native" / "conformers" / "candidate_000.cif"
    lines = source.read_text(encoding="utf-8").splitlines()
    forged = "\n".join(
        line.replace("ATOM 1 N N ", "ATOM 1 C N ").replace(
            "ATOM 4 O O ", "ATOM 4 C O "
        )
        for line in lines
    ).encode("utf-8")
    with pytest.raises(StructureMapError, match="backbone atom/element mismatch"):
        validate_coordinate_mmcif(forged, expected_sequence="MKT", expected_chain_id="A")


def test_cm4_002_at_most_two_references() -> None:
    with pytest.raises(ConformationalMappingRequestError, match="at most two"):
        build_confornets_coordinate_plan(
            _fixture_settings(references=[
                {"reference_id": value, "staged_path": f"/staged/{value}.pdb", "content_sha256": str(index) * 64, "state": value, "source": "authenticated_upload"}
                for index, value in enumerate(("open", "closed", "intermediate"), start=1)
            ]),
            target_id="target-a",
        )


def test_cm4_003_task_dispatch_uses_request_bound_canonical_prep() -> None:
    adapter = (REPO_ROOT / "modules" / "conformational_mapping_confornets.nf").read_text(
        encoding="utf-8"
    )
    workflow = (REPO_ROOT / "workflows" / "conformational_mapping.nf").read_text(
        encoding="utf-8"
    )

    assert "from './confornets_experimental.nf'" in adapter
    assert "PrepCanonicalConforNetsRequest" in adapter
    for process_name in (
        "RunCanonicalConforNets",
        "FinalizeConforNetsOutputs",
        "BindCanonicalConforNetsOutputLedger",
    ):
        assert process_name in adapter
    assert "PrepConforNetsRequest()" not in adapter
    assert adapter.index("PrepCanonicalConforNetsRequest(") < adapter.index(
        "RunCanonicalConforNets("
    )
    assert adapter.index("RunCanonicalConforNets(") < adapter.index(
        "FinalizeConforNetsOutputs("
    )
    assert "label 'local_cpu'" in adapter
    assert "finalize_confornets_conformational_mapping.py" in adapter
    assert "CONFORMATIONAL_MAPPING_CONFORNETS" in workflow
    assert "CONFORMATIONAL_MAPPING_PROTENIX" in workflow
    assert "CONFORMATIONAL_MAPPING_IMPORT" in workflow
    assert "CONFORMATIONAL_MAPPING_CONFORNETS" in workflow
    assert "_UNIMPLEMENTED" not in workflow


def test_cm4_003b_canonical_prep_requires_installed_instrumented_runtime(
    tmp_path: Path,
) -> None:
    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "prep_canonical_confornets_request.py"),
            "--request",
            str(FIXTURE_ROOT / "request.json"),
            "--coordinate-plan",
            str(FIXTURE_ROOT / "cm_coordinate_plan_v1.json"),
            "--assets-dir",
            str(tmp_path / "assets"),
            "--output",
            str(tmp_path / "native_request.json"),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "instrumented confornets runtime is unavailable" in result.stderr.lower()
    assert not (tmp_path / "assets").exists()
    assert not (tmp_path / "native_request.json").exists()


@pytest.mark.parametrize("probe", ["symlink", "traversal", "arbitrary_repo"])
def test_cm4_003c_internal_prep_rejects_unowned_or_escaped_paths(
    tmp_path: Path, probe: str
) -> None:
    copied = _copy_fixture(tmp_path / probe)
    request_path = copied / "request.json"
    plan_path = copied / "cm_coordinate_plan_v1.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    if probe == "symlink":
        link = copied / "inputs" / "checkpoint-link.pt"
        link.symlink_to("/etc/passwd")
        request["confornets"]["checkpoint"] = {
            "path": "inputs/checkpoint-link.pt",
            "sha256": _sha256(Path("/etc/passwd")),
        }
    elif probe == "traversal":
        request["confornets"]["checkpoint"]["path"] = (
            "inputs/../inputs/checkpoint.pt"
        )
    else:
        request["confornets"]["backend_identity"]["repo_path"] = "/etc"
    request["request_sha256"] = canonical_sha256(
        {key: value for key, value in request.items() if key != "request_sha256"}
    )
    request_path.write_text(json.dumps(request), encoding="utf-8")
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["request_sha256"] = request["request_sha256"]
    plan["coordinate_plan_sha256"] = canonical_sha256(
        {key: value for key, value in plan.items() if key != "coordinate_plan_sha256"}
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    assets = tmp_path / f"{probe}-assets"
    output = tmp_path / f"{probe}-native.json"

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "prep_canonical_confornets_request.py"),
            "--request",
            str(request_path),
            "--coordinate-plan",
            str(plan_path),
            "--assets-dir",
            str(assets),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert any(
        message in result.stderr.lower()
        for message in ("symlink", "escape", "approved", "instrumented confornets runtime")
    )
    assert not assets.exists()
    assert not output.exists()


def test_cm4_003d_missing_canonical_runtime_stops_before_prep(
    tmp_path: Path,
) -> None:
    copied = _copy_fixture(tmp_path)
    request_path = copied / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["source"]["kind"] = "server_authenticated_internal_v1"
    request["request_sha256"] = canonical_sha256(
        {key: value for key, value in request.items() if key != "request_sha256"}
    )
    request_path.write_text(json.dumps(request), encoding="utf-8")
    plan_path = copied / "cm_coordinate_plan_v1.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["request_sha256"] = request["request_sha256"]
    plan["coordinate_plan_sha256"] = canonical_sha256(
        {key: value for key, value in plan.items() if key != "coordinate_plan_sha256"}
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    assets = tmp_path / "assets"
    output = tmp_path / "native.json"

    result = subprocess.run(
        [
            sys.executable,
            str(REPO_ROOT / "scripts" / "prep_canonical_confornets_request.py"),
            "--request",
            str(request_path),
            "--coordinate-plan",
            str(plan_path),
            "--assets-dir",
            str(assets),
            "--output",
            str(output),
        ],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode != 0
    assert "instrumented confornets runtime is unavailable" in result.stderr.lower()
    assert not assets.exists()
    assert not output.exists()


def test_cm4_004_full_coordinate_identity(tmp_path: Path) -> None:
    result, output = _run_finalizer(tmp_path)
    assert result.returncode == 0, result.stderr
    ensemble = json.loads((output / "cm_ensemble_v1.json").read_text(encoding="utf-8"))

    assert len(ensemble["candidates"]) == 8
    for record in ensemble["candidates"]:
        coordinates = record["backend_coordinates"]
        assert set(coordinates) == {
            "backend",
            "target_id",
            "task",
            "test_case_id",
            "reference_id",
            "run_index",
            "saved_step",
            "confornet_index",
            "sample_index",
        }
        assert record["candidate_id"] == candidate_id(coordinates)
    assert len({row["candidate_id"] for row in ensemble["candidates"]}) == 8


def test_cm4_005_dimension_formula(tmp_path: Path) -> None:
    result, output = _run_finalizer(tmp_path)
    assert result.returncode == 0, result.stderr
    ensemble = json.loads((output / "cm_ensemble_v1.json").read_text(encoding="utf-8"))

    # Two target/task/test/reference groups, each 1 run * 2 steps * 1 net * 2 samples.
    assert ensemble["expected_cardinality"] == 2 * (1 * 2 * 1 * 2)
    assert len(ensemble["expected_coordinates"]) == ensemble["expected_cardinality"]


def test_cm4_006_missing_or_extra_coordinate_fails(tmp_path: Path) -> None:
    missing = _copy_fixture(tmp_path / "missing")
    (missing / "native" / "conformers" / "candidate_007.cif").unlink()
    result, _ = _run_finalizer(tmp_path / "missing-run", fixture_root=missing)
    assert result.returncode != 0
    assert "missing" in result.stderr.lower()

    extra = _copy_fixture(tmp_path / "extra")
    (extra / "native" / "conformers" / "unexpected.cif").write_bytes(b"data_extra\n")
    result, _ = _run_finalizer(tmp_path / "extra-run", fixture_root=extra)
    assert result.returncode != 0
    assert "extra" in result.stderr.lower()

    shared = _copy_fixture(tmp_path / "shared")
    samples_path = shared / "native" / "samples.json"
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    samples[1]["relative_path"] = samples[0]["relative_path"]
    samples_path.write_text(json.dumps(samples), encoding="utf-8")
    result, _ = _run_finalizer(tmp_path / "shared-run", fixture_root=shared)
    assert result.returncode != 0
    assert "shared" in result.stderr.lower() or "duplicate" in result.stderr.lower()

    escaped = _copy_fixture(tmp_path / "escaped")
    samples_path = escaped / "native" / "samples.json"
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    samples[0]["relative_path"] = "../candidate_000.cif"
    samples_path.write_text(json.dumps(samples), encoding="utf-8")
    result, _ = _run_finalizer(tmp_path / "escaped-run", fixture_root=escaped)
    assert result.returncode != 0
    assert "path escape" in result.stderr.lower()

    unreferenced = _copy_fixture(tmp_path / "unreferenced")
    (unreferenced / "native" / "undeclared.bin").write_bytes(b"undeclared")
    result, _ = _run_finalizer(tmp_path / "unreferenced-run", fixture_root=unreferenced)
    assert result.returncode != 0
    assert "unreferenced" in result.stderr.lower()

    collision = _copy_fixture(tmp_path / "collision")
    (collision / "native" / "raw" / "request.json").write_bytes(b"{}")
    result, _ = _run_finalizer(tmp_path / "collision-run", fixture_root=collision)
    assert result.returncode != 0
    assert "basename collision" in result.stderr.lower()


def test_cm4_007_placeholder_bfactor_not_confidence(tmp_path: Path) -> None:
    result, output = _run_finalizer(tmp_path)
    assert result.returncode == 0, result.stderr
    ensemble = json.loads((output / "cm_ensemble_v1.json").read_text(encoding="utf-8"))

    for candidate in ensemble["candidates"]:
        sidecars = [output / path for path in candidate["sidecar_paths"]]
        confidence = json.loads(
            next(path for path in sidecars if path.name.endswith("confidence.json")).read_text(
                encoding="utf-8"
            )
        )
        evaluation = json.loads(
            next(path for path in sidecars if path.name.endswith("evaluation.json")).read_text(
                encoding="utf-8"
            )
        )
        assert confidence["status"] in {"computed", "not_computed", "requested_missing"}
        assert evaluation["status"] in {"computed", "not_computed", "requested_missing"}
        assert confidence["status"] == "computed"
        assert confidence["metrics"]["plddt"] == 88.0
        assert confidence["metrics"]["plddt"] != 50.0
        assert confidence["authenticated_source_path"] == "native/confidence/confidence_summary.json"


def test_cm4_008_native_manifest_complete(tmp_path: Path) -> None:
    result, output = _run_finalizer(tmp_path)
    assert result.returncode == 0, result.stderr
    native_manifest = json.loads(
        (output / "cm_native_artifacts_v1.json").read_text(encoding="utf-8")
    )
    ensemble = json.loads((output / "cm_ensemble_v1.json").read_text(encoding="utf-8"))
    validate_schema("cm_native_artifacts_v1", native_manifest)
    validate_schema("cm_ensemble_v1", ensemble)

    fixture_files = {
        path.relative_to(FIXTURE_ROOT / "native").as_posix(): path
        for path in (FIXTURE_ROOT / "native").rglob("*")
        if path.is_file()
    }
    copied_files = {
        path.relative_to(output / "native").as_posix(): path
        for path in (output / "native").rglob("*")
        if path.is_file()
    }
    assert set(copied_files) == set(fixture_files)
    for relative_path, source in fixture_files.items():
        assert copied_files[relative_path].read_bytes() == source.read_bytes()

    manifested = {record["relative_path"] for record in native_manifest["files"]}
    observed = {
        path.relative_to(output).as_posix()
        for path in output.rglob("*")
        if path.is_file() and path.name not in {"cm_native_artifacts_v1.json", "cm_ensemble_v1.json"}
    }
    assert manifested == observed
    for record in native_manifest["files"]:
        artifact = output / record["relative_path"]
        assert record["sha256"] == _sha256(artifact)
        assert record["bytes"] == artifact.stat().st_size


def test_cm4_009_legacy_artifacts_semantically_preserved(tmp_path: Path) -> None:
    protected = [
        REPO_ROOT / "modules" / "confornets_experimental.nf",
        REPO_ROOT / "workflows" / "confornets_experimental.nf",
        REPO_ROOT / "scripts" / "prep_confornets_request.py",
        REPO_ROOT / "scripts" / "run_confornets_inference.py",
    ]
    before = {path: _sha256(path) for path in protected}
    result, output = _run_finalizer(tmp_path)
    assert result.returncode == 0, result.stderr
    assert {path: _sha256(path) for path in protected} == before

    for relative_path in ("artifact_manifest.json", "samples.json", "ensemble_manifest.json"):
        assert json.loads((output / "native" / relative_path).read_text(encoding="utf-8")) == json.loads(
            (FIXTURE_ROOT / "native" / relative_path).read_text(encoding="utf-8")
        )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [
        ("sequence", "sequence"),
        ("task", "task"),
        ("test_case", "test case"),
        ("references", "reference"),
        ("dimensions", "settings"),
    ],
)
def test_cm4_010_native_request_mutations_fail_closed(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    copied = _copy_fixture(tmp_path)
    native_path = copied / "native" / "request.json"
    native = json.loads(native_path.read_text(encoding="utf-8"))
    if mutation == "sequence":
        native["sequence"] = "AAA"
    elif mutation == "task":
        native["task"] = "transfer"
    elif mutation == "test_case":
        native["test_case"] = "forged-case"
    elif mutation == "references":
        native["references"][0]["name"] = "closed"
    else:
        native["params"]["num_samples"] += 1
    native_path.write_text(json.dumps(native), encoding="utf-8")
    result, _ = _run_finalizer(tmp_path / "run", fixture_root=copied)
    assert result.returncode != 0
    assert expected in result.stderr.lower()


def test_cm4_011_coordinate_plan_mutation_fails_hash_binding(tmp_path: Path) -> None:
    copied = _copy_fixture(tmp_path)
    plan_path = copied / "cm_coordinate_plan_v1.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["coordinates"][0]["task"] = "transfer"
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    result, _ = _run_finalizer(tmp_path / "run", fixture_root=copied)
    assert result.returncode != 0
    assert "coordinate plan" in result.stderr.lower()


def test_cm4_012_output_ledger_rejects_mutated_bytes_and_aliases(tmp_path: Path) -> None:
    mutated = _copy_fixture(tmp_path / "mutated")
    sample = mutated / "native" / "conformers" / "candidate_000.cif"
    sample.write_bytes(sample.read_bytes() + b"# mutation\n")
    result, _ = _run_finalizer(tmp_path / "mutated-run", fixture_root=mutated)
    assert result.returncode != 0
    assert "ledger" in result.stderr.lower() or "sha256" in result.stderr.lower()

    alias = _copy_fixture(tmp_path / "alias")
    ledger_path = alias / "native" / "cm_output_coordinate_ledger_v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["entries"][1]["relative_path"] = ledger["entries"][0]["relative_path"]
    ledger["entries"][1]["sha256"] = ledger["entries"][0]["sha256"]
    ledger["entries"][1]["bytes"] = ledger["entries"][0]["bytes"]
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    result, _ = _run_finalizer(tmp_path / "alias-run", fixture_root=alias)
    assert result.returncode != 0
    assert "shared" in result.stderr.lower() or "duplicate" in result.stderr.lower()


@pytest.mark.parametrize(
    "payload",
    [
        b"this is not mmcif\n",
        b"",
        (
            b"data_nonfinite\nloop_\n"
            b"_atom_site.group_PDB\n_atom_site.id\n_atom_site.type_symbol\n"
            b"_atom_site.label_atom_id\n_atom_site.label_alt_id\n_atom_site.label_comp_id\n"
            b"_atom_site.label_asym_id\n_atom_site.label_entity_id\n_atom_site.label_seq_id\n"
            b"_atom_site.pdbx_PDB_ins_code\n_atom_site.Cartn_x\n_atom_site.Cartn_y\n"
            b"_atom_site.Cartn_z\n_atom_site.occupancy\n_atom_site.B_iso_or_equiv\n"
            b"_atom_site.auth_seq_id\n_atom_site.auth_comp_id\n_atom_site.auth_asym_id\n"
            b"_atom_site.auth_atom_id\n_atom_site.pdbx_PDB_model_num\n"
            b"ATOM 1 C CA . MET A 1 1 ? nan 0 0 1 50 1 MET A CA 1\n"
        ),
    ],
)
def test_cm4_012b_coordinate_content_is_parsed_after_hash_binding(
    tmp_path: Path, payload: bytes
) -> None:
    copied = _copy_fixture(tmp_path / "content")
    relative = "conformers/candidate_000.cif"
    coordinate = copied / "native" / relative
    coordinate.write_bytes(payload)
    digest = _sha256(coordinate)

    samples_path = copied / "native" / "samples.json"
    samples = json.loads(samples_path.read_text(encoding="utf-8"))
    sample = next(row for row in samples if row["relative_path"] == relative)
    sample.update(bytes=len(payload), sha256=digest)
    samples_path.write_text(json.dumps(samples), encoding="utf-8")

    ledger_path = copied / "native" / "cm_output_coordinate_ledger_v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    entry = next(row for row in ledger["entries"] if row["relative_path"] == relative)
    entry.update(bytes=len(payload), sha256=digest)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")

    result, _ = _run_finalizer(tmp_path / "content-run", fixture_root=copied)
    assert result.returncode != 0
    assert "coordinate mmcif" in result.stderr.lower()


def test_cm4_013_caller_identity_strings_without_receipt_are_quarantined(
    tmp_path: Path,
) -> None:
    result, output = _run_finalizer(tmp_path)
    assert result.returncode == 0, result.stderr
    ensemble = json.loads((output / "cm_ensemble_v1.json").read_text(encoding="utf-8"))
    assert ensemble["terminal_status"] == "quarantined"
    assert ensemble["resumable"] is False
    assert ensemble["resume_descriptor"] is None
    assert ensemble["resume_key"] == "0" * 64
    assert "execution receipt" in ensemble["omissions"][0]


def test_cm4_014_unknown_zero_runtime_identity_quarantines_resume(tmp_path: Path) -> None:
    copied = _copy_fixture(tmp_path)
    request_path = copied / "request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["confornets"]["backend_identity"]["container_digest"] = "sha256:" + "0" * 64
    request["confornets"]["checkpoint"]["sha256"] = "0" * 64
    request["request_sha256"] = canonical_sha256(
        {key: value for key, value in request.items() if key != "request_sha256"}
    )
    request_path.write_text(json.dumps(request), encoding="utf-8")
    plan_path = copied / "cm_coordinate_plan_v1.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    plan["request_sha256"] = request["request_sha256"]
    plan["coordinate_plan_sha256"] = canonical_sha256(
        {key: value for key, value in plan.items() if key != "coordinate_plan_sha256"}
    )
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    native_request_path = copied / "native" / "request.json"
    native_request = json.loads(native_request_path.read_text(encoding="utf-8"))
    native_request["backend_identity"] = request["confornets"]["backend_identity"]
    native_request["canonical_binding"] = {
        "request_sha256": request["request_sha256"],
        "coordinate_plan_sha256": plan["coordinate_plan_sha256"],
        "target_id": request["targets"][0]["target_id"],
    }
    native_request_path.write_text(json.dumps(native_request), encoding="utf-8")
    ledger_path = copied / "native" / "cm_output_coordinate_ledger_v1.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))
    ledger["request_sha256"] = request["request_sha256"]
    ledger["coordinate_plan_sha256"] = plan["coordinate_plan_sha256"]
    ledger["native_request_sha256"] = _sha256(native_request_path)
    ledger_path.write_text(json.dumps(ledger), encoding="utf-8")
    sidecars_path = copied / "native" / "authenticated_sidecars.json"
    sidecars = json.loads(sidecars_path.read_text(encoding="utf-8"))
    sidecars["request_sha256"] = request["request_sha256"]
    sidecars_path.write_text(json.dumps(sidecars), encoding="utf-8")
    result, output = _run_finalizer(tmp_path / "run", fixture_root=copied)
    assert result.returncode == 0, result.stderr
    ensemble = json.loads((output / "cm_ensemble_v1.json").read_text(encoding="utf-8"))
    assert ensemble["resumable"] is False
    assert ensemble["resume_descriptor"] is None
    assert ensemble["terminal_status"] == "quarantined"


def test_cm4_015_behavioral_disposable_nextflow_probe(tmp_path: Path) -> None:
    if os.environ.get("BMS_RUN_CM_NEXTFLOW_PROBE") != "1":
        pytest.skip("set BMS_RUN_CM_NEXTFLOW_PROBE=1 for the disposable runtime probe")
    nextflow_bin = Path("/home/dalab/.local/lib/nextflow/25.10.1/nextflow")
    if not nextflow_bin.is_file():
        pytest.skip("managed Nextflow is unavailable")

    controlled_repo = tmp_path / "controlled_confornets"
    (controlled_repo / "scripts").mkdir(parents=True)
    (controlled_repo / "preprocess.py").write_text(
        "import argparse\np=argparse.ArgumentParser(); p.add_argument('--benchmark'); p.add_argument('--assets-dir'); p.add_argument('--skip-msa', action='store_true'); p.parse_args()\n",
        encoding="utf-8",
    )
    controlled_run = '''import argparse, json
from pathlib import Path
p=argparse.ArgumentParser(add_help=False)
p.add_argument('--output-dir', required=True)
p.add_argument('--assets-dir', required=True)
args, _ = p.parse_known_args()
out=Path(args.output_dir); out.mkdir(parents=True, exist_ok=True)
context=json.loads((Path(args.assets_dir)/'canonical_execution_context.json').read_text())
entries=[]
for index, coordinate in enumerate(context['coordinates']):
    name=f'explicit_{index:05d}.cif'
    cif=f"""data_probe_{index}
loop_
_atom_site.group_PDB
_atom_site.id
_atom_site.type_symbol
_atom_site.label_atom_id
_atom_site.label_alt_id
_atom_site.label_comp_id
_atom_site.label_asym_id
_atom_site.label_entity_id
_atom_site.label_seq_id
_atom_site.pdbx_PDB_ins_code
_atom_site.Cartn_x
_atom_site.Cartn_y
_atom_site.Cartn_z
_atom_site.occupancy
_atom_site.B_iso_or_equiv
_atom_site.auth_seq_id
_atom_site.auth_comp_id
_atom_site.auth_asym_id
_atom_site.auth_atom_id
_atom_site.pdbx_PDB_model_num
ATOM 1 N N . MET A 1 1 ? 0.000 {index}.0 0.000 1.00 50.00 1 MET A N 1
ATOM 2 C CA . MET A 1 1 ? 1.450 {index}.1 0.000 1.00 50.00 1 MET A CA 1
ATOM 3 C C . MET A 1 1 ? 2.900 {index}.0 0.000 1.00 50.00 1 MET A C 1
ATOM 4 O O . MET A 1 1 ? 3.500 {index}.0 0.000 1.00 50.00 1 MET A O 1
ATOM 5 N N . LYS A 1 2 ? 3.800 {index}.0 0.000 1.00 50.00 2 LYS A N 1
ATOM 6 C CA . LYS A 1 2 ? 5.250 {index}.1 0.000 1.00 50.00 2 LYS A CA 1
ATOM 7 C C . LYS A 1 2 ? 6.700 {index}.0 0.000 1.00 50.00 2 LYS A C 1
ATOM 8 O O . LYS A 1 2 ? 7.300 {index}.0 0.000 1.00 50.00 2 LYS A O 1
ATOM 9 N N . THR A 1 3 ? 7.600 {index}.0 0.000 1.00 50.00 3 THR A N 1
ATOM 10 C CA . THR A 1 3 ? 9.050 {index}.1 0.000 1.00 50.00 3 THR A CA 1
ATOM 11 C C . THR A 1 3 ? 10.500 {index}.0 0.000 1.00 50.00 3 THR A C 1
ATOM 12 O O . THR A 1 3 ? 11.100 {index}.0 0.000 1.00 50.00 3 THR A O 1
#
"""
    (out/name).write_text(cif)
    entries.append({'coordinates': coordinate, 'source_relative_path': name})
(out/'state.pt').write_bytes(b'controlled contract state')
(out/'loss.csv').write_text('step,loss\\n1,0.5\\n')
(out/'cm_upstream_coordinate_ledger_v1.json').write_text(json.dumps({'request_sha256':context['request_sha256'],'coordinate_plan_sha256':context['coordinate_plan_sha256'],'entries':entries}))
'''
    (controlled_repo / "scripts" / "run_mse_training.py").write_text(
        controlled_run, encoding="utf-8"
    )
    reference = tmp_path / "reference.pdb"
    reference.write_text(
        "ATOM      1  CA  MET A   1      12.000  12.000  13.000  1.00 20.00           C\nEND\n",
        encoding="utf-8",
    )
    checkpoint = tmp_path / "checkpoint.pt"
    checkpoint.write_bytes(b"controlled contract checkpoint")
    settings = {
        "sequence": "MKT",
        "chain_id": "A",
        "task": "mse",
        "test_case_id": "probe-case",
        "benchmark_name": "probe-benchmark",
        "references": [{
            "reference_id": "probe-ref",
            "staged_path": str(reference),
            "content_sha256": _sha256(reference),
            "state": "open",
            "source": "controlled_fixture",
        }],
        "runs": 1,
        "saved_steps": [1, 2],
        "confornet_count": 1,
        "samples": 2,
        "max_steps": 2,
        "num_recycles": 0,
        "num_diffusion_steps": 2,
        "learning_rate": 0.001,
        "gradient_clip": 10.0,
        "skip_msa": True,
        "compute_confidence": False,
        "save_full_confidence": False,
        "compute_evaluation": False,
        "checkpoint": {"path": str(checkpoint), "sha256": _sha256(checkpoint)},
        "config": None,
        "transfer_source": None,
        "backend_identity": {
            "backend_version": "controlled-probe-v1",
            "backend_commit": hashlib.sha1(controlled_run.encode()).hexdigest(),
            "runtime_identity": f"nextflow-25.10.1+python-{sys.version_info.major}.{sys.version_info.minor}",
            # The controlled host runtime is deliberately not represented as a
            # container.  An unknown container identity must quarantine resume.
            "container_digest": "sha256:" + ("0" * 64),
            "model_id": "controlled-confornets",
            "feature_identity_sha256": hashlib.sha256(controlled_run.encode()).hexdigest(),
            "repo_path": str(controlled_repo),
        },
    }
    params = {
        "backend": "confornets",
        "targets": [{"target_id": "probe-target", "target_order": 0, "sequence": "MKT", "molecule_type": "protein", "chain_count": 1}],
        "ordered_seeds": [17],
        "samples_per_seed": 2,
        "feature_policy": {"mode": "features_disabled_control_v1"},
        "runtime_policy": {"use_default_params": True},
        "analysis_policy": {
            "sign_zero_epsilon": 0.000001,
            "clash_detector_id": "bms_clash",
            "clash_detector_version": "1",
            "outer_support_minimum": 0.8,
            "inner_support_minimum": 0.6,
            "sign_consistency_minimum": 0.8,
            "clash_free_minimum": 0.9,
            "rank_stability_minimum": 0.6,
            "minimum_common_ranked_universe_size": 3,
        },
        "confornets": settings,
    }
    request_root = tmp_path / "request"
    request_root.mkdir()
    shutil.copytree(controlled_repo, request_root / "controlled_confornets")
    (request_root / "inputs").mkdir()
    shutil.copy2(reference, request_root / "inputs" / "reference.pdb")
    shutil.copy2(checkpoint, request_root / "inputs" / "checkpoint.pt")
    settings["references"][0]["staged_path"] = "inputs/reference.pdb"
    settings["checkpoint"]["path"] = "inputs/checkpoint.pt"
    settings["backend_identity"]["repo_path"] = "controlled_confornets"
    materialized = materialize_trusted_internal_request(
        params,
        output_dir=request_root,
        request_id="00000000-0000-4000-8000-000000000415",
        principal_id="controlled-nextflow-probe",
        source_kind="disposable_controlled_probe_v1",
    )
    out_dir = tmp_path / "out"
    work_dir = tmp_path / "work"
    env = os.environ.copy()
    shell_wrapper = tmp_path / "probe_shell.py"
    shell_wrapper.write_text(
        "#!/usr/bin/env python3\n"
        "import os, sys\n"
        "from pathlib import Path\n"
        "source=Path(sys.argv[-1])\n"
        "target=source.with_name(source.name+'.controlled-probe')\n"
        f"text=source.read_text().replace('/scripts/run_confornets_inference.py', {str(REPO_ROOT / 'scripts' / 'run_confornets_inference.py')!r})\n"
        "target.write_text(text)\n"
        "os.execv('/bin/bash', ['/bin/bash', *sys.argv[1:-1], str(target)])\n",
        encoding="utf-8",
    )
    shell_wrapper.chmod(0o755)
    probe_config = tmp_path / "controlled_probe.config"
    probe_config.write_text(
        "apptainer.enabled = false\n"
        "singularity.enabled = false\n"
        "docker.enabled = false\n"
        "process {\n"
        f"  shell = ['{shell_wrapper}', '-ue']\n"
        "  withLabel: ConforNets { container = null; containerOptions = null }\n"
        "}\n",
        encoding="utf-8",
    )
    result = subprocess.run(
        [
            str(nextflow_bin), "run", str(REPO_ROOT / "workflows" / "conformational_mapping.nf"),
            "-c", str(probe_config),
            "-profile", "conformational_mapping,workstation_ryzen7960x",
            "-w", str(work_dir),
            "--out_dir", str(out_dir),
            "--cm_request_path", str(materialized.request_path),
        ],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
        timeout=300,
    )
    (tmp_path / "nextflow.stdout.log").write_text(result.stdout, encoding="utf-8")
    (tmp_path / "nextflow.stderr.log").write_text(result.stderr, encoding="utf-8")
    assert result.returncode == 0, result.stdout + result.stderr
    manifests = list(out_dir.rglob("cm_ensemble_v1.json"))
    assert len(manifests) == 1
    ensemble = json.loads(manifests[0].read_text(encoding="utf-8"))
    assert ensemble["terminal_status"] == "quarantined"
    assert ensemble["resumable"] is False
    assert ensemble["resume_descriptor"] is None
    assert ensemble["resume_key"] == "0" * 64
    assert len(ensemble["candidates"]) == 4
    native_request = next(work_dir.rglob("confornets_request.json"))
    native = json.loads(native_request.read_text(encoding="utf-8"))
    assert native["task"] == "mse"
    assert native["sequence"] == "MKT"
    assert [row["name"] for row in native["references"]] == ["probe-ref"]
    assert native["params"]["num_runs"] == 1
    assert native["params"]["save_steps"] == [1, 2]
    assert native["params"]["k_confornets"] == 1
    assert native["params"]["num_samples"] == 2
