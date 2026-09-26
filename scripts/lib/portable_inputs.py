"""Typed native input references and placement bindings, not a scientific compiler.

The core is stdlib-only. YAML callers supply their native safe loader/dumper;
JSON discovery never imports an API or a scientific runtime. Archived documents
are never modified. Bindings are supplied by the trusted placement owner.
"""
from __future__ import annotations

import copy
import hashlib
import json
import os
from pathlib import Path
import stat

ENV = "BMS_PORTABLE_INPUT_BINDINGS"
SCHEMA = "bms.portable-input-bindings.v1"
MAX_DOCUMENT_BYTES = 16 * 1024 * 1024


def _contained(path, roots):
    candidate = Path(os.path.abspath(path))
    if any(p.is_symlink() for p in (candidate, *candidate.parents)):
        raise ValueError(f"Portable input traverses a symlink: {candidate}")
    resolved = candidate.resolve(strict=True)
    if not any(resolved == root or root in resolved.parents for root in roots):
        raise ValueError(f"Portable input outside approved roots: {candidate}")
    return resolved


def _identity(path):
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode) or os.fstat(stream.fileno()).st_nlink != 1:
            raise ValueError("Portable input must be a regular file")
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(block)
            digest.update(block)
    return digest.hexdigest(), size


def _bindings():
    value = os.environ.get(ENV)
    if not value:
        return None
    path = Path(value)
    if path.stat().st_size > MAX_DOCUMENT_BYTES:
        raise ValueError("Portable bindings exceed document bound")
    payload = json.loads(path.read_bytes())
    if payload.get("schema") != SCHEMA or not payload.get("roots"):
        raise ValueError("Invalid portable input binding schema/roots")
    return payload


def resolve_input_path(value, *, owner=None):
    """Resolve an exact file binding and verify bytes; local use is unchanged.

    Relative references are interpreted against their native document owner.
    Unbound worker-created files must remain inside approved worker roots.
    Directory bindings are exact root identities, never prefix replacements.
    """
    raw = Path(value)
    if not raw.is_absolute() and owner:
        raw = Path(owner).parent / raw
    payload = _bindings()
    if payload is None:
        return raw
    key = os.path.abspath(raw)
    roots = [Path(root).resolve(strict=False) for root in payload["roots"]]
    matches = [item for item in payload.get("bindings", [])
               if key in (item["reference"]["source_path"], item["reference"].get("reference_path"), item["path"])]
    # Nextflow may stage an exact verified artifact through a task symlink.
    # Only its already-declared immutable target is accepted, never a new read.
    if not matches and raw.is_symlink():
        target = str(raw.resolve(strict=True))
        matches = [item for item in payload.get("bindings", []) if item["path"] == target]
    if matches:
        identities = {(item["path"], item["reference"]["sha256"], item["reference"]["size_bytes"])
                      for item in matches}
        if len(identities) != 1:
            raise ValueError("Ambiguous portable input binding")
        target, digest, size = identities.pop()
        path = _contained(target, roots)
        if _identity(path) != (digest, size):
            raise ValueError("Portable input binding digest/size mismatch")
        return path
    directories = [item for item in payload.get("directories", [])
                   if key in (item["source_path"], item["path"])]
    if directories:
        if len({item["path"] for item in directories}) != 1:
            raise ValueError("Ambiguous portable directory binding")
        path = _contained(directories[0]["path"], roots)
        if not path.is_dir():
            raise ValueError("Portable directory binding is not a directory")
        return path
    # A relative reference can be rooted at a relocated immutable document.
    # Translate its owner back to the source identity for exact lookup, not a
    # global pathname replacement and not an arbitrary directory prefix alias.
    if owner and not Path(value).is_absolute():
        source_owners = {item["reference"]["source_path"] for item in payload.get("bindings", [])
                         if os.path.abspath(owner) == item["path"]}
        if len(source_owners) == 1:
            return resolve_input_path(Path(source_owners.pop()).parent / value)
    return _contained(raw, roots)


def trusted_results_root(default):
    payload = _bindings()
    if payload is None:
        return Path(default).resolve()
    root = payload.get("results_root")
    if not root:
        raise ValueError("Portable canonical request requires worker results_root binding")
    return _contained(root, [Path(p).resolve(strict=False) for p in payload["roots"]])


