"""Map accepted P1 native receipts, not QC filenames, to derived sources.

No QC or verification manifest is invented for an alignment-only result. The
source-manifest field binds the canonical accepted native scientific receipt.
The current catalog source schema supports a single immutable reference;
pooled multi-reference and unaligned/demux results remain explicitly unsupported.
"""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import re

import pysam
import rfc8785

from services import ngs_alignment_sessions as storage
from services.job_result_roots import resolve_persisted_job_result_root
from services.ngs_alignment_derived_products import source_identity, identity_sha256, catalog_request_id

KINDS = frozenset({"ont_native_basecall", "ont_native_methylation", "ont_native_plasmid",
                   "ont_native_clone_validation", "ont_native_alignment"})
ARTIFACTS = {"alignment": "align/aligned.bam", "alignment_index": "align/aligned.bam.bai",
             "reference": "align/reference.fasta", "reference_index": "align/reference.fasta.fai"}


def is_native(job):
    provenance = job.provenance if isinstance(job.provenance, dict) else {}
    receipt = provenance.get("result_integrity")
    return isinstance(receipt, dict) and receipt.get("result_kind") in KINDS


@contextmanager
def result_root(job):
    root = resolve_persisted_job_result_root(job)
    with storage.open_presentation_authority_root(root, create=False) as pinned:
        yield pinned


def accepted_artifacts(job):
    """Shared accepted receipt inventory for catalog and original delivery."""
    if not is_native(job):
        raise storage.AlignmentSessionError("unsupported native scientific receipt")
    receipt = job.provenance["result_integrity"]
    params = job.params if isinstance(job.params, dict) else {}
    if receipt.get("state") != "validated" or receipt.get("partial") is not False:
        raise storage.AlignmentSessionError("native scientific receipt is not validated")
    artifacts = receipt.get("artifacts")
    if not isinstance(artifacts, list) or hashlib.sha256(rfc8785.dumps(artifacts)).hexdigest() != receipt.get("artifact_set_sha256"):
        raise storage.AlignmentSessionError("native artifact-set integrity mismatch")
    if receipt.get("workflow_id") != params.get("ont_workflow_id") or receipt.get("input_mode") != params.get("ont_input_mode"):
        raise storage.AlignmentSessionError("native workflow/input authority mismatch")
    by_path = {}
    for artifact in artifacts:
        if not isinstance(artifact, dict) or set(artifact) != {"path", "sha256", "size_bytes"}:
            raise storage.AlignmentSessionError("native artifact authority is malformed")
        name = artifact["path"]
        if (not isinstance(name, str) or not name or Path(name).is_absolute()
                or any(part in {"", ".", ".."} for part in name.split("/"))
                or name in by_path or re.fullmatch(r"[0-9a-f]{64}", str(artifact["sha256"])) is None
                or type(artifact["size_bytes"]) is not int or artifact["size_bytes"] < 0):
            raise storage.AlignmentSessionError("native artifact identity is unsafe or ambiguous")
        by_path[name] = artifact
    if hashlib.sha256(rfc8785.dumps(params)).hexdigest() != receipt.get("effective_params_sha256"):
        raise storage.AlignmentSessionError("native effective settings authority changed")
    return receipt, by_path


def artifact_descriptors(job):
    receipt, by_path = accepted_artifacts(job)
    if job.status != "completed" or job.queue_status != "completed" or job.awaiting_input:
        raise storage.AlignmentSessionError("native scientific result is not accepted")
    scientific = {key: value for key, value in receipt.items() if key != "alignment_presentations"}
    receipt_sha = hashlib.sha256(rfc8785.dumps(scientific)).hexdigest()
    mime = {".bam": "application/octet-stream", ".bai": "application/octet-stream",
            ".fasta": "text/plain", ".fai": "text/plain", ".json": "application/json", ".html": "text/html"}
    return [{"artifact_id": identity_sha256({"schema": "bms.ngs.native-artifact.v1", "job_id": str(job.id),
                "receipt_sha256": receipt_sha, **artifact}),
             "kind": Path(name).suffix.lstrip(".") or "artifact", "filename": Path(name).name,
             "relative_path": name, "sha256": artifact["sha256"], "size_bytes": artifact["size_bytes"],
             "mime_type": mime.get(Path(name).suffix, "application/octet-stream"),
             "source_manifest_sha256": receipt_sha, "available": True}
            for name, artifact in by_path.items()]


