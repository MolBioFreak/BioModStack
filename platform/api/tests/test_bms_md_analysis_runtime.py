from __future__ import annotations

import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import scripts.bms_md.analysis as analysis_module
import scripts.bms_md.contract as contract_module
import scripts.bms_md.promote_analysis_runtime as promotion_module
from scripts.bms_md.analysis_runtime_probe import (
    FixtureContractError,
    build_apptainer_probe_command,
    load_fixture_catalog,
    qualify_fixture_catalog,
)
from scripts.bms_md.promote_analysis_runtime import PromotionError, _load_probe_evidence, promote_runtime


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_catalog(root: Path, *, artifact_path: str = "gromacs/system.gro", corrupt_hash: bool = False) -> Path:
    artifact = root / "gromacs" / "system.gro"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("fixture\n", encoding="utf-8")
    digest = "0" * 64 if corrupt_hash else _sha256(artifact)
    documentation = root / "README.md"
    license_path = root / "SOURCE_LICENSE.txt"
    documentation.write_text("fixture provenance\n", encoding="utf-8")
    license_path.write_text("fixture license\n", encoding="utf-8")
    catalog = {
        "schema": "bms.md.analysis-fixtures.v1",
        "provenance": {
            "package": {
                "canonical_release_url": "https://conda.anaconda.org/conda-forge/noarch/test-fixture.conda",
                "archive_sha256": "1" * 64,
            },
            "source_files": {"source/member": "2" * 64},
            "documentation": {"path": "README.md", "sha256": _sha256(documentation)},
            "license": {"path": "SOURCE_LICENSE.txt", "sha256": _sha256(license_path)},
        },
        "fixtures": [
            {
                "id": "gromacs-format-smoke",
                "format_pair": "gro_xtc",
                "manifest": "gromacs/manifest.json",
                "artifacts": {
                    "topology": {"path": artifact_path, "bytes": artifact.stat().st_size, "sha256": digest}
                },
            }
        ],
    }
    path = root / "fixtures.json"
    path.write_text(json.dumps(catalog), encoding="utf-8")
    return path


def _write_complete_probe_evidence(path: Path, *, candidate: Path, fixtures: Path) -> Path:
    catalog = json.loads((fixtures / "fixtures.json").read_text(encoding="utf-8"))
    fixture_results = []
    for fixture in catalog["fixtures"]:
        manifest = fixtures / fixture["manifest"]
        outputs = []
        for name, role, content in (
            ("report.json", "md_analysis_report", b"r"),
            ("timeseries.parquet", "md_analysis_timeseries", b"t"),
            ("residue-metrics.parquet", "md_analysis_residue_metrics", b"m"),
        ):
            output = path.parent / "qualified" / fixture["id"] / name
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_bytes(content)
            outputs.append(
                {
                    "name": name,
                    "path": output.relative_to(path.parent).as_posix(),
                    "bytes": output.stat().st_size,
                    "sha256": _sha256(output),
                    "semantic_role": role,
                }
            )
        fixture_results.append(
            {
                "id": fixture["id"],
                "format_pair": fixture["format_pair"],
                "status": "passed",
                "input_manifest_sha256": _sha256(manifest),
                "outputs": outputs,
            }
        )
    path.write_text(
        json.dumps(
            {
                "schema": "bms.md.analysis-runtime-qualification.v1",
                "status": "passed",
                "runtime_sif_sha256": _sha256(candidate),
                "implementation_sha256": analysis_module._implementation_sha256(),
                "fixture_catalog_sha256": _sha256(fixtures / "fixtures.json"),
                "fixtures": fixture_results,
            }
        ),
        encoding="utf-8",
    )
    return path