def native_reference_fields(document, format):
    """Yield (selector tuple, native path, role); no arbitrary string walking."""
    def field(obj, key, prefix, role="input"):
        value = obj.get(key) if isinstance(obj, dict) else None
        if isinstance(value, str) and value and value != "empty":
            yield prefix + (key,), value, role
    if format == "md-job":
        inp = document.get("input", {})
        for key in ("structure", "coordinates", "topology"):
            yield from field(inp, key, ("input",), key)
        closure = inp.get("topology_closure", {})
        for i, item in enumerate(closure.get("files", [])):
            yield ("input", "topology_closure", "files", i, "path"), str(Path(closure["root"]) / item["path"]), "topology-include"
    elif format == 'bc2-compilation':
        # Only executable source reads, not requested_settings provenance or
        # project_folder (a native output destination), belong to input closure.
        request = document.get('native_request', {})
        for i, target in enumerate(request.get('targets', [])):
            yield from field(target, 'target_path', ('native_request', 'targets', i), 'target')
        yield from field(request, 'binder_scaffold', ('native_request',), 'scaffold')
    elif format == "boltz-authority":
        for source in document.get("input_files", {}):
            yield ("input_files", source), source, "input"
    elif format == "boltz-complex":
        for i, component in enumerate(document.get("components", [])):
            yield from field(component, "msa_path", ("components", i), "msa")
    elif format == "boltz-batch":
        for i, entry in enumerate(document):
            yield from field(entry, "complex_json", (i,), "boltz-complex")
    elif format == "boltz-yaml":
        for i, entry in enumerate(document.get("sequences", [])):
            yield from field(entry.get("protein", {}), "msa", ("sequences", i, "protein"), "msa")
        for i, entry in enumerate(document.get("templates", [])):
            for key in ("cif", "pdb"):
                yield from field(entry, key, ("templates", i), "template")
    elif format == "protein-cad":
        for section, fields in {"laproteina": ("motif_pdb", "checkpoint_dir", "data_path"),
                                "disco": ("input_json_path", "compiled_input_json", "ligand_sdf", "checkpoint_path", "cutlass_path")}.items():
            if document.get("backend") and section != document["backend"]:
                continue
            for key in fields:
                role = "runtime" if key in {"checkpoint_dir", "data_path", "checkpoint_path", "cutlass_path"} else "disco-json" if "json" in key else "input"
                yield from field(document.get(section, {}), key, (section,), role)
    elif format == "disco-json":
        for i, job in enumerate(document):
            for j, entry in enumerate(job.get("sequences", [])):
                ligand = entry.get("ligand", {}).get("ligand")
                if isinstance(ligand, str) and ligand.startswith("FILE_"):
                    yield (i, "sequences", j, "ligand", "ligand"), ligand[5:], "ligand"
    elif format == "cm-request":
        settings = document.get("confornets", {})
        for key in ("checkpoint", "config"):
            yield from field(settings.get(key), "path", ("confornets", key), "runtime-snapshot" if key == "checkpoint" else "input")
        for i, item in enumerate(settings.get("references", [])):
            yield from field(item, "staged_path", ("confornets", "references", i), "reference")
        yield from field(settings.get("transfer_source"), "staged_path", ("confornets", "transfer_source"), "transfer-source")


def bind_native_document(document, format, *, owner=None):
    """Make a derived execution object; identities in original stay untouched."""
    if format in {"boltz-authority", "cm-request"}:
        raise ValueError("Sealed native authority must use access bindings, not derived documents")
    result = copy.deepcopy(document)
    for selector, value, role in native_reference_fields(document, format):
        # MD closure members retain relative names; the native copy owner resolves
        # each member independently, preserving topology include semantics.
        if format == "md-job" and role == "topology-include":
            continue
        target = result
        for part in selector[:-1]:
            target = target[part]
        path = str(resolve_input_path(value, owner=owner))
        target[selector[-1]] = "FILE_" + path if format == "disco-json" else path
    return result


def _format(document, path):
    if isinstance(document, dict):
        if document.get('schema_version') == 1 and 'native_request' in document and 'upstream_commit' in document:
            return 'bc2-compilation'
        if str(document.get("schema", "")).startswith("bms.md.job."):
            return "md-job"
        if document.get("schema_name") == "cm_request" and "request_sha256" in document:
            return "cm-request"
        if document.get("backend") in {"disco", "laproteina"}:
            return "protein-cad"
        if document.get("schema_name") == "boltz_launch_authority":
            return "boltz-authority"
        if "components" in document:
            return "boltz-complex"
        if "sequences" in document:
            return "boltz-yaml"
    if isinstance(document, list) and document:
        if isinstance(document[0], dict) and "complex_json" in document[0]:
            return "boltz-batch"
        if isinstance(document[0], dict) and "sequences" in document[0]:
            return "disco-json"
    return "json"


