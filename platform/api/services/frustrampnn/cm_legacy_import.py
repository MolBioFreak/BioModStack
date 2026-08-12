"""Governed import of retained legacy CM FrustraMPNN landscapes.

The import is database-driven. Persisted CM request, record, artifact, and
landscape authorities select and validate every retained byte. A fsynced
journal coordinates SQLite publication with one atomic directory rename.
"""
from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import (
    ConformationalMappingArtifact,
    ConformationalMappingLandscapeRow,
    ConformationalMappingRecord,
    ConformationalMappingRequest,
    FrustraMPNNArtifact,
    FrustraMPNNLandscapeRow,
    FrustraMPNNResult,
    Job,
)
from services.conformational_mapping.contracts import canonical_json_bytes, canonical_sha256, validate_schema as validate_cm_schema
from services.conformational_mapping.request_builder import validate_materialized_coordinate_plan
from services.frustrampnn.contracts import AA_ORDER, validate_schema

IMPORT_VERSION = "cm_legacy_frustrampnn_import_v1"
_POLICY = {"id": "frustrampnn_class_v1", "high_max": -1.0, "minimal_min": 0.58}
_CLASS = {"high": "high", "neutral": "neutral", "minimally_frustrated": "minimal"}
_SOURCE_ROLES = {
    "normalized_pdb": "normalized.pdb",
    "structure_map": "cm_structure_map_v1.json",
    "frustrampnn_raw": "frustrampnn_raw.csv",
    "frustration_landscape": "cm_frustration_landscape_v1.json",
}
_ARTIFACTS = {
    "normalized_input.pdb": ("normalized_input", "chemical/x-pdb", None, None),
    "frustrampnn_structure_map_v1.json": ("structure_map", "application/json", "frustrampnn_structure_map", 1),
    "legacy_cm_structure_map_v1.json": ("legacy_structure_map", "application/json", "cm_structure_map", 1),
    "raw_frustrampnn.csv": ("raw_csv", "text/csv", None, None),
    "frustrampnn_landscape_legacy_cm_v1.json": ("landscape", "application/json", "cm_frustration_landscape", 1),
    "frustrampnn_summary_v1.json": ("summary", "application/json", "frustrampnn_summary", 1),
    "cm_legacy_import_manifest_v1.json": ("identity_authority", "application/json", "cm_legacy_frustrampnn_import", 1),
}
_AA3 = {
    "ALA":"A","CYS":"C","ASP":"D","GLU":"E","PHE":"F","GLY":"G","HIS":"H","ILE":"I",
    "LYS":"K","LEU":"L","MET":"M","ASN":"N","PRO":"P","GLN":"Q","ARG":"R","SER":"S",
    "THR":"T","VAL":"V","TRP":"W","TYR":"Y",
}


class CMLegacyFrustraMPNNImportError(RuntimeError):
    pass