def sources(job, root):
    """Read-only mapping; caller supplies accepted or terminal-CAS candidate Job."""
    if not is_native(job):
        return []
    receipt, by_path = accepted_artifacts(job)
    params = job.params
    present = [name in by_path for name in ARTIFACTS.values()]
    if not any(present):
        return []  # Unaligned basecall, demux, or a disabled alignment branch.
    if not all(present):
        raise storage.AlignmentSessionError("native alignment bundle is incomplete")
    alignment = receipt.get("alignment", {})
    reference_sha = alignment.get("reference_sequence_sha256")
    if reference_sha != params.get("reference_sequence_sha256") or re.fullmatch(r"[0-9a-f]{64}", str(reference_sha)) is None:
        raise storage.AlignmentSessionError("native reference sequence authority is missing")
    reference_authority = receipt.get("catalog_reference_authority")
    topology = reference_authority.get("topology") if isinstance(reference_authority, dict) else params.get("reference_topology")
    if topology not in {"linear", "circular"}:
        raise storage.AlignmentSessionError("native reference topology authority is missing")
    if hashlib.sha256(rfc8785.dumps(params)).hexdigest() != receipt.get("effective_params_sha256"):
        raise storage.AlignmentSessionError("native effective settings authority changed")
    ref, fai = by_path[ARTIFACTS["reference"]], by_path[ARTIFACTS["reference_index"]]
    if reference_authority is not None and (
            reference_authority.get("schema") != "bms.ngs.native-catalog-reference.v1"
            or reference_authority.get("normalized_sequence_sha256") != reference_sha
            or reference_authority.get("fasta_sha256") != ref["sha256"]
            or reference_authority.get("fasta_size_bytes") != ref["size_bytes"]):
        raise storage.AlignmentSessionError("native catalog reference binding changed")
    with storage.open_verified_artifact_snapshot(root / ref["path"], expected_sha256=ref["sha256"], expected_size=ref["size_bytes"]) as fasta_handle, storage.open_verified_artifact_snapshot(
            root / fai["path"], expected_sha256=fai["sha256"], expected_size=fai["size_bytes"]) as fai_handle:
        contigs, sequence = storage._fasta_contigs_from_handle(fasta_handle)
        if len(contigs) != 1:
            raise storage.AlignmentSessionError("native single-reference catalog is unsupported")
        contig, (length, _md5) = next(iter(contigs.items()))
        if hashlib.sha256(sequence).hexdigest() != reference_sha:
            raise storage.AlignmentSessionError("native reference digest mismatch")
        with pysam.FastaFile(storage._descriptor_path(fasta_handle.fileno()),
                filepath_index=storage._descriptor_path(fai_handle.fileno())) as fasta:
            if tuple(fasta.references) != (contig,) or fasta.get_reference_length(contig) != length:
                raise storage.AlignmentSessionError("native reference index integrity mismatch")
    # Derived request pointers are excluded to avoid a source/intent hash cycle.
    scientific = {key: value for key, value in receipt.items() if key != "alignment_presentations"}
    receipt_sha = hashlib.sha256(rfc8785.dumps(scientific)).hexdigest()
    bam, bai = by_path[ARTIFACTS["alignment"]], by_path[ARTIFACTS["alignment_index"]]
    session_id = identity_sha256({"schema": "bms.ngs.native-alignment-session.v2",
        "job_id": str(job.id), "receipt_sha256": receipt_sha, "mode": "primary"})[:24]
    reference = {"contig": contig, "length_bp": length, "topology": topology,
                 "normalized_sequence_sha256": reference_sha, "fasta_sha256": ref["sha256"], "fai_sha256": fai["sha256"]}
    pair = hashlib.sha256(b"bms.ngs.alignment-pair.v1\0" + rfc8785.dumps({
        "alignment_sha256": bam["sha256"], "alignment_index_sha256": bai["sha256"]})).hexdigest()
    source = source_identity(job_id=str(job.id), session_id=session_id, mode="primary", reference=reference,
        source_manifest_sha256=receipt_sha, source_artifact_set_sha256=receipt["artifact_set_sha256"],
        package_artifact_set_sha256=receipt["artifact_set_sha256"], alignment_pair_sha256=pair,
        alignment_sha256=bam["sha256"], alignment_size_bytes=bam["size_bytes"],
        alignment_index_sha256=bai["sha256"], alignment_index_size_bytes=bai["size_bytes"])
    return [(source, {"alignment_path": root / bam["path"], "index_path": root / bai["path"],
                      "result_root": root})]


async def prepare_intents(job, session):
    from starlette.concurrency import run_in_threadpool
    def resolve():
        with result_root(job) as root:
            return [source for source, _inputs in sources(job, root)]
    reference = await reference_binding(job)
    if reference is not None:
        job.provenance["result_integrity"]["catalog_reference_authority"] = reference
    current = await run_in_threadpool(resolve)
    receipts = []
    for source in current:
        session.info.setdefault("ngs_derived_catalog_intents", {}).setdefault(str(job.id), {})[source["session_id"]] = source
        receipts.append({"session_id": source["session_id"], "request_id": catalog_request_id(source),
                         "source_authority_sha256": identity_sha256(source)})
    return receipts


