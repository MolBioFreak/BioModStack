"""Complexa discovery/binding leaf for ordinary Job and portable input owners."""
from __future__ import annotations
import json
from pathlib import Path
from scripts.lib.complexa_contract import MODEL_ID, PROFILES, Request, normalize_request, parameter_schema

REQUEST_FIELD = 'complexa_request'
SOURCE_SLOT_FIELDS = {'document_id': ('structure', None), 'evaluation_slots': ('structure', 'items')}

SUPPORTED_MODES = frozenset({"generate", "evaluate"})


def normalize_job_request(mode: str, params: dict) -> dict:
    request = Request.model_validate(params)
    expected = "evaluate" if request.operation in ("evaluate", "analysis") else "generate"
    if mode != expected:
        raise ValueError(f"Complexa operation {request.operation} belongs to mode {expected}")
    return normalize_request(params)


def bind_for_job(mode: str, params: dict, directory):
    """Compile native request bytes without allocating the output directory."""
    from component_runtime import GeneratedInput
    request = normalize_job_request(mode, params['complexa_request'])
    result = {**params, 'complexa_request': request}
    if params.get('native_sources') and params.get('complexa_request_dir'):
        return result, ()
    relative = Path('complexa') / 'request.json'
    result.setdefault('complexa_request_dir', str(Path(directory) / relative.parent))
    return result, (GeneratedInput(relative.as_posix(), (json.dumps(request, indent=2) + '\n').encode()),)


def source_slots_for_params(mode, params):
    request = normalize_job_request(mode, params[REQUEST_FIELD])
    slots = [request['source']['document_id']] if request.get('source') else []
    if request.get('operation') in ('evaluate', 'analysis'):
        slots.extend(request.get('evaluation_slots', []))
    return list(dict.fromkeys(slots))


def prepare_for_job(mode, params, directory, *, resolved_sources, allowed_roots, source_identities):
    import shutil
    from scripts.lib.portable_inputs import _contained
    destination = Path(directory) / 'complexa'
    request = normalize_job_request(mode, params[REQUEST_FIELD])
    write_request(mode, request, destination)
    # This is a generated staging directory. Re-preparation must not execute
    # unselected documents left by a previous basket/source selection.
    previous_source = destination / 'source-path.txt'
    if previous_source.exists():
        # Relative filename written exclusively by this preparation owner.
        (destination / Path(previous_source.read_text().strip()).name).unlink(missing_ok=True)
        previous_source.unlink()
    (destination / 'source.pdb').unlink(missing_ok=True)
    if (destination / 'evaluation').exists():
        shutil.rmtree(destination / 'evaluation')
    retained = {}
    evaluation_slots = request.get('evaluation_slots', []) if mode == 'evaluate' else []
    for slot in source_slots_for_params(mode, params):
        source = _contained(Path(resolved_sources[slot]), [Path(p).resolve() for p in allowed_roots])
        paths = []
        if (request.get('source') or {}).get('document_id') == slot:
            relative_source = 'source' + ''.join(source.suffixes)
            shutil.copyfile(source, destination / relative_source)
            (destination / 'source-path.txt').write_text(relative_source + '\n')
            paths.append(relative_source)
        for index, selected in enumerate(evaluation_slots):
            if selected != slot:
                continue
            evaluation = destination / 'evaluation'
            evaluation.mkdir(exist_ok=True)
            # Keep input format honest: a CIF is not made into a PDB by renaming.
            relative = Path('evaluation') / f'{index:04d}{"".join(source.suffixes)}'
            shutil.copyfile(source, destination / relative)
            paths.append(relative.as_posix())
        retained[slot] = {'identity': source_identities.get(slot), 'paths': paths}
    (destination / 'sources.json').write_text(json.dumps(source_identities, indent=2) + '\n')
    (destination / 'source-bindings.json').write_text(json.dumps(retained, indent=2) + '\n')
    return {**params, REQUEST_FIELD: request, 'complexa_request_dir': str(destination)}


def discovery() -> dict:
    from services.native_generation_sources import native_sources_schema, source_slot_schema
    profiles = json.loads((Path(__file__).resolve().parents[3] / "scripts/bms_complexa/profiles.json").read_text())
    return {"model_id": MODEL_ID, "modes": sorted(SUPPORTED_MODES), "request_key": "complexa_request",
            "native_profile_defaults": profiles,
            "native_sources_schema": native_sources_schema(),
            "schema": source_slot_schema(parameter_schema(), MODEL_ID), "profiles": {
                task: {"native_config": profile, "checkpoint_pair": pair,
                       "native_generation": branch + "_generate", "native_evaluation": branch + "_evaluate"}
                for task, (profile, pair, branch) in PROFILES.items()},
            "defaults": {"protein_binder": {"search": "best-of-n", "reward": "af2folding"},
                         "ligand_binder": {"search": "best-of-n", "reward": "rf3folding"},
                         "AME": {"search": "single-pass", "reward": None},
                         "monomer_motif_indexed": {"search": "single-pass", "reward": None},
                         "monomer_motif_unindexed": {"search": "single-pass", "reward": None}},
            "limitations": ["No external structure remodeling/partial diffusion API in this released adapter.",
                            "gradient_ascent is commented but not implemented upstream.",
                            "TMOL does not support ligand/AME complexes upstream.",
                            "Binder evaluation implements colabdesign and RF3 only; docstring Boltz2/Protenix options are not implemented.",
                            "Motif AME-pair architecture modes require installed native qualification."]}


def write_request(mode: str, params: dict, destination: Path) -> Path:
    """Pure request serialization; caller binds document_id via existing source custody."""
    destination.mkdir(parents=True, exist_ok=True)
    path = destination / "request.json"
    path.write_text(json.dumps(normalize_job_request(mode, params), indent=2))
    return path
