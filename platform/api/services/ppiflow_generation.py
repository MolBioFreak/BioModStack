"""Dependency-light initial PPIFlow request/transport authority (no model imports).

Historical antibody_denovo/generator_backbone_refine is deliberately not handled.
Defaults and native mappings are owned by config/models/ppiflow.yaml.
"""
from __future__ import annotations

import copy
import csv
import hashlib
import json
from pathlib import Path
import re
import shutil

import yaml

SOURCE_REVISION = "000ce45a4411e7b97c1523a22c0f1bece7ede5fc"
SCHEMA_VERSION = "ppiflow-generation/1"
MODEL_YAML = Path(__file__).resolve().parents[1] / "config/models/ppiflow.yaml"
MODES = {
    "protein_binder": ("sample_binder.py", "configs/inference_binder.yaml", "binder.ckpt"),
    "antibody_binder": ("sample_antibody_nanobody.py", "configs/inference_nanobody.yaml", "antibody.ckpt"),
    "nanobody_binder": ("sample_antibody_nanobody.py", "configs/inference_nanobody.yaml", "nanobody.ckpt"),
}


def parameter_contract(mode: str) -> list[dict]:
    """Return the closed, typed, mode-specific inventory, including native mappings."""
    if mode not in MODES:
        raise ValueError(f"Unsupported PPIFlow initial-generation mode: {mode}")
    model = yaml.safe_load(MODEL_YAML.read_text())
    keys = next(m["params"] for m in model["modes"] if m["id"] == mode)
    by_name = {p["name"]: p for p in model["params"]}
    return [copy.deepcopy(by_name[k]) for k in keys]


def ppiflow_generation_inventory(mode: str) -> dict:
    """Typed controls plus visible checkpoint-owned architecture and native limits."""
    fields = parameter_contract(mode)
    model = yaml.safe_load(MODEL_YAML.read_text())
    return {"schema_version": SCHEMA_VERSION, "mode": mode, "parameters": fields,
            "profile": model["native_profiles"][mode], "assets": selected_assets(mode),
            "native_behavior": {
                "output_kind": "backbone; generated positions are alanine, not sequence-design results",
                "global_seed": "No native global seed control; dataset_seed is not a global seed",
                "antibody_retry_limit": 20 if mode != "protein_binder" else None,
                "antibody_native_retention": "rmsd_framework < 1 and no backbone clash" if mode != "protein_binder" else None,
                "chain_case": "Native preprocessing uppercases structural dictionary keys; BMS does not rewrite requests",
            }}