def test_fixture_catalog_rejects_path_escape_before_open(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_catalog(fixtures, artifact_path="../outside.gro")

    with pytest.raises(FixtureContractError, match="contained relative"):
        load_fixture_catalog(fixtures)


def test_fixture_catalog_rejects_hash_mismatch(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    _write_catalog(fixtures, corrupt_hash=True)

    with pytest.raises(FixtureContractError, match="SHA-256"):
        load_fixture_catalog(fixtures)


def test_fixture_catalog_rejects_missing_provenance(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    catalog_path = _write_catalog(fixtures)
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog.pop("provenance")
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(FixtureContractError, match="provenance"):
        load_fixture_catalog(fixtures)


def test_fixture_catalog_rejects_provenance_file_hash_mismatch(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    catalog_path = _write_catalog(fixtures)
    (fixtures / "README.md").write_text("documented\n", encoding="utf-8")
    (fixtures / "SOURCE_LICENSE.txt").write_text("licensed\n", encoding="utf-8")
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    catalog["provenance"] = {
        "documentation": {"path": "README.md", "sha256": "0" * 64},
        "license": {"path": "SOURCE_LICENSE.txt", "sha256": _sha256(fixtures / "SOURCE_LICENSE.txt")},
    }
    catalog_path.write_text(json.dumps(catalog), encoding="utf-8")

    with pytest.raises(FixtureContractError, match="provenance"):
        load_fixture_catalog(fixtures)


def test_promotion_rejects_forged_minimal_qualification_evidence(tmp_path: Path) -> None:
    fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "bms_md_analysis"
    forged = tmp_path / "forged.json"
    forged.write_text(
        json.dumps(
            {
                "schema": "bms.md.analysis-runtime-qualification.v1",
                "status": "passed",
                "runtime_sif_sha256": "a" * 64,
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(PromotionError, match="complete|fixture|qualification"):
        _load_probe_evidence(
            forged,
            expected_runtime_sha256="a" * 64,
            fixtures=fixtures,
        )


def test_apptainer_probe_command_is_read_only_and_recursion_guarded(tmp_path: Path) -> None:
    image = tmp_path / "runtime.sif"
    image.write_bytes(b"sif")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    output = tmp_path / "evidence.json"
    repo = tmp_path / "repo"
    repo.mkdir()

    command = build_apptainer_probe_command(
        image=image,
        fixtures=fixtures,
        output=output,
        repo_root=repo,
    )

    assert command[:3] == ["apptainer", "exec", "--cleanenv"]
    assert "--containall" in command
    assert f"{repo}:/opt/biomodstack:ro" in command
    assert f"{fixtures}:/opt/md-fixtures:ro" in command
    assert f"{output.parent}:/opt/md-evidence:rw" in command
    assert str(image) in command
    assert "BMS_MD_ANALYSIS_PROBE_INNER=1" in command
    assert command[-2:] == ["--output", f"/opt/md-evidence/{output.name}"]


def test_qualification_report_requires_both_real_format_pairs(tmp_path: Path) -> None:
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    fixture_records = []
    for fixture_id, pair in (("gromacs-format-smoke", "gro_xtc"), ("openmm-format-smoke", "pdb_dcd")):
        fixture_dir = fixtures / fixture_id
        fixture_dir.mkdir()
        manifest = fixture_dir / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "schema": "bms.md.run.v1", "job_schema": "bms.md.job.v1", "status": "completed",
                    "created_at": "2026-07-21T00:00:00Z", "job_id": fixture_id,
                    "replica_index": 0, "replica_seed": 1,
                    "engine": {
                        "name": "gromacs" if pair == "gro_xtc" else "openmm",
                        "version": "fixture", "platform": "CPU",
                        "runtime": {"sif_sha256": "9" * 64, "image_name": "fixture.sif"},
                    },
                    "config": {"schema": "bms.md.job.v1"},
                    "stages": {"production": {"status": "completed"}},
                    "artifacts": {"fixture": {"path": "fixture.dat", "bytes": 1, "sha256": "8" * 64}},
                }
            ),
            encoding="utf-8",
        )
        fixture_records.append(
            {
                "id": fixture_id,
                "format_pair": pair,
                "manifest": f"{fixture_id}/manifest.json",
                "artifacts": {
                    "manifest": {
                        "path": f"{fixture_id}/manifest.json",
                        "bytes": manifest.stat().st_size,
                        "sha256": _sha256(manifest),
                    }
                },
            }
        )
    documentation = fixtures / "README.md"
    license_path = fixtures / "SOURCE_LICENSE.txt"
    documentation.write_text("fixture provenance\n", encoding="utf-8")
    license_path.write_text("fixture license\n", encoding="utf-8")
    (fixtures / "fixtures.json").write_text(
        json.dumps(
            {
                "schema": "bms.md.analysis-fixtures.v1",
                "provenance": {
                    "package": {
                        "canonical_release_url": "https://conda.anaconda.org/conda-forge/noarch/test-fixture.conda",
                        "archive_sha256": "1" * 64,
                    },
                    "source_files": {"source/member": "2" * 64},
                    "documentation": {"path": documentation.name, "sha256": _sha256(documentation)},
                    "license": {"path": license_path.name, "sha256": _sha256(license_path)},
                },
                "fixtures": fixture_records,
            }
        ),
        encoding="utf-8",
    )

    def fake_analysis_writer(manifest: Path, output: Path, **_kwargs: object) -> tuple[Path, bool]:
        output.parent.mkdir(parents=True, exist_ok=True)
        report = {
            "schema": "bms.md.analysis.v1", "status": "completed",
            "inputs": {"manifest_sha256": _sha256(manifest)},
            "tool": {"implementation_sha256": analysis_module._implementation_sha256()},
        }
        output.write_text(json.dumps(report), encoding="utf-8")
        derived = {}
        for name in ("timeseries", "residue_metrics"):
            path = output.parent / f"{output.stem}.{name}.parquet"
            path.write_bytes(name.encode())
            derived[name] = {"path": path.name, "bytes": path.stat().st_size, "sha256": _sha256(path), "semantic_role": f"md_analysis_{name}"}
        sidecar = output.parent / f"{output.stem}.artifacts.json"
        sidecar.write_text(
            json.dumps(
                {
                    "schema": "bms.md.analysis-artifacts.v1",
                    "status": "completed",
                    "artifacts": {
                        "analysis_report": {"path": output.name, "bytes": output.stat().st_size, "sha256": _sha256(output), "semantic_role": "md_analysis_report"},
                        **derived,
                    },
                }
            ),
            encoding="utf-8",
        )
        return output, True

    evidence = qualify_fixture_catalog(
        fixtures,
        tmp_path / "output",
        analysis_writer=fake_analysis_writer,
        runtime_sha256="a" * 64,
    )

    assert evidence["schema"] == "bms.md.analysis-runtime-qualification.v1"
    assert evidence["status"] == "passed"
    assert evidence["fixture_catalog_sha256"] == _sha256(fixtures / "fixtures.json")
    assert {record["format_pair"] for record in evidence["fixtures"]} == {"gro_xtc", "pdb_dcd"}
    assert all(record["status"] == "passed" for record in evidence["fixtures"])


def test_promotion_failure_never_replaces_destination(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.sif"
    candidate.write_bytes(b"candidate")
    destination = tmp_path / "runtime.sif"
    destination.write_bytes(b"existing")
    definition = tmp_path / "runtime.def"
    definition.write_text("definition", encoding="utf-8")
    lockfile = tmp_path / "requirements.lock"
    lockfile.write_text("lock", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    def failing_runner(*_args: object, **_kwargs: object) -> SimpleNamespace:
        return SimpleNamespace(returncode=1, stdout="", stderr="failed test")

    with pytest.raises(PromotionError, match="apptainer test"):
        promote_runtime(
            candidate=candidate,
            destination=destination,
            definition=definition,
            lockfile=lockfile,
            fixtures=fixtures,
            command_runner=failing_runner,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"existing"
    assert not destination.with_suffix(destination.suffix + ".manifest.json").exists()


def test_successful_promotion_records_exact_hashes_and_uses_atomic_replace(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.sif"
    candidate.write_bytes(b"candidate-sif")
    destination = tmp_path / "runtime.sif"
    definition = tmp_path / "runtime.def"
    definition.write_text("definition", encoding="utf-8")
    lockfile = tmp_path / "requirements.lock"
    lockfile.write_text("lock", encoding="utf-8")
    fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "bms_md_analysis"
    evidence = _write_complete_probe_evidence(
        tmp_path / "probe.json",
        candidate=candidate,
        fixtures=fixtures,
    )
    calls: list[list[str]] = []

    def passing_runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        calls.append(command)
        return SimpleNamespace(returncode=0, stdout="apptainer version 1.3.0\n", stderr="")

    manifest_path = promote_runtime(
        candidate=candidate,
        destination=destination,
        definition=definition,
        lockfile=lockfile,
        fixtures=fixtures,
        command_runner=passing_runner,
        probe_evidence=evidence,
    )

    assert destination.read_bytes() == candidate.read_bytes()
    manifest = json.loads(manifest_path.read_text())
    assert manifest["schema"] == "bms.md.analysis-runtime.v1"
    assert manifest["sif"]["sha256"] == _sha256(destination)
    assert manifest["definition"]["sha256"] == _sha256(definition)
    assert manifest["requirements_lock"]["sha256"] == _sha256(lockfile)
    assert calls[0][:2] == ["apptainer", "test"]
    assert not list(tmp_path.glob("*.tmp-*"))


def test_promotion_qualifies_an_immutable_staged_copy_when_source_is_replaced(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.sif"
    candidate.write_bytes(b"original-qualified-bytes")
    original = candidate.read_bytes()
    destination = tmp_path / "runtime.sif"
    definition = tmp_path / "runtime.def"
    lockfile = tmp_path / "requirements.lock"
    definition.write_text("definition", encoding="utf-8")
    lockfile.write_text("lock", encoding="utf-8")
    fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "bms_md_analysis"
    evidence = _write_complete_probe_evidence(tmp_path / "probe.json", candidate=candidate, fixtures=fixtures)
    invoked_images: list[Path] = []

    def swapping_runner(command: list[str], **_kwargs: object) -> SimpleNamespace:
        if command[:2] in (["apptainer", "test"], ["apptainer", "exec"]):
            invoked_images.append(next(Path(token) for token in command if token.endswith(".sif")))
        candidate.write_bytes(b"attacker-replacement")
        stdout = '{"python":"3.12.10"}' if command[:2] == ["apptainer", "exec"] else "apptainer 1.3.0"
        return SimpleNamespace(returncode=0, stdout=stdout, stderr="")

    promote_runtime(
        candidate=candidate,
        destination=destination,
        definition=definition,
        lockfile=lockfile,
        fixtures=fixtures,
        command_runner=swapping_runner,
        probe_evidence=evidence,
    )

    assert destination.read_bytes() == original
    assert invoked_images
    assert all(path != candidate for path in invoked_images)


def test_promotion_manifest_commit_failure_restores_previous_runtime_generation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    candidate = tmp_path / "candidate.sif"
    candidate.write_bytes(b"new-qualified-runtime")
    destination = tmp_path / "runtime.sif"
    destination.write_bytes(b"old-runtime")
    manifest_path = destination.with_suffix(destination.suffix + ".manifest.json")
    manifest_path.write_bytes(b"old-manifest")
    definition = tmp_path / "runtime.def"
    lockfile = tmp_path / "requirements.lock"
    definition.write_text("definition", encoding="utf-8")
    lockfile.write_text("lock", encoding="utf-8")
    fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "bms_md_analysis"
    evidence = _write_complete_probe_evidence(tmp_path / "probe.json", candidate=candidate, fixtures=fixtures)
    real_replace = promotion_module.os.replace
    failed = False

    def fail_manifest_once(source: object, destination_path: object) -> None:
        nonlocal failed
        if Path(destination_path) == manifest_path and not failed:
            failed = True
            raise OSError("injected runtime-manifest failure")
        real_replace(source, destination_path)

    monkeypatch.setattr(promotion_module.os, "replace", fail_manifest_once)
    with pytest.raises(OSError, match="runtime-manifest"):
        promote_runtime(
            candidate=candidate,
            destination=destination,
            definition=definition,
            lockfile=lockfile,
            fixtures=fixtures,
            command_runner=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout='{"python":"3.12.10"}', stderr=""),
            probe_evidence=evidence,
            replace_existing=True,
        )

    assert destination.read_bytes() == b"old-runtime"
    assert manifest_path.read_bytes() == b"old-manifest"


def test_atom_order_manifest_is_canonical_and_order_sensitive(tmp_path: Path) -> None:
    gro = tmp_path / "system.gro"
    gro.write_text(
        "fixture\n"
        "2\n"
        "    1ALA      N    1   0.000   0.000   0.000\n"
        "    1ALA     CA    2   0.100   0.000   0.000\n"
        "   1.0   1.0   1.0\n",
        encoding="utf-8",
    )

    first = contract_module.build_atom_order_manifest(gro)
    second = contract_module.build_atom_order_manifest(gro)
    assert first == second
    assert first["schema"] == "bms.md.atom-order.v1"
    assert first["atom_count"] == 2
    first_digest = contract_module.atom_order_identity(first)

    gro.write_text(
        "fixture\n"
        "2\n"
        "    1ALA     CA    2   0.100   0.000   0.000\n"
        "    1ALA      N    1   0.000   0.000   0.000\n"
        "   1.0   1.0   1.0\n",
        encoding="utf-8",
    )
    assert contract_module.atom_order_identity(contract_module.build_atom_order_manifest(gro)) != first_digest


def test_analysis_recomputes_topology_atom_order_instead_of_trusting_manifest(tmp_path: Path) -> None:
    topology = tmp_path / "system.pdb"
    topology.write_text(
        "ATOM      1  N   ALA A   1      11.000  12.000  13.000  1.00 20.00           N\nEND\n",
        encoding="utf-8",
    )
    trajectory = tmp_path / "trajectory.dcd"
    trajectory.write_bytes(b"not-opened-before-atom-order-admission")
    false_order = contract_module.build_atom_order_manifest(topology)
    false_order["atoms"][0]["name"] = "CA"
    atom_order = tmp_path / "atom-order-manifest.json"
    atom_order.write_text(json.dumps(false_order, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
    identity = f"sha256:{_sha256(atom_order)}"

    def record(path: Path, role: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "path": path.name,
            "bytes": path.stat().st_size,
            "sha256": _sha256(path),
            "semantic_role": role,
        }
        if role in {"analysis_topology", "analysis_trajectory"}:
            payload["atom_order_identity"] = identity
        return payload

    manifest = tmp_path / "manifest.json"
    manifest.write_text(
        json.dumps(
            {
                "schema": "bms.md.run.v1",
                "job_schema": "bms.md.job.v1",
                "status": "completed",
                "created_at": "2026-07-21T00:00:00Z",
                "job_id": "false-order",
                "replica_index": 0,
                "replica_seed": 1,
                "engine": {
                    "name": "gromacs", "version": "fixture", "platform": "CPU",
                    "runtime": {"sif_sha256": "9" * 64, "image_name": "gromacs-md-2025.3.sif"},
                },
                "config": {"schema": "bms.md.job.v1"},
                "stages": {"production": {"status": "completed"}},
                "artifacts": {
                    "topology": record(topology, "analysis_topology"),
                    "trajectory": record(trajectory, "analysis_trajectory"),
                    "atom_order": record(atom_order, "atom_order_manifest"),
                },
            }
        ),
        encoding="utf-8",
    )

    with pytest.raises(analysis_module.MDAnalysisContractError) as rejected:
        analysis_module.analyze_manifest(manifest, runtime_sha256="f" * 64)
    assert rejected.value.code == "MD_ANALYSIS_ATOM_ORDER_MISMATCH"


def test_verified_analysis_artifact_is_consumed_from_descriptor_pinned_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "system.pdb"
    source.write_bytes(b"ORIGINAL-VERIFIED-BYTES")
    attacker = tmp_path / "attacker.pdb"
    attacker.write_bytes(b"REPLACED-AFTER-OPEN")
    record = {
        "path": source.name,
        "bytes": source.stat().st_size,
        "sha256": _sha256(source),
    }
    snapshot_root = tmp_path / "snapshots"
    snapshot_root.mkdir()
    real_read = analysis_module.os.read
    swapped = False

    def swap_after_open(descriptor: int, count: int) -> bytes:
        nonlocal swapped
        if not swapped:
            swapped = True
            attacker.replace(source)
        return real_read(descriptor, count)

    monkeypatch.setattr(analysis_module.os, "read", swap_after_open)
    verified, digest = analysis_module._verify_artifact(
        tmp_path,
        record,
        snapshot_root=snapshot_root,
    )

    assert digest == record["sha256"]
    assert source.read_bytes() == b"REPLACED-AFTER-OPEN"
    assert verified.parent == snapshot_root
    assert verified.read_bytes() == b"ORIGINAL-VERIFIED-BYTES"


def test_analysis_schema_rejects_non_finite_numbers_before_json_publication() -> None:
    for value in (float("nan"), float("inf"), float("-inf")):
        with pytest.raises(analysis_module.MDAnalysisContractError) as rejected:
            analysis_module._validate_report_schema({"points": [{"rmsd_angstrom": value}]})
        assert rejected.value.code == "MD_ANALYSIS_NON_FINITE"


def test_analysis_generation_publish_rolls_back_all_files_when_commit_marker_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    targets = [
        tmp_path / "analysis.timeseries.parquet",
        tmp_path / "analysis.json",
        tmp_path / "analysis.artifacts.json",
    ]
    staged: list[tuple[Path, Path]] = []
    for index, target in enumerate(targets):
        target.write_bytes(f"old-{index}".encode())
        temporary = tmp_path / f"new-{index}.tmp"
        temporary.write_bytes(f"new-{index}".encode())
        staged.append((temporary, target))
    old_bytes = {target: target.read_bytes() for target in targets}
    real_replace = analysis_module.os.replace
    failed = False

    def fail_commit_marker_once(source: object, destination: object) -> None:
        nonlocal failed
        if Path(destination) == targets[-1] and not failed:
            failed = True
            raise OSError("injected commit-marker failure")
        real_replace(source, destination)

    monkeypatch.setattr(analysis_module.os, "replace", fail_commit_marker_once)
    with pytest.raises(OSError, match="commit-marker"):
        analysis_module._publish_staged_generation(staged, commit_marker=targets[-1])

    assert {target: target.read_bytes() for target in targets} == old_bytes
    assert not list(tmp_path.glob("*.tmp-*"))
    assert not list(tmp_path.glob("*.backup-*"))


def test_runtime_identity_is_required_and_analysis_identity_excludes_only_wall_clock() -> None:
    with pytest.raises(analysis_module.MDAnalysisContractError, match="runtime SIF") as missing:
        analysis_module.resolve_runtime_sha256(None, environ={})
    assert missing.value.code == "MD_ANALYSIS_RUNTIME_IDENTITY_MISSING"

    base = {
        "schema": "bms.md.analysis.v1",
        "status": "completed",
        "method": "md_backbone_rmsd_v1",
        "created_at": "2026-01-01T00:00:00Z",
        "inputs": {"manifest_sha256": "a" * 64},
        "tool": {"name": "MDAnalysis", "version": "2.9.0", "implementation_sha256": "b" * 64, "runtime_sif_sha256": "c" * 64},
        "policy": {"stride": 1},
        "derived_artifacts": {"timeseries": {"sha256": "d" * 64}},
    }
    changed_clock = json.loads(json.dumps(base))
    changed_clock["created_at"] = "2026-02-02T00:00:00Z"
    assert analysis_module.analysis_identity_sha256(base) == analysis_module.analysis_identity_sha256(changed_clock)
    changed_artifact = json.loads(json.dumps(changed_clock))
    changed_artifact["derived_artifacts"]["timeseries"]["sha256"] = "e" * 64
    assert analysis_module.analysis_identity_sha256(base) != analysis_module.analysis_identity_sha256(changed_artifact)

    changed_policy = json.loads(json.dumps(base))
    changed_policy["policy"]["stride"] = 2
    assert analysis_module.analysis_identity_sha256(base) != analysis_module.analysis_identity_sha256(changed_policy)


def test_apptainer_probe_binds_candidate_sif_hash(tmp_path: Path) -> None:
    image = tmp_path / "runtime.sif"
    image.write_bytes(b"qualified-runtime")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()
    output = tmp_path / "evidence.json"
    repo = tmp_path / "repo"
    repo.mkdir()

    command = build_apptainer_probe_command(image=image, fixtures=fixtures, output=output, repo_root=repo)

    expected = _sha256(image)
    assert "--env" in command
    assert f"BMS_MD_ANALYSIS_SIF_SHA256={expected}" in command


def test_promotion_replacement_is_explicit_and_atomic(tmp_path: Path) -> None:
    candidate = tmp_path / "candidate.sif"
    destination = tmp_path / "runtime.sif"
    definition = tmp_path / "runtime.def"
    lockfile = tmp_path / "requirements.lock"
    candidate.write_bytes(b"new-runtime")
    destination.write_bytes(b"old-runtime")
    definition.write_text("Bootstrap: docker\n", encoding="utf-8")
    lockfile.write_text("locked\n", encoding="utf-8")
    fixtures = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "bms_md_analysis"
    probe = _write_complete_probe_evidence(
        tmp_path / "probe.json",
        candidate=candidate,
        fixtures=fixtures,
    )

    with pytest.raises(PromotionError, match="already exists"):
        promote_runtime(
            candidate=candidate,
            destination=destination,
            definition=definition,
            lockfile=lockfile,
            fixtures=fixtures,
            probe_evidence=probe,
            command_runner=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="{}", stderr=""),
        )
    assert destination.read_bytes() == b"old-runtime"

    promote_runtime(
        candidate=candidate,
        destination=destination,
        definition=definition,
        lockfile=lockfile,
        fixtures=fixtures,
        probe_evidence=probe,
        command_runner=lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout="{}", stderr=""),
        replace_existing=True,
    )
    assert destination.read_bytes() == b"new-runtime"
    assert not list(tmp_path.glob("*.tmp-*"))


def test_promotion_rejects_symlinked_candidate_before_qualification(tmp_path: Path) -> None:
    real_candidate = tmp_path / "real-candidate.sif"
    real_candidate.write_bytes(b"candidate")
    candidate = tmp_path / "candidate.sif"
    candidate.symlink_to(real_candidate)
    definition = tmp_path / "runtime.def"
    definition.write_text("definition", encoding="utf-8")
    lockfile = tmp_path / "requirements.lock"
    lockfile.write_text("locked\n", encoding="utf-8")
    fixtures = tmp_path / "fixtures"
    fixtures.mkdir()

    def forbidden_runner(*_args: object, **_kwargs: object) -> SimpleNamespace:
        raise AssertionError("qualification must not run for a symlinked candidate")

    with pytest.raises(PromotionError, match="candidate SIF must be a nonempty regular file"):
        promote_runtime(
            candidate=candidate,
            destination=tmp_path / "runtime.sif",
            definition=definition,
            lockfile=lockfile,
            fixtures=fixtures,
            command_runner=forbidden_runner,
        )