def prepared_generation_source_fields(model_id, mode, params):
    """Original source settings are provenance once the native tree is prepared.

    Do not reacquire controller sources on replay; only these model-owned input
    slots are superseded. Other parameters retain existing discovery behavior.
    """
    if (model_id == 'ppiflow' and mode in {'protein_binder', 'antibody_binder', 'nanobody_binder'}
            and params.get('ppiflow_generation_request')):
        return {'target_pdb', 'framework_pdb', 'input_csv'}
    if (model_id == 'boltzgen' and mode in {'protein_binder', 'peptide_binder', 'nanobody_binder'}
            and params.get('boltzgen_yaml_config')):
        return {'target_pdb', 'input_pdb', 'ligand_pdb', 'dna_structure', 'scaffold_path',
                'boltzgen_target_pdb_path', 'boltzgen_input_pdb', 'boltzgen_ligand_pdb',
                'boltzgen_dna_structure', 'boltzgen_scaffold_path', 'boltzgen_nanobody_scaffold_specs'}
    return set()


def discover_native_input_references(model_id, mode, params, generated_inputs, *, output_dir, allowed_roots, yaml_loader=None, runtime_references=None, document_owners=None):
    """Discover declared native closure. YAML uses the caller's native safe loader.

    Generated inputs must already be materialized; their immutable payload is
    checked if supplied by the compiler. Runtime fields are labeled separately.
    """
    roots = [Path(p).resolve(strict=False) for p in allowed_roots]
    records = []
    visited = set()
    scanned_directories = set()
    identities = {}
    inventory_supplied = runtime_references is not None
    runtime_references = runtime_references or {}
    document_owners = document_owners or {}
    def visit(value, owner, selector, role="input", lineage=(), source_owner=None, snapshot_sha256=None):
        raw = Path(value)
        if not raw.is_absolute():
            raw = Path(source_owner or owner).parent / raw if owner else Path(output_dir) / raw
        if role == "runtime":
            # The dependency owner has already inventoried these bytes. Never
            # enumerate a model tree as biological input acquisition.
            key = os.path.abspath(raw)
            selected = runtime_references.get(key)
            if selected is None:
                raise ValueError(f"Native runtime reference is not a selected dependency: {key}")
            record = dict(selected, source_path=key, role=role, owner=str(owner or "params"),
                          selector=list(selector), lineage=list(lineage))
            record["logical_id"] = "native-runtime:" + hashlib.sha256(
                json.dumps([key, str(owner), selector], separators=(",", ":")).encode()).hexdigest()
            records.append(record)
            return
        path = _contained(raw, roots)
        if path.is_dir():
            if role == "runtime-snapshot":
                raise ValueError("Request-owned runtime snapshot must be a regular file")
            if path in scanned_directories:
                return
            scanned_directories.add(path)
            for child in sorted(path.rglob("*")):
                if child.is_symlink():
                    raise ValueError("Portable input directory contains a symlink")
                if child.is_file():
                    visit(child, owner, selector + (child.relative_to(path).as_posix(),), role, lineage)
            return
        if path not in identities:
            identities[path] = _identity(path)
        digest, size = identities[path]
        if role == "runtime-snapshot":
            if digest != snapshot_sha256:
                raise ValueError("Request-owned runtime snapshot differs from declared checkpoint digest")
            # Read-only approval binds the immutable request-owned snapshot, not
            # an installed model tree. Transport additionally proves membership
            # in the selected dependency owner's concrete runtime inventory.
            if inventory_supplied and not any(
                    item.get("sha256") == digest and item.get("size_bytes") == size
                    for item in runtime_references.values()):
                raise ValueError("Request-owned runtime snapshot is not a selected dependency")
        identity = json.dumps([model_id, mode, str(owner or "params"), selector, digest], separators=(",", ":"))
        logical_id = "native-input:" + hashlib.sha256(identity.encode()).hexdigest()
        record = dict(logical_id=logical_id, role=role, format=path.suffix.lstrip(".") or "binary", source_path=str(path), sha256=digest, size_bytes=size, owner=str(owner or "params"), lineage=list(lineage), selector=list(selector))
        if source_owner and not Path(value).is_absolute():
            record["reference_path"] = os.path.abspath(Path(owner).parent / value)
        records.append(record)
        if role == 'ppiflow-csv' and path not in visited:
            # Native CSV processed_path is a source read, unlike observational
            # original paths archived inside the prepared request JSON.
            import csv
            visited.add(path)
            with path.open(newline='') as handle:
                for index, row in enumerate(csv.DictReader(handle)):
                    if row.get('processed_path'):
                        visit(row['processed_path'], path, ('processed_path', index),
                              lineage=(*lineage, logical_id))
            return
        if path in visited or role == "runtime" or path.suffix.lower() not in {".json", ".yaml", ".yml"}:
            return
        visited.add(path)
        if size > MAX_DOCUMENT_BYTES:
            raise ValueError("Native reference document exceeds bound")
        data = path.read_bytes()
        if path.suffix.lower() in {".yaml", ".yml"}:
            if yaml_loader is None:
                raise ValueError("YAML native discovery requires caller's safe yaml_loader")
            document = yaml_loader(data)
        else:
            document = json.loads(data)
        fmt = _format(document, path)
        record["format"] = fmt
        for child_selector, child, child_role in native_reference_fields(document, fmt):
            visit(child, path, child_selector, child_role, (*lineage, logical_id),
                  source_owner=document_owners.get(str(path)) if child_role != "msa" else None,
                  snapshot_sha256=document.get("confornets", {}).get("checkpoint", {}).get("sha256")
                  if fmt == "cm-request" and child_role == "runtime-snapshot" else None)
        if fmt == "cm-request":
            visit(path.parent / "cm_runtime_registry_v1.json", path, ("runtime_registry",), "runtime-config", (*lineage, logical_id))
            visit(path.parent / "cm_coordinate_plan_v1.json", path, ("coordinate_plan",), "coordinate-plan", (*lineage, logical_id))
    keys = {"complex_json_path", "sequence_batch_json_path", "msa_path", "bcp_input_path", "input_path", "cm_request_path", "cm_coordinate_plan_path", "md_job_config", "laproteina_motif_pdb", "disco_input_json_path", "disco_ligand_sdf", "protein_cad_request", "boltz_launch_authority_path", "boltz_prepared_msa_dir"}
    # The CAD compiler consumes the public names and emits pcad_* slots.
    # Discover the same immutable documents after compilation so nested FILE_
    # references reach the existing portable binding owner, not a JSON rewrite.
    keys.update({'pcad_laproteina_motif_pdb', 'pcad_disco_input_json_path',
                 'pcad_disco_ligand_sdf'})
    if model_id in {'antibody_denovo', 'template_antibody_denovo'} and mode in {
            'antibody_denovo_pipeline', 'antibody_refinement_pipeline'}:
        # Full-root preview and transport must bind the actual native biological
        # inputs, not just the path strings in the selected compiler plan.
        keys.update({'target_pdb', 'selected_input_dir', 'selected_input_manifest',
                     'rfantibody_input_pdbs', 'fampnn_collected_pdbs',
                     'manual_mutation_fixed_positions_json'})
    if (model_id, mode) in {('template_antibody_denovo', 'maturation_child'),
                           ('binder_refinement', 'refine'), ('caliby_binder', 'design')}:
        keys.add('source_identity_json')
        for index, path in enumerate(str(params.get('pdb_paths') or '').split(',')):
            if path.strip():
                visit(path.strip(), None, ('pdb_paths', index))
    if model_id == 'bindcraft2':
        keys.add('bc2_compilation')
    if model_id == 'ppiflow' and mode in {'protein_binder', 'antibody_binder', 'nanobody_binder'}:
        keys.add('ppiflow_generation_request')
        if not params.get('ppiflow_generation_request'):
            keys.update({'target_pdb', 'framework_pdb'})
            if params.get('input_csv'):
                visit(params['input_csv'], None, ('input_csv',), 'ppiflow-csv')
    if model_id in {'boltzgen', 'boltzgen_child'}:
        keys.add('boltzgen_yaml_config')
        if not params.get('boltzgen_yaml_config'):
            keys.update({'boltzgen_target_pdb_path', 'boltzgen_scaffold_path',
                         'boltzgen_input_pdb', 'boltzgen_ligand_pdb', 'boltzgen_dna_structure'})
    keys.difference_update(prepared_generation_source_fields(model_id, mode, params))
    if model_id == "nanopore":
        keys.update({"fastq_path", "reference_fasta", "bam_path"})
    for key in sorted(keys & params.keys()):
        if params[key]:
            visit(params[key], None, (key,))
    custody = params.get('ont_input_provenance') or {}
    if model_id == 'nanopore' and custody.get('source') == 'managed_fastq_launch_snapshot':
        received = next((record for record in records if record['selector'] == ['fastq_path']), None)
        if (received is None or received['sha256'] != custody.get('sha256')
                or received['size_bytes'] != custody.get('size_bytes')):
            raise ValueError('Portable FASTQ differs from its immutable native launch snapshot')
    for item in generated_inputs:
        relative = item.get("relative_path") if isinstance(item, dict) else item.relative_path
        if not isinstance(relative, str) or Path(relative).is_absolute() or ".." in Path(relative).parts:
            raise ValueError("Generated input requires contained relative path")
        path = Path(output_dir) / relative
        payload = item.get("payload") if isinstance(item, dict) else getattr(item, "payload", None)
        contained = _contained(path, roots)
        if payload is not None:
            if contained not in identities:
                identities[contained] = _identity(contained)
            if identities[contained] != (hashlib.sha256(payload).hexdigest(), len(payload)):
                raise ValueError("Generated native input differs from compiler bytes")
        visit(path, None, ("generated_inputs", relative))
    return list({record["logical_id"]: record for record in records}.values())


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Resolve a typed native input binding")
    parser.add_argument("operation", choices=["resolve"])
    parser.add_argument("path")
    args = parser.parse_args()
    print(resolve_input_path(args.path))