def normalize_ppiflow_generation_params(mode: str, params: dict) -> dict:
    """Validate native inputs/settings and expand only mode-applicable defaults.

    Caller supplies scientific params only; placement and Project remain with
    their existing owners. No native imports, checkpoint reads or proof gates.
    """
    fields = parameter_contract(mode)
    names = {p["name"] for p in fields}
    unknown = set(params) - names
    if unknown:
        raise ValueError(f"Unknown {mode} settings: {', '.join(sorted(unknown))}")
    out = copy.deepcopy(params)
    for field in fields:
        key = field["name"]
        if key not in out and "default" in field:
            out[key] = copy.deepcopy(field["default"])
        value = out.get(key)
        if value is None:
            if field.get("required") or (key in out and field.get("default") is not None):
                raise ValueError(f"{key} cannot be null for {mode}")
            continue
        kind = field["type"]
        valid = {
            "integer": type(value) is int,
            "number": type(value) in (int, float),
            "boolean": type(value) is bool,
            "string": isinstance(value, str),
            "file": isinstance(value, str),
        }[kind]
        if not valid:
            raise ValueError(f"{key} must be {kind}")
        if kind == "number":
            import math
            if not math.isfinite(value):
                raise ValueError(f"{key} must be finite")
        if "enum" in field and value not in field["enum"]:
            raise ValueError(f"{key} must be one of {field['enum']}")
        if "minimum" in field and value < field["minimum"]:
            raise ValueError(f"{key} must be >= {field['minimum']}")
        if "maximum" in field and value > field["maximum"]:
            raise ValueError(f"{key} must be <= {field['maximum']}")
        if "pattern" in field and not re.fullmatch(field["pattern"], value):
            raise ValueError(f"{key} has invalid native syntax")
    if mode == "protein_binder":
        if bool(out.get("target_pdb")) == bool(out.get("input_csv")):
            raise ValueError("Specify exactly one target_pdb or input_csv")
        if out.get("target_pdb") and not out.get("binder_chain"):
            # process_file directly indexes chain2_id, even for a target-only PDB.
            raise ValueError("binder_chain is required by native PDB preprocessing (virtual binder identity)")
        if out["samples_min_length"] >= out["samples_max_length"]:
            raise ValueError("samples_max_length is exclusive and must exceed samples_min_length")
        if out["samples_per_target"] % out["samples_batch_size"]:
            raise ValueError("Native samples_per_target must be divisible by samples_batch_size")
        if out.get("specified_hotspots") is None:
            if (out["sample_hotspot_rate_min"], out["sample_hotspot_rate_max"]) != (0.2, 0.5):
                raise ValueError("Native CLI requires specified_hotspots when customizing hotspot rates")
    else:
        for key in ("target_pdb", "framework_pdb", "antigen_chain", "heavy_chain", "specified_hotspots"):
            if not out.get(key):
                raise ValueError(f"{key} is required by the native antibody/nanobody generator")
        if mode == "antibody_binder" and not out.get("light_chain"):
            raise ValueError("light_chain is required for antibody_binder")
        if mode == "nanobody_binder" and out.get("light_chain") is not None:
            raise ValueError("nanobody_binder uses the native heavy-only operation")
        chain = out["antigen_chain"]
        if out["specified_hotspots"][0] != chain:
            raise ValueError("Hotspot chain must match antigen_chain")
        parts = out["cdr_length"].split(",")
        if len(parts) % 2:
            raise ValueError("cdr_length must contain native CDR-name,low-high pairs")
        lengths = {}
        for name, bounds in zip(parts[::2], parts[1::2]):
            match = re.fullmatch(r"(\d+)-(\d+)", bounds)
            if not match or int(match[1]) > int(match[2]):
                raise ValueError("cdr_length ranges must be nonnegative and ordered")
            lengths[name.strip()] = bounds
        needed = {"CDRH1", "CDRH2", "CDRH3"}
        if mode == "antibody_binder":
            needed |= {"CDRL1", "CDRL2", "CDRL3"}
        if not needed <= lengths.keys():
            raise ValueError("cdr_length is missing native chain CDR ranges")
    hotspots = out.get("specified_hotspots")
    if mode == "protein_binder" and hotspots is not None:
        if not hotspots or hotspots[0] != out["target_chain"]:
            raise ValueError("Hotspot chain must match target_chain")
        try:
            for token in hotspots.split(","):
                int(token.strip(out["target_chain"]))
        except ValueError as exc:
            raise ValueError("Protein-binder native hotspots use integer residue numbers, not insertion codes") from exc
    return out


def selected_assets(mode: str) -> dict:
    """Exact initial-generation closure; no partial-flow/MPNN/prediction assets."""
    script, config, checkpoint = MODES[mode]
    return {"image": "ppiflow.sif", "weights": [f"ppiflow/{checkpoint}"],
            "entrypoint": script, "config": config, "source_revision": SOURCE_REVISION,
            "process": "RunPPIFlowGeneration", "checkpoint_mount": f"/opt/ppiflow/ckpt/{checkpoint}"}