def _digest(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stable_id(kind: str, *parts: Any) -> str:
    return _digest(canonical_json_bytes([kind, *parts]))


def _safe_component(value: Any, field: str) -> str:
    component = str(value or "")
    if not component or component in {".", ".."} or "/" in component or "\\" in component or Path(component).name != component:
        raise CMLegacyFrustraMPNNImportError(f"{field} is not a safe path component")
    return component


def _real_directory(path: Path, field: str) -> Path:
    absolute = path.absolute()
    chain = [absolute, *absolute.parents]
    for component in reversed(chain[:-1]):
        try:
            info = os.lstat(component)
        except OSError as exc:
            raise CMLegacyFrustraMPNNImportError(f"{field} is unavailable") from exc
        if stat.S_ISLNK(info.st_mode):
            raise CMLegacyFrustraMPNNImportError(f"{field} traverses a symlink")
    if not absolute.is_dir():
        raise CMLegacyFrustraMPNNImportError(f"{field} is not a real directory")
    return absolute.resolve(strict=True)


def _regular_file(path: Path, *, root: Path | None = None) -> bytes:
    if root is not None:
        try:
            relative = path.absolute().relative_to(root)
            resolved = path.resolve(strict=True)
            resolved.relative_to(root)
        except (OSError, ValueError) as exc:
            raise CMLegacyFrustraMPNNImportError("retained artifact escapes its persisted result root") from exc
        cursor = root
        for component in relative.parts[:-1]:
            cursor = cursor / component
            try:
                if stat.S_ISLNK(os.lstat(cursor).st_mode):
                    raise CMLegacyFrustraMPNNImportError("retained artifact traverses a symlink")
            except OSError as exc:
                raise CMLegacyFrustraMPNNImportError("retained artifact parent is unavailable") from exc
    if path.is_symlink() or not path.is_file():
        raise CMLegacyFrustraMPNNImportError(f"required retained artifact is absent or unsafe: {path}")
    before = path.stat(); payload = path.read_bytes(); after = path.stat()
    identity = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if identity(before) != identity(after) or len(payload) != before.st_size:
        raise CMLegacyFrustraMPNNImportError(f"retained artifact changed during import: {path}")
    return payload


def _json_payload(payload: bytes, field: str) -> dict[str, Any]:
    try: value = json.loads(payload)
    except Exception as exc: raise CMLegacyFrustraMPNNImportError(f"{field} is malformed JSON") from exc
    if not isinstance(value, dict): raise CMLegacyFrustraMPNNImportError(f"{field} must be a JSON object")
    return value


def _fsync_file_at(parent_fd: int, name: str, payload: bytes) -> None:
    flags=os.O_WRONLY|os.O_CREAT|os.O_EXCL|getattr(os,"O_NOFOLLOW",0)
    fd=os.open(name,flags,0o640,dir_fd=parent_fd)
    try:
        view=memoryview(payload)
        while view:
            written=os.write(fd,view); view=view[written:]
        os.fsync(fd)
    finally: os.close(fd)


def _fsync_fd(fd: int) -> None:
    os.fsync(fd)


def _mkdir_open_at(parent_fd: int, name: str, *, exist_ok: bool) -> int:
    try: os.mkdir(name,mode=0o750,dir_fd=parent_fd)
    except FileExistsError:
        if not exist_ok: raise CMLegacyFrustraMPNNImportError("compatibility publication directory already exists")
    return _open_dir_at(parent_fd,name)


def _open_dir_at(parent_fd:int,name:str)->int:
    try: return os.open(name,os.O_RDONLY|os.O_DIRECTORY|getattr(os,"O_NOFOLLOW",0),dir_fd=parent_fd)
    except OSError as exc: raise CMLegacyFrustraMPNNImportError("compatibility publication directory is unsafe") from exc


def _read_file_at(parent_fd:int,name:str)->bytes:
    try: fd=os.open(name,os.O_RDONLY|getattr(os,"O_NOFOLLOW",0),dir_fd=parent_fd)
    except OSError as exc: raise CMLegacyFrustraMPNNImportError("compatibility authority file is unavailable") from exc
    try:
        before=os.fstat(fd); chunks=[]
        while True:
            chunk=os.read(fd,1024*1024)
            if not chunk: break
            chunks.append(chunk)
        after=os.fstat(fd); payload=b"".join(chunks)
        if (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns) or len(payload)!=before.st_size:
            raise CMLegacyFrustraMPNNImportError("compatibility authority changed during read")
        return payload
    finally: os.close(fd)


def _entry_kind_at(parent_fd:int,name:str)->str|None:
    try: value=os.stat(name,dir_fd=parent_fd,follow_symlinks=False)
    except FileNotFoundError: return None
    if stat.S_ISREG(value.st_mode): return "file"
    if stat.S_ISDIR(value.st_mode): return "directory"
    return "unsafe"


def _replace_journal_at(output_parent_fd:int,value:Mapping[str,Any])->None:
    replacement="import-journal-v1.replacement"
    if _entry_kind_at(output_parent_fd,replacement) is not None:
        raise CMLegacyFrustraMPNNImportError("compatibility journal replacement already exists")
    _fsync_file_at(output_parent_fd,replacement,canonical_json_bytes(value))
    os.replace(replacement,"import-journal-v1.json",src_dir_fd=output_parent_fd,dst_dir_fd=output_parent_fd)
    _fsync_fd(output_parent_fd)


def _recover_journal_replacement_at(output_parent_fd:int)->None:
    """Discard an interrupted next-state write; the durable journal remains authoritative."""
    kind=_entry_kind_at(output_parent_fd,"import-journal-v1.replacement")
    if kind is None: return
    if kind!="file":
        raise CMLegacyFrustraMPNNImportError("compatibility journal replacement state is unsafe")
    journal_kind=_entry_kind_at(output_parent_fd,"import-journal-v1.json")
    staged_kind=_entry_kind_at(output_parent_fd,"results.staging")
    output_kind=_entry_kind_at(output_parent_fd,"results")
    if journal_kind not in {None,"file"}:
        raise CMLegacyFrustraMPNNImportError("compatibility journal state is unsafe")
    if journal_kind is None and (staged_kind is not None or output_kind is not None):
        raise CMLegacyFrustraMPNNImportError("orphaned compatibility journal replacement is ambiguous")
    os.unlink("import-journal-v1.replacement",dir_fd=output_parent_fd)
    _fsync_fd(output_parent_fd)


def _journal_value(*,state:str,job_id:str,request_sha256:str,candidates:list[dict[str,Any]])->dict[str,Any]:
    return {"schema_name":"cm_legacy_frustrampnn_import_journal","schema_version":1,"state":state,
            "job_id":job_id,"request_sha256":request_sha256,"candidates":candidates}


def _require_exact_journal_at(output_parent_fd:int,expected:Mapping[str,Any])->dict[str,Any]:
    value=_json_payload(_read_file_at(output_parent_fd,"import-journal-v1.json"),"compatibility journal")
    if value!=dict(expected):
        raise CMLegacyFrustraMPNNImportError("compatibility journal differs from rederived authority")
    return value


def _read_bundle_file_at(staged_fd:int,candidate_id:str,name:str)->bytes:
    bundle_fd=_open_dir_at(staged_fd,candidate_id)
    try: return _read_file_at(bundle_fd,name)
    finally: os.close(bundle_fd)


def _fsync_file(path: Path, payload: bytes) -> None:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o640)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(fd, view); view = view[written:]
        os.fsync(fd)
    finally: os.close(fd)


