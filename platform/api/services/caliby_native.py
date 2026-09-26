"""Standalone Caliby contracts; historical ``design`` requests stay retired."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class Closed(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Structure(Closed):
    state_id: str = Field(min_length=1)
    path: str = Field(min_length=1)


class Conformer(Structure):
    fixed_pos_seq: str = ""
    fixed_pos_scn: str = ""
    fixed_pos_override_seq: str = ""
    pos_restrict_aatype: str = ""
    symmetry_pos: str = ""


class Ensemble(Closed):
    ensemble_id: str = Field(min_length=1)
    # Order is scientific: state zero is the native primary conformer.
    states: list[Conformer] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_states(self):
        if len({s.state_id for s in self.states}) != len(self.states):
            raise ValueError("state_id must be unique within an ensemble")
        return self


class Common(Closed):
    schema_version: Literal[1] = 1
    batch_size: int = Field(default=4, ge=1)
    num_workers: int = Field(default=8, ge=0)
    scn_num_steps: int = Field(default=50, ge=1)
    scn_step_scale: float = Field(default=1.5, ge=0)


class EnsembleDesign(Common):
    task: Literal["ensemble_design"] = "ensemble_design"
    ensembles: list[Ensemble] = Field(min_length=1)
    model_name: Literal["caliby", "soluble_caliby", "soluble_caliby_v1"] = "soluble_caliby_v1"
    num_seqs_per_pdb: int = Field(default=4, ge=1)
    temperature: float = Field(default=0.1, gt=0)
    omit_aas: list[Literal["A", "C", "D", "E", "F", "G", "H", "I", "K", "L", "M", "N", "P", "Q", "R", "S", "T", "V", "W", "Y"]] = Field(default_factory=lambda: ["C"])
    verbose: bool = True
    use_primary_res_type: bool = True
    ensemble_ignore_res_idx_mismatch: bool = False
    gaussian_n_conformers: int = Field(default=0, ge=0)
    gaussian_noise_std: float = Field(default=0.0, ge=0)
    potts_regularization: Literal["LCP", "none"] = "LCP"
    potts_sweeps: int = Field(default=500, ge=1)
    potts_proposal: Literal["dlmc", "chromatic"] = "dlmc"
    potts_rejection_step: bool = False
    potts_only_cond: bool = False

    @model_validator(mode="after")
    def unique_ensembles(self):
        if len({e.ensemble_id for e in self.ensembles}) != len(self.ensembles):
            raise ValueError("ensemble_id must be unique")
        return self


class SidechainPack(Common):
    task: Literal["sidechain_pack"] = "sidechain_pack"
    structures: list[Structure] = Field(min_length=1)
    packer_model_name: Literal["caliby_packer_000", "caliby_packer_010", "caliby_packer_030"] = "caliby_packer_010"

    @model_validator(mode="after")
    def unique_structures(self):
        if len({s.state_id for s in self.structures}) != len(self.structures):
            raise ValueError("state_id must be unique")
        return self


REQUEST = TypeAdapter(Annotated[EnsembleDesign | SidechainPack, Field(discriminator="task")])
SUPPORTED_MODES = frozenset({"ensemble_design", "sidechain_pack"})


def normalize_request(mode: str, params: dict) -> dict:
    if mode not in SUPPORTED_MODES:
        raise ValueError("Historical Caliby design mode is not reinterpreted")
    if params.get("task", mode) != mode:
        raise ValueError("Caliby task must match mode")
    return REQUEST.validate_python({"task": mode, **params}).model_dump(mode="json")


def parameter_schema(mode: str) -> dict:
    """Mode-specific discovery of the same closed native request contract."""
    if mode not in SUPPORTED_MODES:
        raise ValueError("Historical Caliby design mode is not reinterpreted")
    schema = REQUEST.json_schema()
    branch = schema["discriminator"]["mapping"][mode]
    schema["oneOf"] = [{"$ref": branch}]
    schema["discriminator"]["mapping"] = {mode: branch}
    return schema


def science_params(mode: str, params: dict) -> dict:
    """Project model-owned fields; the caller owns framework-key admission."""
    if mode not in SUPPORTED_MODES:
        raise ValueError("Historical Caliby design mode is not reinterpreted")
    fields = (EnsembleDesign if mode == "ensemble_design" else SidechainPack).model_fields
    return normalize_request(mode, {key: value for key, value in params.items() if key in fields})


def read_prepared_request(mode: str, params: dict, directory: str | Path, *, allowed_roots=None) -> dict:
    """Validate retained custody against the selected request, without source I/O."""
    import copy
    import hashlib
    from scripts.lib.portable_inputs import _contained

    root = Path(directory)
    roots = [Path(p).resolve() for p in allowed_roots] if allowed_roots is not None else [root.resolve()]
    root = _contained(root, roots)
    payload = json.loads(_contained(root / "request.json", [root]).read_bytes())
    requested = science_params(mode, params)
    if payload.get("requested") != requested:
        raise ValueError("Caliby prepared request differs from selected settings")
    effective = copy.deepcopy(requested)
    states = ([s for e in effective["ensembles"] for s in e["states"]]
              if mode == "ensemble_design" else effective["structures"])
    sources = payload.get("sources")
    if not isinstance(sources, list) or len(sources) != len(states):
        raise ValueError("Caliby prepared source ledger is incomplete")
    for index, (state, source) in enumerate(zip(states, sources)):
        suffix = Path(state["path"]).suffix.lower()
        relative = Path("structures") / f"state_{index:06d}{suffix}"
        if (suffix not in {".pdb", ".cif", ".mmcif"}
                or source.get("state_id") != state["state_id"]
                or source.get("source_path") != state["path"]
                or source.get("path") != relative.as_posix()
                or source.get("native_example_id") != relative.stem):
            raise ValueError("Caliby prepared source binding changed")
        path = _contained(root / relative, [root])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source.get("sha256"):
            raise ValueError("Caliby retained source bytes changed")
        state["path"] = relative.as_posix()
    if payload.get("effective") != effective:
        raise ValueError("Caliby prepared effective settings changed")
    return payload


def prepare_for_job(mode: str, params: dict, directory: str | Path, *, allowed_roots,
                    retain_prepared: bool = False) -> dict:
    """Snapshot once, or reuse/copy retained bytes through existing input custody."""
    import shutil
    from scripts.lib.portable_inputs import _contained

    science = science_params(mode, params)
    roots = [Path(p).resolve() for p in allowed_roots]
    retained = params.get("caliby_request_dir")
    destination = Path(directory)
    if retained:
        read_prepared_request(mode, science, retained, allowed_roots=roots)
        if retain_prepared or Path(retained).resolve() == destination.resolve():
            return {"caliby_request_dir": str(Path(retained).resolve())}
    # Check the destination's existing ancestor before any write, including links.
    ancestor = destination
    while not ancestor.exists() and not ancestor.is_symlink():
        ancestor = ancestor.parent
    _contained(ancestor, roots)
    if retained:
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(retained, destination, symlinks=True)
    else:
        materialize_request(mode, science, destination,
                            authorize_source=lambda path: _contained(path, roots))
    read_prepared_request(mode, science, destination, allowed_roots=roots)
    return {"caliby_request_dir": str(destination.resolve())}


def selected_assets(mode: str, params: dict) -> dict:
    # Asset discovery needs the checkpoint choice, never a dummy scientific input.
    if mode not in SUPPORTED_MODES:
        raise ValueError("Historical Caliby design mode is not reinterpreted")
    key, model = (("packer_model_name", SidechainPack) if mode == "sidechain_pack"
                  else ("model_name", EnsembleDesign))
    field = model.model_fields[key]
    name = TypeAdapter(field.annotation).validate_python(params.get(key, field.default))
    return {"container": "caliby.sif", "checkpoint": f"caliby/{name}.ckpt", "model_params_subdir": "caliby/model_params"}


def materialize_request(mode: str, params: dict, output_dir: str | Path, *, authorize_source=None) -> dict:
    """Use the same snapshot owner as the preparation CLI before remote review."""
    import runpy

    script = Path(__file__).resolve().parents[3] / "scripts/prep_caliby_request.py"
    return runpy.run_path(str(script))["prepare_request"](
        normalize_request(mode, params), Path(output_dir), authorize_source=authorize_source)


def result_contract(mode: str) -> dict:
    if mode not in SUPPORTED_MODES:
        raise ValueError("Historical Caliby design mode is not reinterpreted")
    return {"native_contract_authority": "platform/api/services/caliby_native.py:read_native_results",
            "schema": "bms.caliby-native-results.v1", "mode": mode,
            "relative_output_dir": "caliby_native", "primary_document": "caliby_results.json"}


def read_native_results(output_root: str | Path) -> dict:
    """Read the portable native document, retaining absent metrics as absent."""
    root = Path(output_root).resolve()
    document = json.loads((root / "caliby_results.json").read_text())
    for row in document["records"]:
        path = (root / row["structure_path"]).resolve()
        if not path.is_relative_to(root) or not path.is_file():
            raise ValueError("Missing or unowned Caliby structure")
    return document