def _digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def materialize_ppiflow_generation_request(mode: str, params: dict, directory: str | Path,
                                          source_identity: dict | None = None,
                                          authorize_source=None, requested_settings: dict | None = None) -> dict:
    """Snapshot sources into one portable request directory; return compiler params.

    Existing source/Job owner resolves permissions/conversions before calling.
    API callers pass authorize_source(Path) to apply that existing authority to
    every file, including CSV dependencies. Trusted internal callers may omit it.
    CSV processed_path members are copied as bytes, never unpickled here.
    source_identity is optional provenance, not a new admission requirement.
    """
    effective = normalize_ppiflow_generation_params(mode, params)
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=False)
    inputs = root / "inputs"
    inputs.mkdir()
    bindings = []

    def snapshot(value: str, name: str, role: str) -> str:
        source = Path(value).resolve(strict=True)
        if authorize_source is not None:
            authorize_source(source)
        dest = inputs / name
        shutil.copyfile(source, dest)
        relative = dest.relative_to(root).as_posix()
        bindings.append({"role": role, "original_path": str(source), "path": relative,
                         "sha256": _digest(dest), "size": dest.stat().st_size})
        return relative

    transport = copy.deepcopy(effective)
    for key in ("target_pdb", "framework_pdb"):
        if transport.get(key):
            transport[key] = snapshot(transport[key], f"{key}.pdb", key)
    source_rows = []
    if transport.get("input_csv"):
        source_csv = Path(transport["input_csv"]).resolve(strict=True)
        original = snapshot(str(source_csv), "original.csv", "input_csv")
        with source_csv.open(newline="") as handle:
            reader = csv.DictReader(handle)
            fieldnames = reader.fieldnames
            rows = list(reader)
        for index, row in enumerate(rows):
            target_name = row["pdb_name"]
            if not target_name or Path(target_name).name != target_name or target_name in {".", ".."}:
                raise ValueError("Native CSV pdb_name must remain within the owned output directory")
            member = Path(row["processed_path"])
            if not member.is_absolute():
                member = source_csv.parent / member
            relative = snapshot(str(member), f"processed_{index}.pkl", f"csv_row:{index}")
            source_rows.append({"source_row_index": index, "native_target_name": row["pdb_name"],
                                "processed_path": relative, "source_csv": original})
            row["processed_path"] = relative
        target = inputs / "native_input.csv"
        with target.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rows)
        transport["input_csv"] = target.relative_to(root).as_posix()
    else:
        source_rows = [{"source_row_index": 0, "native_target_name": "bms_target"}]
    payload = {"schema_version": SCHEMA_VERSION, "mode": mode,
               "requested_settings": copy.deepcopy(params if requested_settings is None else requested_settings),
               "effective_settings": effective, "transport_settings": transport,
               "source_bindings": bindings, "source_identity": source_identity or {},
               "source_rows": source_rows, "runtime": selected_assets(mode)}
    (root / "request.json").write_text(json.dumps(payload, indent=2, allow_nan=False) + "\n")
    return {"ppiflow_generation_request": str(root.resolve())}


def prepare_ppiflow_generation_request(mode: str, params: dict, directory: str | Path,
                                       *, allowed_roots, source_identity=None,
                                       requested_settings=None, retain_prepared: bool = False) -> dict:
    """Prepare once, or copy a verified retained input for a new Job attempt."""
    from scripts.lib.portable_inputs import _contained
    names = {field['name'] for field in parameter_contract(mode)}
    science = {key: value for key, value in params.items() if key in names}
    retained = params.get('ppiflow_generation_request')
    destination = Path(directory)
    roots = [Path(root).resolve() for root in allowed_roots]
    if not retained:
        return materialize_ppiflow_generation_request(mode, science, destination,
            source_identity=source_identity, requested_settings=requested_settings,
            authorize_source=lambda path: _contained(path, roots))
    read_prepared_ppiflow_generation_request(mode, science, retained, allowed_roots=roots)
    if retain_prepared:
        return {'ppiflow_generation_request': str(Path(retained).resolve())}
    if Path(retained).resolve() != destination.resolve():
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(retained, destination)
        # Check the copied bytes, never reopen the external original source.
        read_prepared_ppiflow_generation_request(mode, science, destination, allowed_roots=roots)
    return {'ppiflow_generation_request': str(destination.resolve())}


