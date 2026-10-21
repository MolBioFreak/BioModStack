from __future__ import annotations

import asyncio
import hashlib
import io
from pathlib import Path

import pytest
from Bio.PDB import MMCIFIO, PDBParser
from Bio.PDB.MMCIF2Dict import MMCIF2Dict

from services.md import starting_structures as sources
from scripts.bms_md.chemistry.amber_prepare_worker import _preprocess_pdb


def ensemble():
    atom = "ATOM      7  CA AGLY A  12B      1.000   2.000   3.000  0.50 20.00           C  \n"
    second = atom.replace("   1.000", "   9.000")
    return ("MODEL        1\n" + atom + "ENDMDL\nMODEL        2\n" + second + "ENDMDL\nEND\n").encode()


@pytest.mark.parametrize("member", [1, 2])
@pytest.mark.parametrize("format", ["pdb", "cif"])
def test_exact_member_derivative_retains_author_atoms_and_original(tmp_path, monkeypatch, member, format):
    raw = ensemble()
    if format == "cif":
        writer = MMCIFIO()
        writer.set_structure(PDBParser(QUIET=True).get_structure("ensemble", io.StringIO(raw.decode())))
        target = io.StringIO(); writer.save(target)
        raw = target.getvalue().encode()
    path = tmp_path / f"ensemble.{format}"
    path.write_bytes(raw)
    ref = sources.StartingStructureSourceRef(kind="upload", id="selected", model_number=member)
    async def resolve(selected, session):
        return sources.ResolvedStartingStructure(source_ref=selected, path=path, label=path.name)
    monkeypatch.setattr(sources, "_resolve_source", resolve)
    resolved = asyncio.run(sources.resolve_source(ref, None))
    try:
        derived = sources.read_resolved_structure(resolved)
        assert path.read_bytes() == raw
        assert Path(resolved.original_runtime_path).read_bytes() == raw
        assert resolved.source_provenance["original_sha256"] == hashlib.sha256(raw).hexdigest()
        assert resolved.source_provenance["consumed_sha256"] == derived.sha256
        if format == "pdb":
            expected = [line for line in raw.splitlines(keepends=True) if line.startswith(b"ATOM")][member - 1]
            assert expected in derived.data
            assert derived.data.count(b"ATOM") == 1
        else:
            before = MMCIF2Dict(io.StringIO(raw.decode()))
            after = MMCIF2Dict(io.StringIO(derived.data.decode()))
            rows = [i for i, value in enumerate(before["_atom_site.pdbx_PDB_model_num"]) if int(value) == member]
            for key in before:
                if key.startswith("_atom_site."):
                    assert after[key] == [before[key][i] for i in rows]
        inspection = sources.inspect_resolved_structure(resolved)
        assert inspection.inspection.model_count == 1
        assert "selection=" in inspection.viewer.url
        snapshot = sources.open_resolved_structure_snapshot(resolved, expected_sha256=derived.sha256)
        assert b"".join(snapshot.iter_chunks()) == derived.data
    finally:
        resolved.close()


def test_worker_preserves_model_boundaries_without_cross_model_disulfides(tmp_path):
    source, target = tmp_path / "source.pdb", tmp_path / "prepared.pdb"
    source.write_bytes(ensemble().replace(b" CA AGLY", b" SG  CYS"))
    assert _preprocess_pdb(source, target) == []
    text = target.read_text()
    assert text.count("MODEL ") == 2 and text.count("ENDMDL") == 2
    assert text.index("ENDMDL") < text.rindex("ATOM")


def test_unselected_single_model_wire_unchanged():
    assert sources.StartingStructureSourceRef(kind="design", id="existing").model_dump() == {"kind": "design", "id": "existing"}


@pytest.mark.asyncio
async def test_exact_design_document_snapshot_ignores_primary_retarget(tmp_path, monkeypatch):
    from database import Design, Job, JobArtifact
    import paths
    from services.md import launch_contract as contract
    from services.md.starting_structures import MdNativeLaunchIntent, compile_native_job_intent
    from test_md_native_contract import _guided_intent
    source = tmp_path / "selected.pdb"; source.write_bytes(ensemble())
    other = tmp_path / "primary.pdb"; other.write_bytes(ensemble().replace(b"   9.000", b"  99.000"))
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    job = Job(id="11111111-1111-4111-8111-111111111111", name="producer", model_id="bindcraft2", mode="design", status="completed", output_dir=str(tmp_path), params={}, provenance={
        "bindcraft2_native_publication": {"candidates": [{"design_id": "22222222-2222-4222-8222-222222222222", "structures": [{"artifact_id": "33333333-3333-4333-8333-333333333333", "sha256": digest, "target_state": "bound"}]}]}})
    design = Design(id="22222222-2222-4222-8222-222222222222", job_id=job.id, name="candidate", pdb_path=str(other), provenance={"primary_artifact_id": "not-selected"})
    artifact = JobArtifact(id="33333333-3333-4333-8333-333333333333", owner_job_id=job.id, storage_path=str(source), logical_path="selected.pdb", sha256=digest)
    class Session:
        async def get(self, cls, identity):
            return {(Job, job.id): job, (Design, design.id): design, (JobArtifact, artifact.id): artifact}.get((cls, identity))
    from services import job_result_roots
    monkeypatch.setattr(job_result_roots, "get_results_dir", lambda: tmp_path)
    monkeypatch.setattr(paths, "get_allowed_roots", lambda: {"inputs": tmp_path})
    monkeypatch.setattr(sources, "get_allowed_roots", lambda: {"inputs": tmp_path})
    ref = sources.StartingStructureSourceRef(kind="candidate", id=job.id, candidate_ref={"kind": "design", "job_id": job.id, "design_id": design.id, "document": {"artifact_id": artifact.id, "target_state": "bound", "model": 2}})
    resolved = await sources.resolve_source(ref, Session())
    try:
        derivative = sources.read_resolved_structure(resolved)
        design.pdb_path = str(tmp_path / "gone.pdb")
        design.provenance = {"primary_artifact_id": "changed-after-selection"}
        source.write_bytes(other.read_bytes())
        assert sources.read_resolved_structure(resolved).data == derivative.data
        intent, catalog, profile = _guided_intent()
        spec = compile_native_job_intent(MdNativeLaunchIntent.model_validate(intent), profile=profile,
                                        source_token=sources.resolved_runtime_path(resolved))
        monkeypatch.setattr(contract, "_bound_engine_runtime_identity", lambda engine: {"image_name": "inert", "sif_sha256": "a" * 64})
        provenance = {**resolved.source_provenance, "source_sha256": derivative.sha256,
                      "source_ref": ref.model_dump(mode="json"),
                      "native_input_identity": {"structure": derivative.sha256, "original_structure": digest}}
        materialized = contract.materialize_md_job_spec(params={"md_job_spec": spec, "md_source_provenance": provenance},
            job_id="selected-model", output_dir=tmp_path / "output", chemistry_catalog=catalog,
            resolve_runtime_path=lambda value: resolved.original_runtime_path if value == "bms-md-starting-structure:original" else value)
        consumed = Path(materialized["md_job_spec"]["input"]["structure"])
        original = Path(materialized["md_source_provenance"]["original_snapshot"]["path"])
        assert consumed.suffix == original.suffix == ".pdb"
        assert consumed.read_bytes() == derivative.data
        assert original.read_bytes() == ensemble()
        assert materialized["md_source_provenance"]["source_ref"] == ref.model_dump(mode="json")
    finally:
        resolved.close()
