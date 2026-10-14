"""Tiny real files, real bundle builders and rendered module shell; no inference."""
from __future__ import annotations

import ast
import errno
import gzip
import hashlib
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPTS = ROOT / "scripts/shape_blueprint"
sys.path.insert(0, str(SCRIPTS))
from build_shape_result import build_result, _closed_file, retain_file
from build_shape_skip_bundle import build_skip_bundle
from attach_shape_post_refold import attach_post_refold, _gzip_copy
from run_shape_validator_suite import run_validator_suite
from validate_bundle import _verify


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def document(path, value):
    path.write_text(json.dumps(value))
    return path


def descriptor(path):
    return {"filename": path.name, "sha256": sha(path), "bytes": path.stat().st_size}


def inputs(root):
    candidate = root / "candidate"
    candidate.mkdir()
    (candidate / "structure.cif").write_text("data_EXPLICITLY_INERT\n")
    (candidate / "source.pdb").write_text("REMARK EXPLICITLY_INERT\n")
    request = dict(request_id="request", request_sha256="a" * 64, geometry_id="geometry",
                   geometry_sha256="b" * 64, point_pool_sha256="c" * 64,
                   sdf_sha256="d" * 64, sdf_sign="positive_inside", validator_suite=["esmfold2"])
    document(root / "request.json", request)
    metrics = {"schema": "bms_shape_candidate_metrics_v1", "candidate_id": "candidate",
               **{k: request[k] for k in ("geometry_sha256", "point_pool_sha256", "sdf_sha256")},
               "source_backbone_sha256": sha(candidate / "source.pdb")}
    document(candidate / "metrics.json", metrics)
    metadata = dict(schema="bms_shape_candidate_bundle_v1", candidate_id="candidate", name="candidate",
                    status="rejected", reason={"code": "inert_fixture"},
                    structure=descriptor(candidate / "structure.cif"), source_backbone=descriptor(candidate / "source.pdb"),
                    metrics=descriptor(candidate / "metrics.json"), provenance={"sequence_name": "sequence"})
    document(candidate / "candidate_bundle.json", metadata)
    evidence = root / "evidence"
    evidence.mkdir()
    native = evidence / "sample.cif"
    native.write_text("data_INERT_NATIVE_SAMPLE\n")
    suite = dict(schema="bms_shape_validator_suite_v2", sequence_name="sequence", validators=["esmfold2"],
                 records={"esmfold2": {"status": "completed", "artifacts": [descriptor(native)],
                                      "effective_settings": {"seed": 7, "use_templates": False},
                                      "native_metrics": {"sample_id": "sequence_000"}}})
    document(evidence / "shape_validator_records.json", suite)
    sequences = root / "sequences"
    sequences.mkdir()
    document(sequences / "sequence_records.json", {"records": [dict(sequence_name="sequence", engine="proteinmpnn",
        backbone_candidate_id="parent", sample_index=0, sequence_settings={"temperature": 0.1},
        requested_sequence_settings={"temperature": 0.1})]})
    aggregate = dict(schema="bms_rfd3_aggregate_manifest_v1", request_sha256=request["request_sha256"], status="complete")
    aggregate["aggregate_sha256"] = hashlib.sha256(json.dumps(aggregate, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    document(root / "aggregate.json", aggregate)
    return candidate, evidence, sequences


def attach(root, output, source, evidence, sequences):
    return attach_post_refold(bundle_dir=output, source_bundle=source,
        validator_records_path=evidence / "shape_validator_records.json", sequence_bundle=sequences,
        request_path=root / "request.json", geometry_manifest_path=root / "unused",
        points_path=root / "unused", sdf_path=root / "unused")


def test_actual_attachment_module_shell_private_outputs_and_terminal_roles(tmp_path, monkeypatch):
    candidate, evidence, sequences = inputs(tmp_path)
    before = {p.name: p.read_bytes() for p in candidate.iterdir()}
    # Exactly Nextflow's relative directory aliases, with a real private cwd.
    work = tmp_path / "work"
    work.mkdir()
    for name, target in [("candidate", candidate), ("evidence", evidence), ("sequences", sequences)]:
        (work / name).symlink_to(os.path.relpath(target, work), target_is_directory=True)
    module = (ROOT / "modules/shape_blueprint.nf").read_text()
    process = module.split("process AttachShapePostRefold {", 1)[1].split("process BuildShapeSkipBundle", 1)[0]
    assert "stageInMode 'rellink'" in process and "cp -a" not in process
    shell = process.split('"""', 2)[1].replace("\\\\", "\\")
    substitutions = {"sequence_name": "sequence", "params.code_root": str(ROOT), "sequence_bundle": "sequences",
                     "candidate_bundle": "candidate", "validator_records": "evidence",
                     "request_json": str(tmp_path / "request.json"), "geometry_manifest": "unused",
                     "point_pool": "unused", "sdf_grid": "unused"}
    for key, value in substitutions.items():
        shell = shell.replace("${" + key + "}", value)
    assert "${" not in shell
    # Use the locked interpreter, not a system Python selected by a shell default.
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "python3").write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    (binary / "python3").chmod(0o755)
    completed = subprocess.run(["bash", "-c", shell], cwd=work,
        env={**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"]}, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stdout + completed.stderr
    attached = work / "attached_shape_candidate_bundle_sequence"
    assert attached.is_dir() and not attached.is_symlink()
    assert {p.name: p.read_bytes() for p in candidate.iterdir()} == before
    copied = []
    copyfile = shutil.copyfile
    def observed_copy(source, destination, *args, **kwargs):
        copied.append(Path(source).name)
        return copyfile(source, destination, *args, **kwargs)
    monkeypatch.setattr(shutil, "copyfile", observed_copy)
    sibling = tmp_path / "sibling"
    attach(tmp_path, sibling, candidate, evidence, sequences)
    for name in ("structure.cif", "source.pdb"):
        assert (attached / name).samefile(candidate / name)
        assert (sibling / name).samefile(candidate / name)
    for name in ("candidate_bundle.json", "metrics.json", "post_refold_evaluation.json"):
        assert not (attached / name).samefile(sibling / name)
    # Unchanged native evidence has one payload at all subsequent handoffs.
    shared = attached / "validator_esmfold2_0000.cif"
    assert shared.samefile(evidence / "sample.cif")
    published = tmp_path / "published"
    manifest = build_result(job_id="job", request_path=tmp_path / "request.json", candidate_bundles=[attached],
                            output_dir=published, aggregate_path=tmp_path / "aggregate.json")
    assert sorted(copied) == ["candidate_bundle.json", "metrics.json", "metrics.json"]
    row, = manifest["rejections"]
    assert row["candidate_id"] == "candidate"
    assert row["provenance"]["sequence_engine"] == "proteinmpnn"
    native, = row["validator_artifacts"]
    assert (published / native["relative_path"]).samefile(shared)
    assert native["native_path"] == "sample.cif" and native["validator"] == "esmfold2"
    assert native["sha256"] == sha(evidence / "sample.cif")
    suite = json.loads((published / row["validator_evidence"]["relative_path"]).read_text())
    assert suite["records"]["esmfold2"]["effective_settings"] == {"seed": 7, "use_templates": False}
    # Native mutable writes occur only to private metadata/new transforms.
    (attached / "metrics.json").write_text("child truncation")
    with (attached / "candidate_bundle.json").open("a") as stream:
        stream.write("child append")
    _gzip_copy(attached / "structure.cif", attached / "new.cif.gz")
    assert gzip.decompress((attached / "new.cif.gz").read_bytes()) == before["structure.cif"]
    assert {p.name: p.read_bytes() for p in candidate.iterdir()} == before
    assert json.loads((published / row["metrics"]["relative_path"]).read_text())["schema"] == "bms_shape_candidate_metrics_v1"
    assert json.loads((sibling / "metrics.json").read_text())["schema"] == "bms_shape_candidate_metrics_v1"
    # Unlink/atomic replacement of a retained immutable child name never damages siblings.
    replacement = attached / "replacement"
    replacement.write_text("new transform")
    os.replace(replacement, attached / "source.pdb")
    (attached / "structure.cif").unlink()
    assert (sibling / "source.pdb").read_bytes() == before["source.pdb"]
    shutil.rmtree(candidate)
    shutil.rmtree(evidence)
    shutil.rmtree(sequences)
    shutil.rmtree(work)
    assert (published / native["relative_path"]).read_text() == "data_INERT_NATIVE_SAMPLE\n"


def test_real_nextflow_attachment_result_publication_and_cached_republish(tmp_path):
    """Run only CPU file plumbing; no images, engine, provider or DB."""
    candidate, evidence, sequences = inputs(tmp_path)
    unused = tmp_path / "unused"
    unused.write_text("not read by pre-rejected candidate")
    (tmp_path / "points.f32").write_bytes(b"\x00" * 12)
    (tmp_path / "sdf.f32").write_bytes(b"\x00" * 32)
    jar = Path(os.environ.get("BMS_TEST_NEXTFLOW_JAR") or os.environ.get("ONE_COPY_NEXTFLOW_JAR")
               or Path.home() / ".nextflow/framework/25.10.1/nextflow-25.10.1-one.jar")
    assert jar.is_file(), "installed offline Nextflow is required; never download"
    binary = tmp_path / "bin"
    binary.mkdir()
    (binary / "python3").write_text("#!/bin/sh\nexec " + shlex.quote(sys.executable) + ' "$@"\n')
    (binary / "python3").chmod(0o755)
    workflow = tmp_path / "fixture.nf"
    workflow.write_text(f"""
include {{ AttachShapePostRefold; BuildShapeResult }} from '{ROOT}/modules/shape_blueprint'
workflow {{
    AttachShapePostRefold(Channel.of(tuple('sequence', file('{candidate}'), file('{evidence}'), file('{sequences}'))),
        file('{tmp_path}/request.json'), file('{unused}'), file('{tmp_path}/points.f32'), file('{tmp_path}/sdf.f32'))
    BuildShapeResult(AttachShapePostRefold.out.bundle.map {{ sequence, bundle -> bundle }}.collect(),
        file('{tmp_path}/request.json'), file('{tmp_path}/aggregate.json'), 'job')
}}
""")
    config = tmp_path / "fixture.config"
    config.write_text("process.executor='local'\napptainer.enabled=false\ndocker.enabled=false\n")
    environment = {**os.environ, "PATH": str(binary) + ":" + os.environ["PATH"],
                   "JAVA_TOOL_OPTIONS": "--add-opens=java.base/java.lang=ALL-UNNAMED --add-opens=java.base/java.util=ALL-UNNAMED",
                   "NXF_OFFLINE": "true", "NXF_DISABLE_CHECK_LATEST": "true"}
    command = ["java", "-jar", str(jar), "-C", str(config), "run", str(workflow),
               "--code_root", str(ROOT), "--out_dir", str(tmp_path / "published"),
               "-work-dir", str(tmp_path / "work"), "-with-trace", str(tmp_path / "trace.tsv")]
    first = subprocess.run(command, cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
    assert first.returncode == 0, first.stdout + first.stderr
    manifest = json.loads((tmp_path / "published/results/shape_result_manifest.json").read_text())
    native, = manifest["rejections"][0]["validator_artifacts"]
    published = tmp_path / "published" / native["relative_path"]
    assert published.samefile(evidence / "sample.cif")
    attached_outputs = [p for p in (tmp_path / "work").glob("*/*/attached_shape_candidate_bundle_sequence") if not p.is_symlink()]
    assert len(attached_outputs) == 1
    attached = attached_outputs[0]
    assert not (attached / "metrics.json").samefile(candidate / "metrics.json")
    assert json.loads((candidate / "metrics.json").read_text()).get("post_refold") is None
    shutil.rmtree(tmp_path / "published")
    command[-1] = str(tmp_path / "resume-trace.tsv")
    resumed = subprocess.run(command + ["-resume"], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=120)
    assert resumed.returncode == 0, resumed.stdout + resumed.stderr
    import csv
    with (tmp_path / "resume-trace.tsv").open() as handle:
        rows = list(csv.DictReader(handle, delimiter="\t"))
    assert len(rows) == 2 and all(row["status"] == "CACHED" for row in rows), rows
    assert published.samefile(evidence / "sample.cif")
    shutil.rmtree(tmp_path / "work")
    shutil.rmtree(evidence)
    assert published.read_text() == "data_INERT_NATIVE_SAMPLE\n"


@pytest.mark.parametrize("kind", ["regular", "hardlink", "leaf_alias", "root_alias"])
def test_bundle_reader_old_and_shared_layouts_preserve_hash_and_containment(tmp_path, kind):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    member = bundle / "native.cif"
    member.write_text("data_INERT\n")
    desc = descriptor(member)
    if kind == "hardlink":
        os.link(member, bundle / "retained.cif")
    elif kind == "leaf_alias":
        member.rename(bundle / "concrete.cif")
        member.symlink_to("concrete.cif")
    elif kind == "root_alias":
        alias = tmp_path / "alias"
        alias.symlink_to(bundle, target_is_directory=True)
        bundle = alias
    assert _closed_file(bundle, desc, "native").read_bytes() == b"data_INERT\n"
    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        _closed_file(bundle, {**desc, "sha256": "0" * 64}, "native")
    outside = tmp_path / "outside.cif"
    outside.write_bytes(b"data_INERT\n")
    (bundle / "escape.cif").symlink_to(outside)
    with pytest.raises(ValueError, match="outside"):
        _closed_file(bundle, {**desc, "filename": "escape.cif"}, "native")


def test_geometry_staging_keeps_baked_sampler_single_link_referents(tmp_path):
    payload = tmp_path / "geometry.f32"
    payload.write_bytes(b"\x00" * 12)
    aliases = []
    for i in range(4):
        work = tmp_path / str(i)
        work.mkdir()
        alias = work / payload.name
        alias.symlink_to(os.path.relpath(payload, work))
        aliases.append(alias)
        assert _verify(alias, sha(payload), 12) == sha(payload)
        with pytest.raises(ValueError, match="hash mismatch"):
            _verify(alias, "0" * 64, 12)
    assert payload.stat().st_nlink == 1
    # Execute the actual baked helper unchanged, without importing an inference engine.
    tree = ast.parse((SCRIPTS / "rfd3_shape_sampler.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_read_regular")
    namespace = {"Path": Path}
    exec(compile(ast.Module(body=[node], type_ignores=[]), "rfd3_shape_sampler.py", "exec"), namespace)
    for alias in aliases:
        assert namespace["_read_regular"](str(alias.resolve()), label="geometry") == payload.read_bytes()
    wrapper = (SCRIPTS / "run_shape_rfd3.py").read_text()
    assert "shape_points_path={points_path.resolve()}" in wrapper
    assert "shape_sdf_path={sdf_path.resolve()}" in wrapper
    module = (ROOT / "modules/shape_blueprint.nf").read_text()
    for name in ("ValidateShapeBundle", "RunShapeRFD3", "AdmitRFD3InitialCandidate", "EvaluateShapeCandidate", "AttachShapePostRefold"):
        assert "stageInMode 'rellink'" in module.split("process " + name + " {", 1)[1].split("input:", 1)[0]


def test_skip_builder_and_final_bundle_share_backbone_without_copy(tmp_path, monkeypatch):
    source = tmp_path / "backbone"
    source.mkdir()
    backbone = source / "shape_backbone.pdb"
    backbone.write_text("REMARK tiny retained backbone\n")
    fields = dict(request_sha256="a" * 64, geometry_sha256="b" * 64, point_pool_sha256="c" * 64, sdf_sha256="d" * 64)
    req = dict(**fields, request_id="request", geometry_id="geometry", sdf_sign="positive_inside",
               sequence_policy="skip", sequences_per_backbone=0, validator_suite=[])
    document(tmp_path / "request.json", req)
    document(source / "shape_backbone_manifest.json", dict(**fields, status="accepted", candidate_id="a" * 64, backbone=descriptor(backbone)))
    document(source / "initial_admission.json", dict(**fields, status="accepted", metrics={"inert": True}))
    def forbidden(*args, **kwargs):
        raise AssertionError("unchanged same-filesystem payload was copied")
    monkeypatch.setattr(shutil, "copyfile", forbidden)
    bundle = tmp_path / "skip"
    metadata = build_skip_bundle(backbone_dir=source, request_path=tmp_path / "request.json", output_dir=bundle)
    for role in ("structure", "source_backbone"):
        assert (bundle / metadata[role]["filename"]).samefile(backbone)
        assert metadata[role]["sha256"] == sha(backbone)
    assert metadata["provenance"]["sequence_engine"] is None
    assert not (bundle / metadata["metrics"]["filename"]).samefile(backbone)


def test_retention_cross_filesystem_is_explicit_independent_closure(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.write_text("tiny closed result")
    destination = tmp_path / "retained"
    def exdev(*args, **kwargs):
        raise OSError(errno.EXDEV, "fixture cross filesystem")
    monkeypatch.setattr(os, "link", exdev)
    retain_file(source, destination)
    assert not destination.samefile(source)
    source.unlink()
    assert destination.read_text() == "tiny closed result"


@pytest.mark.parametrize("kind", ["hardlink", "contained_alias", "wrong_hash", "outroot"])
def test_native_peer_reader_aliases_keep_identity_and_real_containment(tmp_path, kind):
    bundle = tmp_path / "peer"
    bundle.mkdir()
    artifact = bundle / "native.cif"
    artifact.write_text("data_INERT_PEER\n")
    binding = descriptor(artifact)
    if kind == "hardlink":
        os.link(artifact, bundle / "retained.cif")
    elif kind == "contained_alias":
        artifact.rename(bundle / "concrete.cif")
        artifact.symlink_to("concrete.cif")
    elif kind == "wrong_hash":
        binding["sha256"] = "0" * 64
    else:
        artifact.rename(tmp_path / "outside.cif")
        artifact.symlink_to(tmp_path / "outside.cif")
    document(bundle / "shape_validator_records.json", {
        "schema": "bms_shape_validator_suite_v2", "sequence_name": "seq", "validators": ["boltz2"],
        "runtime": {"seed": 9}, "records": {"boltz2": {"schema": "bms_shape_validator_record_v1",
        "validator": "boltz2", "status": "completed", "artifacts": [binding]}}})
    staged = tmp_path / "staged_peer"
    staged.symlink_to(bundle, target_is_directory=True)
    output = tmp_path / "aggregate/shape_validator_records.json"
    kwargs: dict[str, Any] = dict(sequence="INERT", sequence_name="seq", esm_structure_path=None, esm_metrics_path=None,
                  output_path=output, validators=["boltz2"], seed=9, code_root=ROOT, peer_evidence=[staged])
    if kind in {"outroot", "wrong_hash"}:
        with pytest.raises(ValueError, match="escaped|changed"):
            run_validator_suite(**kwargs)
    else:
        result = run_validator_suite(**kwargs)
        leaf, = result["records"]["boltz2"]["artifacts"]
        assert (output.parent / leaf["filename"]).samefile(artifact)
        assert leaf["sha256"] == binding["sha256"]


def test_validator_aggregate_reuses_completed_native_artifacts(tmp_path, monkeypatch):
    source = tmp_path / "esm"
    source.mkdir()
    structure = source / "seq_000.cif"
    structure.write_text("data_INERT\n")
    metrics = document(source / "seq_000.json", dict(sequence_name="seq", sample_id="seq_000", plddt_mean=0.8))
    document(source / "manifest.json", {"samples": [{"sample_id": "seq_000", "cif": structure.name, "metrics": metrics.name}]})
    def forbidden(*args, **kwargs):
        raise AssertionError("completed evidence copied")
    monkeypatch.setattr(shutil, "copyfile", forbidden)
    output = tmp_path / "aggregate/shape_validator_records.json"
    payload = run_validator_suite(sequence="INERT", sequence_name="seq", esm_structure_path=structure,
        esm_metrics_path=metrics, esm_bundle=source, output_path=output, validators=["esmfold2"],
        seed=9, code_root=ROOT, peer_evidence=[], request={"validator_settings": {"esmfold2": {"seed": 9}}})
    record = payload["records"]["esmfold2"]
    assert record["effective_settings"] == {"seed": 9}
    for artifact, original in zip(record["artifacts"], (structure, metrics)):
        assert (output.parent / artifact["filename"]).samefile(original)
        assert artifact["sha256"] == sha(original)
    assert record["samples"][0]["native_sample_key"] == "seq_000"