def read_prepared_ppiflow_generation_request(mode: str, params: dict, directory: str | Path,
                                              *, allowed_roots=None) -> dict:
    """Reopen retained model inputs without rereading their original sources."""
    root = Path(directory)
    if root.is_symlink() or any(parent.is_symlink() for parent in root.parents):
        raise ValueError("PPIFlow request must be a regular owned directory")
    root = root.resolve(strict=True)
    if allowed_roots is not None and not any(
            root.is_relative_to(Path(allowed).resolve()) for allowed in allowed_roots):
        raise ValueError("PPIFlow request is outside allowed input roots")
    request_path = root / 'request.json'
    if request_path.is_symlink():
        raise ValueError("PPIFlow request must be a regular file")
    payload = json.loads(request_path.read_text())
    names = {field['name'] for field in parameter_contract(mode)}
    effective = normalize_ppiflow_generation_params(mode, {key: value for key, value in params.items() if key in names})
    if (payload.get('schema_version') != SCHEMA_VERSION or payload.get('mode') != mode
            or payload.get('effective_settings') != effective
            or payload.get('runtime') != selected_assets(mode)):
        raise ValueError("PPIFlow prepared request differs from the selected settings")
    transport = payload.get('transport_settings')
    if not isinstance(transport, dict) or set(transport) != set(effective):
        raise ValueError("PPIFlow prepared settings are incomplete")
    source_fields = {'target_pdb', 'framework_pdb', 'input_csv'}
    if any(transport[key] != value for key, value in effective.items() if key not in source_fields):
        raise ValueError("PPIFlow transport changed scientific settings")
    bound = {}
    for row in payload['source_bindings']:
        relative = Path(row['path'])
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError("PPIFlow source binding escapes its request")
        path = root / relative
        if path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(root):
            raise ValueError("PPIFlow retained source is unavailable")
        if path.stat().st_size != row['size'] or _digest(path) != row['sha256']:
            raise ValueError("PPIFlow retained source bytes changed")
        bound[row['role']] = row['path']
    for key in ('target_pdb', 'framework_pdb'):
        if effective.get(key) and transport.get(key) != bound.get(key):
            raise ValueError("PPIFlow retained source role changed")
    if effective.get('input_csv'):
        if transport.get('input_csv') != 'inputs/native_input.csv':
            raise ValueError("PPIFlow retained CSV path changed")
        original = root / bound['input_csv']
        generated = root / 'inputs/native_input.csv'
        if generated.is_symlink():
            raise ValueError("PPIFlow retained CSV must be a regular file")
        with original.open(newline='') as handle:
            source_rows = list(csv.DictReader(handle))
        with generated.open(newline='') as handle:
            staged_rows = list(csv.DictReader(handle))
        expected = [{**row, 'processed_path': bound[f'csv_row:{index}']}
                    for index, row in enumerate(source_rows)]
        if staged_rows != expected:
            raise ValueError("PPIFlow retained CSV dependencies changed")
    return payload


def generation_result_contract(mode: str) -> dict | None:
    """Keep initial-generation return semantics separate from partial flow."""
    if mode not in MODES:
        return None
    return {
        'native_contract_authority': 'platform/api/services/ppiflow_generation.py:read_ppiflow_generation_result',
        'output_subdirectory': 'ppiflow_generation',
        'receipt': 'generation_receipt.json',
        'producer_records': 'samples.jsonl',
    }


def _decode_generation_result(receipt: str, samples: str) -> dict:
    """One model-native decoder for direct readback and owned publication bytes."""
    return {"receipt": json.loads(receipt),
            "records": [json.loads(line) for line in samples.splitlines() if line]}


def read_ppiflow_generation_result(directory: str | Path) -> dict:
    """Model-owned readback; numerical values remain producer records, not ranks."""
    root = Path(directory)
    manifest = root / "samples.jsonl"
    return _decode_generation_result((root / "generation_receipt.json").read_text(),
                                     manifest.read_text() if manifest.is_file() else "")