async def reference_binding(job):
    """Resolve existing immutable reference authority; never follow current head."""
    from molbio_ngs_database import molbio_ngs_session_factory
    from molbio_ngs_models import MolBioNGSReferenceRevision
    from services.molbio_ngs_references import get_reference_revision
    params = job.params
    revision_id = params.get("ngs_reference_revision_id")
    if not revision_id:
        receipt = job.provenance["result_integrity"]
        manifests = [item for item in receipt.get("artifacts", [])
                     if item.get("path") == "fastq_qc/qc_manifest.json"]
        if len(manifests) != 1:
            return None
        artifact = manifests[0]
        with result_root(job) as root, storage.open_verified_artifact_snapshot(
                root / artifact["path"], expected_sha256=artifact["sha256"], expected_size=artifact["size_bytes"]) as handle:
            import json
            value = json.load(handle)
        reference = value.get("reference", {})
        if (value.get("job_id") != str(job.id) or reference.get("expected_sha256") != params.get("reference_sequence_sha256")
                or reference.get("topology") not in {"linear", "circular"}):
            return None
        fasta = next((item for item in receipt["artifacts"] if item["path"] == ARTIFACTS["reference"]), None)
        if fasta is None:
            return None
        return {"schema": "bms.ngs.native-catalog-reference.v1", "owner": "accepted_sequence_qc_manifest",
            "authority_id": artifact["path"], "authority_sha256": artifact["sha256"],
            "topology": reference["topology"], "normalized_sequence_sha256": params["reference_sequence_sha256"],
            "fasta_sha256": fasta["sha256"], "fasta_size_bytes": fasta["size_bytes"]}
    async with molbio_ngs_session_factory() as domain:
        revision = await domain.get(MolBioNGSReferenceRevision, revision_id)
        if revision is None:
            raise storage.AlignmentSessionError("native immutable reference revision is missing")
        revision = await get_reference_revision(domain, revision.reference_id, revision.id)
        if (revision.global_domain_experiment_id != params.get("global_domain_experiment_id")
                or revision.normalized_sequence_sha256 != params.get("reference_sequence_sha256")
                or revision.canonical_fasta_sha256 != params.get("managed_reference_snapshot_sha256")
                or revision.canonical_fasta_size_bytes != params.get("managed_reference_snapshot_size_bytes")
                or revision.topology not in {"linear", "circular"}):
            raise storage.AlignmentSessionError("native immutable reference authority is cross-bound")
        return {"schema": "bms.ngs.native-catalog-reference.v1", "owner": "molbio_ngs_reference_revision",
            "authority_id": revision.id, "authority_sha256": revision.payload_sha256,
            "topology": revision.topology, "normalized_sequence_sha256": revision.normalized_sequence_sha256,
            "fasta_sha256": revision.canonical_fasta_sha256, "fasta_size_bytes": revision.canonical_fasta_size_bytes}


def alignment_sessions(job, root):
    """Project native receipt authority into the shared viewer, without QC aliases."""
    inventory = {item["relative_path"]: item for item in artifact_descriptors(job)}
    result = []
    for source, _inputs in sources(job, root):
        artifacts = {}
        for role, name in ARTIFACTS.items():
            item = inventory[name]
            artifacts[role] = {key: item[key] for key in
                ("artifact_id", "sha256", "size_bytes", "mime_type", "source_manifest_sha256")}
            artifacts[role].update(range_capable=True,
                url="/api/jobs/{}/ngs-artifacts/{}".format(job.id, item["artifact_id"]))
        result.append({"schema": "bms.ngs.native-alignment-session.v2",
            "job_id": str(job.id), "session_id": source["session_id"], "mode": source["mode"],
            "ready": True, "unavailable_reason": None,
            "reads_url": "/api/jobs/{}/alignment-sessions/{}/reads".format(job.id, source["session_id"]),
            "source_manifest_sha256": source["source_manifest_sha256"],
            "source_authority_sha256": identity_sha256(source),
            "artifact_set_sha256": source["source_artifact_set_sha256"],
            "reference": source["reference"], "artifacts": artifacts,
            "alignment_pair_sha256": source["alignment_pair_sha256"]})
    return result


def alignment_bundle(job, session_id):
    """Resolve exact native bytes for the existing signal worker snapshot owner."""
    root = resolve_persisted_job_result_root(job)
    with result_root(job) as pinned:
        matches = [item for item in alignment_sessions(job, pinned) if item["session_id"] == session_id]
    if len(matches) != 1:
        raise storage.AlignmentSessionError("native alignment session not found")
    artifacts = matches[0]["artifacts"]
    return (root / ARTIFACTS["alignment"], artifacts["alignment"],
            root / ARTIFACTS["alignment_index"], artifacts["alignment_index"])