def _fsync_dir(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try: os.fsync(fd)
    finally: os.close(fd)


def _remove_exact_precommit_staging(output_parent: Path, candidate_ids: list[str]) -> None:
    """Remove only the exact importer-owned staging layout after rollback."""
    staged=output_parent/"results.staging"; journal=output_parent/"import-journal-v1.json"
    if staged.is_symlink() or not staged.is_dir() or journal.is_symlink() or not journal.is_file():
        raise CMLegacyFrustraMPNNImportError("pre-commit staging state is unsafe")
    expected_files={f"{candidate_id}/{name}" for candidate_id in candidate_ids for name in _ARTIFACTS}
    paths=list(staged.rglob("*"))
    if any(path.is_symlink() for path in paths):
        raise CMLegacyFrustraMPNNImportError("pre-commit staging contains a symlink")
    actual_files={path.relative_to(staged).as_posix() for path in paths if path.is_file()}
    actual_dirs={path.relative_to(staged).as_posix() for path in paths if path.is_dir()}
    if not actual_files.issubset(expected_files) or not actual_dirs.issubset(set(candidate_ids)):
        raise CMLegacyFrustraMPNNImportError("pre-commit staging layout differs from importer authority")
    for candidate_id in sorted(actual_dirs):
        bundle=staged/candidate_id
        for name in _ARTIFACTS:
            path=bundle/name
            if path.exists(): path.unlink()
        bundle.rmdir()
    staged.rmdir(); journal.unlink(); _fsync_dir(output_parent)


def _remove_exact_precommit_staging_at(output_parent_fd:int,candidate_ids:list[str])->None:
    """Descriptor-pinned cleanup of an allowlisted importer staging tree."""
    staged_fd=_mkdir_open_at(output_parent_fd,"results.staging",exist_ok=True)
    try:
        actual=set(os.listdir(staged_fd))
        if not actual.issubset(set(candidate_ids)):
            raise CMLegacyFrustraMPNNImportError("pre-commit staging layout differs from importer authority")
        for candidate_id in sorted(actual):
            bundle_fd=_open_dir_at(staged_fd,candidate_id)
            try:
                names=set(os.listdir(bundle_fd))
                if not names.issubset(set(_ARTIFACTS)):
                    raise CMLegacyFrustraMPNNImportError("pre-commit staging layout differs from importer authority")
                for name in names: os.unlink(name,dir_fd=bundle_fd)
            finally: os.close(bundle_fd)
            os.rmdir(candidate_id,dir_fd=staged_fd)
    finally: os.close(staged_fd)
    os.rmdir("results.staging",dir_fd=output_parent_fd)
    os.unlink("import-journal-v1.json",dir_fd=output_parent_fd); _fsync_fd(output_parent_fd)


def _cm_landscape_provenance(landscape: Mapping[str,Any])->dict[str,str]:
    provenance={"raw_csv_sha256":str(landscape["raw_csv_sha256"]),"checkpoint_sha256":str(landscape["checkpoint_sha256"]),
        "tool_sha256":str(landscape["tool_sha256"]),"threshold_policy_sha256":str(landscape["threshold_policy_sha256"])}
    if landscape.get("container_sha256") is not None: provenance["container_sha256"]=str(landscape["container_sha256"])
    return provenance


def _global_structure_map(*, job_id: str, candidate_id: str, cm_map: Mapping[str, Any], cm_map_sha256: str, normalized_pdb: bytes) -> dict[str, Any]:
    rows: list[dict[str, Any]] = []; sequence: list[str] = []
    for position, source in enumerate(cm_map.get("rows", [])):
        if not isinstance(source, Mapping) or source.get("status") != "mapped":
            raise CMLegacyFrustraMPNNImportError("legacy CM map contains a non-mapped residue")
        residue_name = str(source.get("residue_name") or ""); wt = _AA3.get(residue_name)
        if wt is None: raise CMLegacyFrustraMPNNImportError("legacy CM map residue cannot be translated")
        sequence.append(wt)
        rows.append({
            "entity_instance_id":str(source["entity_instance_id"]),"source_entity_id":source.get("source_entity_id"),
            "label_asym_id":source.get("label_asym_id"),"auth_asym_id":str(source["auth_asym_id"]),
            "label_seq_id":source.get("label_seq_id"),"auth_seq_id":int(source["auth_seq_id"]),
            "insertion_code":str(source.get("insertion_code") or ""),"sequence_index":int(source["sequence_index"]),
            "pdb_chain_id":str(source["pdb_chain_id"]),"pdb_residue_id":int(source["pdb_residue_id"]),
            "pdb_insertion_code":str(source.get("pdb_insertion_code") or ""),"model_position":position,
            "residue_name":residue_name,"wt":wt,"selected_model":int(source.get("source_model") or 1),
            "selected_altloc":str(source.get("selected_altloc") or ""),
            "backbone_complete":all(source.get("backbone_atoms",{}).get(atom) for atom in ("N","CA","C","O")),
            "backbone_atoms":dict(source.get("backbone_atoms") or {}),"status":"mapped","reason":source.get("reason"),
        })
    model_ready = "".join(sequence)
    result = {
        "schema_name":"frustrampnn_structure_map","schema_version":1,"target_id":str(cm_map["target_id"]),
        "parent_job_id":job_id,"candidate_id":candidate_id,"source_format":"pdb","source_sha256":_digest(normalized_pdb),
        "source_bytes":len(normalized_pdb),"identity_authority":"cm_complex_snapshot_v1","identity_domain":"source_authoritative",
        "authority_artifact_sha256":cm_map_sha256,"normalized_pdb_sha256":_digest(normalized_pdb),"selected_source_model":1,
        "altloc_policy":str(cm_map["altloc_policy"]),"normalizer_version":"frustrampnn_structure_normalizer_v1",
        "model_ready_sequence":model_ready,"model_ready_sequence_sha256":_digest(model_ready.encode("ascii")),
        "excluded_records":[],"rows":rows,
    }
    validate_schema("frustrampnn_structure_map_v1", result); return result


def _summary(*, job_id: str, candidate_id: str, landscape: Mapping[str, Any], landscape_sha256: str) -> dict[str, Any]:
    residues=list(landscape["residues"]); slots=[slot for residue in residues for slot in residue["slots"]]
    native=[slot for slot in slots if slot["native"]]; scoreable=[slot for slot in slots if slot["scoreable"]]
    if not native or not scoreable: raise CMLegacyFrustraMPNNImportError("legacy landscape has no scoreable authority")
    counts=lambda values:{name:sum(_CLASS.get(str(v.get("class")))==name for v in values) for name in ("high","neutral","minimal")}
    native_counts,all_counts=counts(native),counts(scoreable); grouped:dict[tuple[str,str],list[Mapping[str,Any]]]=defaultdict(list)
    for residue in residues: grouped[(str(residue["entity_instance_id"]),str(residue["auth_asym_id"]))].append(residue)
    support=[]
    for (entity,chain),values in sorted(grouped.items()):
        value_slots=[slot for residue in values for slot in residue["slots"]]
        support.append({"entity_instance_id":entity,"auth_asym_id":chain,"expected_residues":len(values),"mapped_residues":len(values),
                        "scoreable_residues":len(values),"expected_slots":len(values)*len(AA_ORDER),
                        "observed_slots":sum(slot["status"]!="missing" for slot in value_slots),
                        "scoreable_slots":sum(bool(slot["scoreable"]) for slot in value_slots)})
    missing=Counter(str(slot.get("reason")) for slot in slots if slot["status"]=="missing" and slot.get("reason"))
    result={"schema_name":"frustrampnn_summary","schema_version":1,"configuration_id":"frustrampnn_global_v1",
            "configuration_sha256":canonical_sha256({"compatibility_import":IMPORT_VERSION}),"target_id":str(landscape["target_id"]),
            "parent_job_id":job_id,"candidate_id":candidate_id,"landscape_sha256":landscape_sha256,
            "residue_support":{"expected":len(residues),"mapped":len(residues),"scoreable":len(residues),"excluded":0,"ambiguous":0},
            "slot_support":{"expected":len(residues)*len(AA_ORDER),"observed":sum(s["status"]!="missing" for s in slots),"scoreable":len(scoreable)},
            "missingness_by_reason":dict(sorted(missing.items())),"native_slot_counts":native_counts,
            "native_slot_fractions":{k:native_counts[k]/len(native) for k in native_counts},"complete_landscape_counts":all_counts,
            "complete_landscape_fractions":{k:all_counts[k]/len(scoreable) for k in all_counts},"support_by_entity_chain":support,
            "threshold_policy":dict(_POLICY),"threshold_policy_sha256":canonical_sha256(_POLICY)}
    validate_schema("frustrampnn_summary_v1",result); return result


def _manifest(*,job_id:str,candidate_id:str,invocation_id:str,request_sha256:str,source_sha256:str,source_records:Mapping[str,Mapping[str,Any]])->dict[str,Any]:
    return {"schema_name":"cm_legacy_frustrampnn_import","schema_version":1,"import_version":IMPORT_VERSION,
            "parent_job_id":job_id,"parent_workflow_id":"conformational_mapping","candidate_id":candidate_id,
            "invocation_id":invocation_id,"request_sha256":request_sha256,"source_sha256":source_sha256,
            "source_artifact_sha256":source_sha256,"artifacts":[dict(source_records[name]) for name in sorted(source_records)]}


def _terminal(*,job_id:str,candidate_id:str,invocation_id:str,request_sha256:str,source_sha256:str,
              landscape_sha256:str,artifact_records:Mapping[str,Mapping[str,Any]],ensemble:Mapping[str,Any])->dict[str,Any]:
    return {"schema_name":"cm_legacy_frustrampnn_import_terminal","schema_version":1,"request_sha256":request_sha256,
        "invocation_id":invocation_id,"component_id":"frustrampnn","component_contract_version":"1.0",
        "candidate_id":candidate_id,"parent_job_id":job_id,"parent_workflow_id":"conformational_mapping","status":"succeeded",
        "failure_class":None,"source_artifact":{"artifact_id":None,"sha256":source_sha256,"media_type":"chemical/x-pdb",
        "producer_stage":"conformational_mapping_legacy_import"},"runtime_identity":{},
        "artifacts":[dict(artifact_records[name]) for name in sorted(artifact_records)],
        "result_payload":{"schema_name":"cm_frustration_landscape","schema_version":1,"sha256":landscape_sha256},
        "started_at":ensemble.get("started_at"),"ended_at":ensemble.get("completed_at"),"duration_seconds":None,"gpu_provenance":None}


def _artifact_key(row: ConformationalMappingArtifact)->tuple[str,str]: return (str(row.candidate_id or ""),str(row.role))

def _cm_row_key(row: Any)->tuple[Any,...]:
    return (str(row.entity_instance_id),str(row.auth_asym_id),str(row.auth_seq_id),str(row.insertion_code or ""),int(row.sequence_index),str(row.wt),str(row.mutation_aa))


def _assert_row_matches_cm(row: FrustraMPNNLandscapeRow, cm_row: ConformationalMappingLandscapeRow)->None:
    expected_class=_CLASS.get(str(cm_row.score_class)) if cm_row.score_class is not None else None
    actual=(row.score,row.score_class,row.scoreable,row.status,row.reason)
    expected=(cm_row.score,expected_class or "neutral",bool(cm_row.scoreable),str(cm_row.status),cm_row.reason)
    if actual!=expected: raise CMLegacyFrustraMPNNImportError("projected global row differs from persisted CM landscape authority")


async def import_legacy_cm_frustrampnn(session:AsyncSession,*,job_id:str)->int:
    """Publish exact historical global results from persisted CM authorities."""
    job=await session.get(Job,job_id)
    request=await session.scalar(select(ConformationalMappingRequest).where(ConformationalMappingRequest.job_id==job_id))
    if job is None or str(job.model_id)!="conformational_mapping" or request is None or request.status!="completed":
        raise CMLegacyFrustraMPNNImportError("job is not a completed persisted conformational-mapping request")
    validate_materialized_coordinate_plan(request.request_json,request.coordinate_plan_json)
    if request.request_id!=job_id or request.request_sha256!=request.request_json.get("request_sha256"):
        raise CMLegacyFrustraMPNNImportError("CM request identity is not bound to selected job")
    expected_cardinality=int(request.coordinate_plan_json["expected_cardinality"])
    expected_plan_sha256=canonical_sha256({k:v for k,v in request.coordinate_plan_json.items() if k!="coordinate_plan_sha256"})
    if request.coordinate_plan_sha256!=expected_plan_sha256 or request.coordinate_plan_json.get("coordinate_plan_sha256")!=expected_plan_sha256:
        raise CMLegacyFrustraMPNNImportError("persisted CM coordinate-plan hash is invalid")
    if expected_cardinality!=5: raise CMLegacyFrustraMPNNImportError("this governed compatibility import requires the retained five-candidate DRT4 result")
    job_root=_real_directory(Path(str(job.output_dir)),"persisted job output root")
    canonical_root=_real_directory(job_root/"final/conformational_mapping/canonical_protenix/canonical_result","canonical CM result root")
    output_parent=job_root/"compatibility/frustrampnn"; output_root=output_parent/"results"; staged=output_parent/"results.staging"; journal=output_parent/"import-journal-v1.json"
    records=list((await session.scalars(select(ConformationalMappingRecord).where(ConformationalMappingRecord.request_id==request.request_id))).all())
    ensemble_rows=[row for row in records if row.record_type=="ensemble" and row.record_key=="primary"]
    if len(ensemble_rows)!=1 or canonical_sha256(ensemble_rows[0].payload_json)!=ensemble_rows[0].content_sha256:
        raise CMLegacyFrustraMPNNImportError("persisted CM ensemble authority is unavailable")
    ensemble=dict(ensemble_rows[0].payload_json); validate_cm_schema("cm_ensemble_v1",ensemble)
    if (
        ensemble.get("request_id")!=request.request_id
        or ensemble.get("request_sha256")!=request.request_sha256
        or ensemble.get("backend")!=request.backend
        or ensemble.get("expected_cardinality")!=expected_cardinality
        or ensemble.get("expected_coordinates")!=request.coordinate_plan_json.get("coordinates")
        or ensemble.get("terminal_status")!="complete"
    ):
        raise CMLegacyFrustraMPNNImportError("persisted CM ensemble does not match selected request and coordinate plan")
    candidate_ids=[_safe_component(value.get("candidate_id"),"candidate_id") for value in ensemble.get("candidates") or []]
    if len(candidate_ids)!=expected_cardinality or len(set(candidate_ids))!=expected_cardinality:
        raise CMLegacyFrustraMPNNImportError("persisted CM candidate cardinality differs from coordinate plan")
    artifacts=list((await session.scalars(select(ConformationalMappingArtifact).where(ConformationalMappingArtifact.request_id==request.request_id))).all())
    source_artifacts={_artifact_key(row):row for row in artifacts if row.candidate_id and row.role in _SOURCE_ROLES}
    if len(source_artifacts)!=expected_cardinality*len(_SOURCE_ROLES):
        raise CMLegacyFrustraMPNNImportError("persisted CM source artifact set is incomplete or duplicated")
    cm_rows=list((await session.scalars(select(ConformationalMappingLandscapeRow).where(ConformationalMappingLandscapeRow.request_id==request.request_id))).all())
    cm_rows_by_candidate:dict[str,dict[tuple[Any,...],ConformationalMappingLandscapeRow]]=defaultdict(dict)
    for row in cm_rows:
        key=_cm_row_key(row)
        if key in cm_rows_by_candidate[str(row.candidate_id)]: raise CMLegacyFrustraMPNNImportError("persisted CM landscape row identity is duplicated")
        cm_rows_by_candidate[str(row.candidate_id)][key]=row
    expected_rows=sum(len(values) for values in cm_rows_by_candidate.values())
    if expected_rows!=54000 or any(len(cm_rows_by_candidate[candidate_id])!=10800 for candidate_id in candidate_ids):
        raise CMLegacyFrustraMPNNImportError("persisted CM landscape cardinality differs from retained DRT4 authority")

    source_values:dict[str,dict[str,tuple[bytes,ConformationalMappingArtifact]]]={}
    for candidate_id in candidate_ids:
        source_values[candidate_id]={}
        for role in _SOURCE_ROLES:
            authority=source_artifacts[(candidate_id,role)]; path=Path(authority.storage_path)
            payload=_regular_file(path,root=canonical_root)
            if _digest(payload)!=authority.content_sha256 or len(payload)!=authority.size_bytes:
                raise CMLegacyFrustraMPNNImportError("retained source bytes differ from registered CM artifact authority")
            expected_path=canonical_root/"derived"/candidate_id/_SOURCE_ROLES[role]
            if path.resolve(strict=True)!=expected_path.resolve(strict=True):
                raise CMLegacyFrustraMPNNImportError("registered CM artifact path differs from canonical candidate path")
            source_values[candidate_id][role]=(payload,authority)

    job_fd=os.open(job_root,os.O_RDONLY|os.O_DIRECTORY|getattr(os,"O_NOFOLLOW",0))
    compatibility_fd=_mkdir_open_at(job_fd,"compatibility",exist_ok=True)
    output_parent_fd=_mkdir_open_at(compatibility_fd,"frustrampnn",exist_ok=True)
    _recover_journal_replacement_at(output_parent_fd)
    existing=list((await session.scalars(select(FrustraMPNNResult).where(FrustraMPNNResult.parent_job_id==job_id))).all())
    if existing:
        existing_by_candidate={str(row.candidate_id):row for row in existing}
        if set(existing_by_candidate)!=set(candidate_ids) or len(existing)!=expected_cardinality:
            raise CMLegacyFrustraMPNNImportError("existing global results are partial or have different authority")
        published_artifacts=list((await session.scalars(select(FrustraMPNNArtifact).where(FrustraMPNNArtifact.parent_job_id==job_id))).all())
        published_rows=list((await session.scalars(select(FrustraMPNNLandscapeRow).where(FrustraMPNNLandscapeRow.parent_job_id==job_id))).all())
        if len(published_artifacts)!=expected_cardinality*len(_ARTIFACTS) or len(published_rows)!=expected_rows:
            raise CMLegacyFrustraMPNNImportError("existing compatibility publication cardinality is incomplete")
        published_by_invocation:dict[str,list[FrustraMPNNArtifact]]=defaultdict(list)
        for artifact in published_artifacts: published_by_invocation[str(artifact.invocation_id)].append(artifact)
        expected_journal_candidates=[
            {"candidate_id":candidate_id,"manifest_sha256":existing_by_candidate[candidate_id].manifest_sha256,
             "row_count":len(cm_rows_by_candidate[candidate_id])}
            for candidate_id in candidate_ids
        ]
        staged_kind=_entry_kind_at(output_parent_fd,"results.staging")
        output_kind=_entry_kind_at(output_parent_fd,"results")
        journal_kind=_entry_kind_at(output_parent_fd,"import-journal-v1.json")
        if staged_kind=="directory" and output_kind is None and journal_kind=="file":
            journal_value=_json_payload(_read_file_at(output_parent_fd,"import-journal-v1.json"),"compatibility journal")
            state=str(journal_value.get("state"))
            if state not in {"staged","database_committed"}:
                raise CMLegacyFrustraMPNNImportError("compatibility recovery journal is invalid")
            _require_exact_journal_at(output_parent_fd,_journal_value(state=state,job_id=job_id,
                request_sha256=str(request.request_sha256),candidates=expected_journal_candidates))
            staged_fd=_open_dir_at(output_parent_fd,"results.staging")
            try:
                for artifact in published_artifacts:
                    relative=Path(artifact.storage_path).relative_to(output_root)
                    if len(relative.parts)!=2:
                        raise CMLegacyFrustraMPNNImportError("staged compatibility artifact path is invalid")
                    payload=_read_bundle_file_at(staged_fd,relative.parts[0],relative.parts[1])
                    if _digest(payload)!=artifact.content_sha256 or len(payload)!=artifact.size_bytes:
                        raise CMLegacyFrustraMPNNImportError("staged compatibility bytes differ from database authority")
                os.replace("results.staging","results",src_dir_fd=output_parent_fd,dst_dir_fd=output_parent_fd); _fsync_fd(output_parent_fd)
            finally: os.close(staged_fd)
            journal_value["state"]="complete"; _replace_journal_at(output_parent_fd,journal_value)
            output_kind="directory"
        elif output_kind=="directory" and journal_kind=="file":
            journal_value=_json_payload(_read_file_at(output_parent_fd,"import-journal-v1.json"),"compatibility journal")
            if journal_value.get("state")=="database_committed":
                _require_exact_journal_at(output_parent_fd,_journal_value(state="database_committed",job_id=job_id,
                    request_sha256=str(request.request_sha256),candidates=expected_journal_candidates))
                journal_value["state"]="complete"; _replace_journal_at(output_parent_fd,journal_value)
            else:
                _require_exact_journal_at(output_parent_fd,_journal_value(state="complete",job_id=job_id,
                    request_sha256=str(request.request_sha256),candidates=expected_journal_candidates))
        if output_kind!="directory": raise CMLegacyFrustraMPNNImportError("published compatibility tree is unavailable")
        output_fd=_open_dir_at(output_parent_fd,"results")
        try:
            for artifact in published_artifacts:
                relative=Path(artifact.storage_path).relative_to(output_root)
                if len(relative.parts)!=2 or relative.parts[0] not in candidate_ids or relative.parts[1] not in _ARTIFACTS:
                    raise CMLegacyFrustraMPNNImportError("published compatibility artifact path is invalid")
                payload=_read_bundle_file_at(output_fd,relative.parts[0],relative.parts[1])
                if _digest(payload)!=artifact.content_sha256 or len(payload)!=artifact.size_bytes:
                    raise CMLegacyFrustraMPNNImportError("published compatibility artifact bytes have drifted")
        finally: os.close(output_fd)
        expected_rows_by_id:dict[str,dict[str,Any]]={}; cm_provenance_by_candidate:dict[str,dict[str,str]]={}
        for candidate_id in candidate_ids:
            result=existing_by_candidate[candidate_id]; invocation_id=f"frustrampnn:{job_id}:{candidate_id}"
            values=source_values[candidate_id]; pdb=values["normalized_pdb"][0]; raw=values["frustrampnn_raw"][0]
            legacy_map_bytes=values["structure_map"][0]; legacy_map=_json_payload(legacy_map_bytes,"legacy CM structure map")
            landscape_bytes=values["frustration_landscape"][0]; landscape=_json_payload(landscape_bytes,"legacy CM landscape")
            cm_provenance_by_candidate[candidate_id]=_cm_landscape_provenance(landscape)
            projected_map=_global_structure_map(job_id=job_id,candidate_id=candidate_id,cm_map=legacy_map,
                cm_map_sha256=_digest(legacy_map_bytes),normalized_pdb=pdb)
            projected_map_bytes=canonical_json_bytes(projected_map)
            expected_summary=_summary(job_id=job_id,candidate_id=candidate_id,landscape=landscape,landscape_sha256=_digest(landscape_bytes))
            owned=published_by_invocation[invocation_id]; by_role={str(a.role):a for a in owned}
            if len(by_role)!=len(_ARTIFACTS): raise CMLegacyFrustraMPNNImportError("existing artifact roles are duplicated")
            expected_payloads={"normalized_input.pdb":pdb,"frustrampnn_structure_map_v1.json":projected_map_bytes,
                "legacy_cm_structure_map_v1.json":legacy_map_bytes,"raw_frustrampnn.csv":raw,
                "frustrampnn_landscape_legacy_cm_v1.json":landscape_bytes,"frustrampnn_summary_v1.json":canonical_json_bytes(expected_summary)}
            manifest_records={}
            for name,payload in expected_payloads.items():
                role,media,schema_name,schema_version=_ARTIFACTS[name]
                manifest_records[name]={"relative_path":name,"role":role,"media_type":media,"schema_name":schema_name,
                    "schema_version":schema_version,"sha256":_digest(payload),"bytes":len(payload)}
            expected_manifest=_manifest(job_id=job_id,candidate_id=candidate_id,invocation_id=invocation_id,
                request_sha256=request.request_sha256,source_sha256=_digest(pdb),source_records=manifest_records)
            expected_terminal=_terminal(job_id=job_id,candidate_id=candidate_id,invocation_id=invocation_id,
                request_sha256=request.request_sha256,source_sha256=_digest(pdb),landscape_sha256=_digest(landscape_bytes),
                artifact_records={**manifest_records,"cm_legacy_import_manifest_v1.json":{
                    "relative_path":"cm_legacy_import_manifest_v1.json","role":"identity_authority","media_type":"application/json",
                    "schema_name":"cm_legacy_frustrampnn_import","schema_version":1,"sha256":canonical_sha256(expected_manifest),
                    "bytes":len(canonical_json_bytes(expected_manifest))}},ensemble=ensemble)
            expected_artifact_records={**manifest_records,"cm_legacy_import_manifest_v1.json":{
                "relative_path":"cm_legacy_import_manifest_v1.json","role":"identity_authority","media_type":"application/json",
                "schema_name":"cm_legacy_frustrampnn_import","schema_version":1,"sha256":canonical_sha256(expected_manifest),
                "bytes":len(canonical_json_bytes(expected_manifest))}}
            artifacts_by_path={str(artifact.relative_path):artifact for artifact in owned}
            if set(artifacts_by_path)!=set(expected_artifact_records):
                raise CMLegacyFrustraMPNNImportError("existing compatibility artifact inventory has drifted")
            for name,expected_record in expected_artifact_records.items():
                artifact=artifacts_by_path[name]
                expected_artifact_id=_stable_id(IMPORT_VERSION,job_id,invocation_id,name,expected_record["sha256"])
                if (
                    artifact.artifact_id!=expected_artifact_id or artifact.parent_job_id!=job_id or artifact.invocation_id!=invocation_id
                    or artifact.role!=expected_record["role"] or artifact.relative_path!=name
                    or Path(artifact.storage_path)!=output_root/candidate_id/name
                    or artifact.content_sha256!=expected_record["sha256"] or artifact.size_bytes!=expected_record["bytes"]
                    or artifact.media_type!=expected_record["media_type"] or artifact.metadata_json!=expected_record
                ): raise CMLegacyFrustraMPNNImportError("existing compatibility artifact authority has drifted")
            terminal=result.terminal_result_json if isinstance(result.terminal_result_json,dict) else {}
            expected_parent_metadata={"import_version":IMPORT_VERSION,"cm_request_id":request.request_id,
                "cm_request_sha256":request.request_sha256,"legacy_landscape_sha256":_digest(landscape_bytes),
                "legacy_structure_map_sha256":_digest(legacy_map_bytes)}
            if (
                result.manifest_json!=expected_manifest or canonical_sha256(expected_manifest)!=result.manifest_sha256
                or result.summary_json!=expected_summary or canonical_sha256(expected_summary)!=result.summary_sha256
                or result.request_sha256!=request.request_sha256 or result.source_artifact_sha256!=_digest(pdb)
                or result.parent_workflow_id!="conformational_mapping" or result.requiredness!="required"
                or result.design_id is not None or result.source_artifact_id is not None
                or result.parent_metadata_json!=expected_parent_metadata
                or result.runtime_identity_json or result.assigned_gpu_json
                or terminal!=expected_terminal
            ): raise CMLegacyFrustraMPNNImportError("existing compatibility provenance has drifted")
            provenance={"landscape_sha256":canonical_sha256(landscape),"structure_map_sha256":_digest(projected_map_bytes),
                "normalized_pdb_sha256":_digest(pdb),"raw_csv_sha256":_digest(raw),"threshold_policy":dict(_POLICY),
                "threshold_policy_sha256":canonical_sha256(_POLICY)}
            projected_rows={(str(v["entity_instance_id"]),str(v["auth_asym_id"]),str(v["auth_seq_id"]),
                str(v.get("insertion_code") or ""),int(v["sequence_index"]),str(v["wt"])):v for v in projected_map["rows"]}
            for residue in landscape["residues"]:
                prefix=(str(residue["entity_instance_id"]),str(residue["auth_asym_id"]),str(residue["auth_seq_id"]),
                    str(residue.get("insertion_code") or ""),int(residue["sequence_index"]),str(residue["wt"]))
                mapped=projected_rows[prefix]
                residue_json={**{k:v for k,v in residue.items() if k!="slots"},"pdb_chain_id":mapped["pdb_chain_id"],"model_position":mapped["model_position"]}
                for slot in residue["slots"]:
                    key=(*prefix,str(slot["mutation_aa"])); row_id=_stable_id(IMPORT_VERSION,job_id,invocation_id,*key)
                    expected_rows_by_id[row_id]={"target_id":str(landscape["target_id"]),
                        "row_json":{"residue":residue_json,"slot":dict(slot)},"provenance":provenance}
        for row in published_rows:
            candidate_id=str(row.invocation_id).rsplit(":",1)[-1]; authority=cm_rows_by_candidate[candidate_id].get(_cm_row_key(row))
            if authority is None: raise CMLegacyFrustraMPNNImportError("global row has no persisted CM authority")
            if authority.provenance_json!=cm_provenance_by_candidate[candidate_id]:
                raise CMLegacyFrustraMPNNImportError("persisted CM row provenance differs from retained landscape authority")
            _assert_row_matches_cm(row,authority)
            expected=expected_rows_by_id.get(row.id)
            if expected is None or row.row_json!=expected["row_json"] or row.provenance_json!=expected["provenance"]:
                raise CMLegacyFrustraMPNNImportError("global row JSON or provenance differs from retained authority")
            if row.target_id!=expected["target_id"]:
                raise CMLegacyFrustraMPNNImportError("global row target identity differs from retained authority")
        os.close(output_parent_fd); os.close(compatibility_fd); os.close(job_fd)
        return len(existing)

    staged_kind=_entry_kind_at(output_parent_fd,"results.staging"); output_kind=_entry_kind_at(output_parent_fd,"results")
    journal_kind=_entry_kind_at(output_parent_fd,"import-journal-v1.json")
    if staged_kind is None and output_kind is None and journal_kind=="file":
        # No database or filesystem publication exists. This reserved file can only
        # be an interrupted initial marker, including a truncated first write.
        os.unlink("import-journal-v1.json",dir_fd=output_parent_fd)
        _fsync_fd(output_parent_fd)
        journal_kind=None
    if staged_kind=="directory" and journal_kind=="file" and output_kind is None:
        journal_value=_json_payload(_read_file_at(output_parent_fd,"import-journal-v1.json"),"compatibility journal")
        if journal_value.get("state") in {"preparing","staged"} and journal_value.get("job_id")==job_id and journal_value.get("request_sha256")==request.request_sha256:
            _remove_exact_precommit_staging_at(output_parent_fd,candidate_ids)
            staged_kind=None; journal_kind=None
    if output_kind is not None or staged_kind is not None or journal_kind is not None:
        raise CMLegacyFrustraMPNNImportError("compatibility publication path already exists without matching database authority")
    journal_value={"schema_name":"cm_legacy_frustrampnn_import_journal","schema_version":1,"state":"preparing","job_id":job_id,
                   "request_sha256":request.request_sha256,"candidates":[]}
    _fsync_file_at(output_parent_fd,"import-journal-v1.json",canonical_json_bytes(journal_value)); _fsync_fd(output_parent_fd)
    staged_fd=_mkdir_open_at(output_parent_fd,"results.staging",exist_ok=False)
    result_values=[]; artifact_values=[]; row_values=[]; journal_candidates=[]
    for candidate_id in candidate_ids:
        values=source_values[candidate_id]; pdb=values["normalized_pdb"][0]; raw=values["frustrampnn_raw"][0]
        legacy_map_bytes=values["structure_map"][0]; legacy_map=_json_payload(legacy_map_bytes,"legacy CM structure map")
        legacy_landscape_bytes=values["frustration_landscape"][0]; legacy_landscape=_json_payload(legacy_landscape_bytes,"legacy CM landscape")
        validate_cm_schema("cm_structure_map_v1",legacy_map); validate_cm_schema("cm_frustration_landscape_v1",legacy_landscape)
        map_records=[row for row in records if row.record_type=="structure_map" and row.record_key==candidate_id]
        if len(map_records)!=1 or map_records[0].payload_json!=legacy_map or canonical_sha256(legacy_map)!=map_records[0].content_sha256:
            raise CMLegacyFrustraMPNNImportError("legacy CM map differs from persisted canonical record authority")
        if _digest(raw)!=legacy_landscape.get("raw_csv_sha256") or _digest(pdb)!=legacy_map.get("normalized_pdb_sha256"):
            raise CMLegacyFrustraMPNNImportError("retained candidate hashes differ")
        invocation_id=f"frustrampnn:{job_id}:{candidate_id}"; bundle=staged/candidate_id
        bundle_fd=_mkdir_open_at(staged_fd,candidate_id,exist_ok=False)
        projected_map=_global_structure_map(job_id=job_id,candidate_id=candidate_id,cm_map=legacy_map,cm_map_sha256=_digest(legacy_map_bytes),normalized_pdb=pdb)
        projected_map_bytes=canonical_json_bytes(projected_map)
        summary=_summary(job_id=job_id,candidate_id=candidate_id,landscape=legacy_landscape,landscape_sha256=_digest(legacy_landscape_bytes)); summary_bytes=canonical_json_bytes(summary)
        payloads={"normalized_input.pdb":pdb,"frustrampnn_structure_map_v1.json":projected_map_bytes,
                  "legacy_cm_structure_map_v1.json":legacy_map_bytes,"raw_frustrampnn.csv":raw,
                  "frustrampnn_landscape_legacy_cm_v1.json":legacy_landscape_bytes,"frustrampnn_summary_v1.json":summary_bytes}
        artifact_records={}
        for name,payload in payloads.items():
            role,media,schema_name,schema_version=_ARTIFACTS[name]; _fsync_file_at(bundle_fd,name,payload)
            artifact_records[name]={"relative_path":name,"role":role,"media_type":media,"schema_name":schema_name,
                                    "schema_version":schema_version,"sha256":_digest(payload),"bytes":len(payload)}
        source_sha=_digest(pdb); manifest=_manifest(job_id=job_id,candidate_id=candidate_id,invocation_id=invocation_id,
                                                   request_sha256=str(request.request_sha256),source_sha256=source_sha,source_records=artifact_records)
        manifest_bytes=canonical_json_bytes(manifest); _fsync_file_at(bundle_fd,"cm_legacy_import_manifest_v1.json",manifest_bytes)
        artifact_records["cm_legacy_import_manifest_v1.json"]={"relative_path":"cm_legacy_import_manifest_v1.json","role":"identity_authority",
            "media_type":"application/json","schema_name":"cm_legacy_frustrampnn_import","schema_version":1,"sha256":_digest(manifest_bytes),"bytes":len(manifest_bytes)}
        terminal=_terminal(job_id=job_id,candidate_id=candidate_id,invocation_id=invocation_id,request_sha256=str(request.request_sha256),
            source_sha256=source_sha,landscape_sha256=_digest(legacy_landscape_bytes),artifact_records=artifact_records,ensemble=ensemble)
        result_values.append(FrustraMPNNResult(parent_job_id=job_id,invocation_id=invocation_id,parent_workflow_id="conformational_mapping",
            candidate_id=candidate_id,design_id=None,requiredness="required",request_sha256=str(request.request_sha256),source_artifact_id=None,
            source_artifact_sha256=source_sha,manifest_sha256=_digest(manifest_bytes),manifest_json=manifest,summary_sha256=_digest(summary_bytes),summary_json=summary,
            runtime_identity_json={},assigned_gpu_json={},terminal_result_json=terminal,parent_metadata_json={"import_version":IMPORT_VERSION,
            "cm_request_id":request.request_id,"cm_request_sha256":request.request_sha256,"legacy_landscape_sha256":_digest(legacy_landscape_bytes),
            "legacy_structure_map_sha256":_digest(legacy_map_bytes)},created_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        for name,record in artifact_records.items():
            artifact_values.append(FrustraMPNNArtifact(artifact_id=_stable_id(IMPORT_VERSION,job_id,invocation_id,name,record["sha256"]),parent_job_id=job_id,
                invocation_id=invocation_id,role=record["role"],relative_path=name,storage_path=os.fspath(output_root/candidate_id/name),content_sha256=record["sha256"],
                size_bytes=record["bytes"],media_type=record["media_type"],metadata_json=record))
        provenance={"landscape_sha256":canonical_sha256(legacy_landscape),"structure_map_sha256":_digest(projected_map_bytes),
                    "normalized_pdb_sha256":source_sha,"raw_csv_sha256":_digest(raw),"threshold_policy":dict(_POLICY),
                    "threshold_policy_sha256":canonical_sha256(_POLICY)}
        projected_rows={(str(row["entity_instance_id"]),str(row["auth_asym_id"]),str(row["auth_seq_id"]),str(row.get("insertion_code") or ""),
                         int(row["sequence_index"]),str(row["wt"])):row for row in projected_map["rows"]}
        seen=set()
        for residue in legacy_landscape["residues"]:
            residue_prefix=(str(residue["entity_instance_id"]),str(residue["auth_asym_id"]),str(residue["auth_seq_id"]),str(residue.get("insertion_code") or ""),int(residue["sequence_index"]),str(residue["wt"]))
            projected_row=projected_rows.get(residue_prefix)
            if projected_row is None: raise CMLegacyFrustraMPNNImportError("legacy landscape residue is absent from projected map")
            residue_json={**{k:v for k,v in residue.items() if k!="slots"},"pdb_chain_id":projected_row["pdb_chain_id"],"model_position":projected_row["model_position"]}
            for slot in residue["slots"]:
                key=(*residue_prefix,str(slot["mutation_aa"])); authority=cm_rows_by_candidate[candidate_id].get(key)
                if authority is None or key in seen: raise CMLegacyFrustraMPNNImportError("legacy landscape slot differs from persisted CM row authority")
                if authority.provenance_json!=_cm_landscape_provenance(legacy_landscape):
                    raise CMLegacyFrustraMPNNImportError("persisted CM row provenance differs from retained landscape authority")
                seen.add(key); score_class=_CLASS.get(str(slot.get("class"))) if slot.get("class") is not None else None
                row=FrustraMPNNLandscapeRow(id=_stable_id(IMPORT_VERSION,job_id,invocation_id,*key),parent_job_id=job_id,invocation_id=invocation_id,
                    target_id=str(legacy_landscape["target_id"]),entity_instance_id=key[0],auth_asym_id=key[1],auth_seq_id=key[2],insertion_code=key[3],
                    sequence_index=key[4],wt=key[5],mutation_aa=key[6],score=slot.get("score"),score_class=score_class or "neutral",scoreable=bool(slot["scoreable"]),
                    status=str(slot["status"]),reason=slot.get("reason"),row_json={"residue":residue_json,"slot":dict(slot)},provenance_json=provenance)
                _assert_row_matches_cm(row,authority); row_values.append(row)
        if seen!=set(cm_rows_by_candidate[candidate_id]): raise CMLegacyFrustraMPNNImportError("legacy landscape does not close over persisted CM row authority")
        _fsync_fd(bundle_fd); os.close(bundle_fd)
        journal_candidates.append({"candidate_id":candidate_id,"manifest_sha256":_digest(manifest_bytes),"row_count":len(seen)})
    _fsync_fd(staged_fd)
    journal_value={"schema_name":"cm_legacy_frustrampnn_import_journal","schema_version":1,"state":"staged","job_id":job_id,
                   "request_sha256":request.request_sha256,"candidates":journal_candidates}
    _replace_journal_at(output_parent_fd,journal_value)
    session.add_all(result_values); await session.flush(); session.add_all(artifact_values); session.add_all(row_values); await session.flush()
    await session.commit()
    journal_value["state"]="database_committed"
    _replace_journal_at(output_parent_fd,journal_value)
    os.replace("results.staging","results",src_dir_fd=output_parent_fd,dst_dir_fd=output_parent_fd); _fsync_fd(output_parent_fd)
    journal_value["state"]="complete"
    _replace_journal_at(output_parent_fd,journal_value)
    os.close(staged_fd); os.close(output_parent_fd); os.close(compatibility_fd); os.close(job_fd)
    return len(result_values)