PUBLICATION_KEY = "ppiflow_generation_publication"


def _publication_input(job, output):
    """Only explicit producer sample snapshots enter the Design projection."""
    from services.bindcraft2_publication import _regular
    if job.model_id != "ppiflow" or job.mode not in MODES:
        raise ValueError("Not an initial PPIFlow generation Job")
    output = Path(output).absolute()
    if not job.output_dir or Path(job.output_dir).absolute() != output:
        raise ValueError("PPIFlow publication root differs from Job")
    root = output / "ppiflow_generation"
    files = {}

    def owned_bytes(name):
        path, data = _regular(root, name)
        files[name] = {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
        return path, data

    _, raw = owned_bytes("generation_receipt.json")
    samples = owned_bytes("samples.jsonl")[1] if (root / "samples.jsonl").exists() else b""
    result = _decode_generation_result(raw.decode(), samples.decode())
    receipt = result["receipt"]
    if (receipt.get("schema_version") != SCHEMA_VERSION or receipt.get("model") != "ppiflow"
            or receipt.get("mode") != job.mode or receipt.get("operation") != "initial_generation"):
        raise ValueError("PPIFlow producer receipt identity differs")
    records = result["records"]
    if type(receipt.get("emitted_samples")) is not int or receipt["emitted_samples"] != len(records):
        raise ValueError("PPIFlow producer sample accounting differs")
    names = ["generation_receipt.json"]
    if (root / "samples.jsonl").exists():
        names.append("samples.jsonl")
    keys = set()
    for record in records:
        key = record.get("candidate_key")
        if not isinstance(key, str) or not key or key in keys:
            raise ValueError("PPIFlow producer sample identity missing or repeated")
        keys.add(key)
        if (record.get("schema_version") != SCHEMA_VERSION or record.get("producer") != "ppiflow"
                or record.get("operation") != "initial_generation" or record.get("mode") != job.mode):
            raise ValueError("PPIFlow sample producer identity differs")
        path, data = owned_bytes(record["path"])
        if files[record["path"]]["sha256"] != record["sha256"]:
            raise ValueError("PPIFlow producer snapshot changed")
        from services.core_protein_result_contract import _structure_confidence
        _structure_confidence(data, str(path))  # syntax only; never a scientific score
        names.append(record["path"])
        # The sidecar is emitted at the same event, not reconstructed from native filenames.
        sidecar = record["path"] + ".sample.json"
        _, data = owned_bytes(sidecar)
        if json.loads(data) != record:
            raise ValueError("PPIFlow sample sidecar differs")
        names.append(sidecar)
    if len(set(names)) != len(names):
        raise ValueError("PPIFlow samples share a snapshot path")
    publication = {"schema": "ppiflow.generation-publication.v1", "job_id": job.id,
                   "root": str(output), "campaign_root": "ppiflow_generation",
                   "attempt": job.retry_count or 0, "remote_attempt_id": job.remote_attempt_id,
                   "files": files}
    return root, result, publication


def _generation_design_fields(job, record, artifact):
    import uuid
    # Initial generation is a new observation, not validation inherited from an input.
    identity = (job.id, job.retry_count or 0, record["candidate_key"])
    return {"id": str(uuid.uuid5(uuid.NAMESPACE_URL, "ppiflow:" + repr(identity))),
            "job_id": job.id, "name": record["candidate_key"], "producer_model_id": "ppiflow",
            "pdb_path": artifact.storage_path, "json_path": artifact.storage_path + ".sample.json",
            "lineage_root_job_id": job.lineage_root_job_id or job.id, "origin_job_id": job.id,
            "stage_family": "ppiflow", "stage_mode": job.mode,
            "artifact_class": "binder_backbone", "artifact_schema_version": 1,
            "provenance": {"schema": "ppiflow.candidate-lineage.v1",
                           "candidate_key": record["candidate_key"],
                           "source": record.get("source"), "source_identity": record.get("source_identity"),
                           **{key: record[key] for key in ("target_residue_mapping", "independent_target", "binder_chains", "target_chains")
                              if key in record},
                           "primary_artifact_id": artifact.id, "validation_state": "unvalidated"}}


async def _generation_custody(job, session, *, output=None, publish=False):
    from sqlalchemy import select
    from database import Design, JobArtifact
    import asyncio
    import uuid
    root, result, publication = await asyncio.to_thread(_publication_input, job, output or job.output_dir)
    previous = (job.provenance or {}).get(PUBLICATION_KEY)
    if not publish and previous is None:
        raise ValueError("PPIFlow publication missing")
    if previous is not None and {k: v for k, v in previous.items() if k != "candidates"} != publication:
        raise ValueError("PPIFlow publication replay changed")
    with session.no_autoflush:
        artifacts = list((await session.scalars(select(JobArtifact).where(JobArtifact.owner_job_id == job.id))).all())
        designs = list((await session.scalars(select(Design).where(Design.job_id == job.id))).all())
    owned = {a.logical_path.removeprefix("ppiflow_generation/"): a for a in artifacts
             if a.logical_path.startswith("ppiflow_generation/")}
    if len(owned) != sum(a.logical_path.startswith("ppiflow_generation/") for a in artifacts):
        raise ValueError("PPIFlow registered artifacts span multiple attempts")
    if previous is None and (owned or designs):
        raise ValueError("PPIFlow artifacts or Designs exist without publication")
    if previous is not None and set(owned) != set(publication["files"]):
        raise ValueError("PPIFlow registered artifact inventory changed")
    for name, info in publication["files"].items():
        expected = (str(root / name), info["sha256"], info["bytes"], publication["attempt"])
        row = owned.get(name)
        if row is None:
            row = JobArtifact(id=str(uuid.uuid4()), owner_job_id=job.id, attempt=publication["attempt"],
                              logical_path="ppiflow_generation/" + name, storage_path=str(root / name),
                              sha256=info["sha256"], bytes=info["bytes"],
                              media_type="chemical/x-pdb" if name.endswith(".pdb") else "application/json",
                              provenance={"model_id": "ppiflow"})
            session.add(row)
            owned[name] = row
        elif (row.storage_path, row.sha256, row.bytes, row.attempt) != expected:
            raise ValueError("PPIFlow registered artifact changed")
    expected_designs = [_generation_design_fields(job, record, owned[record["path"]]) for record in result["records"]]
    publication["candidates"] = [{"design_id": fields["id"], "candidate_key": record["candidate_key"],
        "structures": [{"artifact_id": owned[record["path"]].id,
                        "logical_path": owned[record["path"]].logical_path, "path": record["path"],
                        "sha256": record["sha256"], "primary": True, "target_state": None}]}
        for record, fields in zip(result["records"], expected_designs)]
    if previous is not None:
        if publication != previous or {d.id for d in designs} != {d["id"] for d in expected_designs}:
            raise ValueError("PPIFlow persisted candidate identity changed")
        by_id = {d.id: d for d in designs}
        for fields in expected_designs:
            if any(getattr(by_id[fields["id"]], key) != value for key, value in fields.items()):
                raise ValueError("PPIFlow persisted candidate lineage changed")
    else:
        for fields in expected_designs:
            session.add(Design(**fields))
        job.provenance = {**(job.provenance or {}), PUBLICATION_KEY: publication}
        await session.flush()
    return result, publication


async def publish_generation_results(job, output, session, *, commit=False):
    """Register producer snapshots atomically; valid zero yield publishes no Designs."""
    _, publication = await _generation_custody(job, session, output=output, publish=True)
    if commit:
        await session.commit()
    return len(publication["candidates"])


async def read_published_generation_results(job, session, *, offset=0, limit=100):
    """Verified native receipt/metrics plus exact existing Design/document handles."""
    result, publication = await _generation_custody(job, session)
    from services.boltzgen_candidate_publication import generation_workbench
    return generation_workbench(result, publication, offset=offset, limit=limit)
