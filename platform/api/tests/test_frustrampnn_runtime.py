from __future__ import annotations

import hashlib
import importlib
import json
import os
from pathlib import Path
import stat
import subprocess
import textwrap
from types import MappingProxyType

import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
RUNTIME_PATH = REPO_ROOT / "platform/api/services/frustrampnn/runtime.py"
SIF_SHA256 = "c4bd2ad605d49eee37d836f718d3d826d52c8b237a37e6081be2952ac3be72da"
EXECUTABLE_SHA256 = "32089d959f619c08a550c0e7d0fc7b66b508d009ec3179d007f13773a170212f"
CHECKPOINT_SHA256 = "eaee71adb7eec366fc672d2aadef87f2c51243042a4518cd897634784dc2da3b"


def _runtime():
    assert RUNTIME_PATH.is_file(), "neutral FrustraMPNN runtime module is missing"
    return importlib.import_module("services.frustrampnn.runtime")


def _qualified_image(tmp_path: Path, payload: bytes = b"qualified-sif") -> tuple[Path, str]:
    image = tmp_path / "runtime" / "frustrampnn.sif"
    image.parent.mkdir()
    image.write_bytes(payload)
    return image, hashlib.sha256(payload).hexdigest()


def test_runtime_registry_is_canonical_immutable_and_projects_exact_cm_v1(tmp_path: Path) -> None:
    runtime = _runtime()
    identity = runtime.FRUSTRAMPNN_RUNTIME_IDENTITY
    registry = runtime.FRUSTRAMPNN_RUNTIME_REGISTRY

    assert identity.sif_name == "frustrampnn.sif"
    assert identity.configured_sif_path == "/mnt/BioModStack/apptainer/frustrampnn.sif"
    assert identity.sif_sha256 == SIF_SHA256
    assert identity.executable_path == "/opt/venv/bin/frustrampnn"
    assert identity.executable_sha256 == EXECUTABLE_SHA256
    assert identity.checkpoint_id == "megascale.ckpt"
    assert identity.checkpoint_path == "/opt/frustrampnn_weights/megascale.ckpt"
    assert identity.checkpoint_sha256 == CHECKPOINT_SHA256
    assert identity.package_version == "1.0.0"
    assert identity.source_commit == "bbae1d03edf33dbe6f645d45c5604eb4464962ca"
    assert identity.python_version == "3.10.12"
    assert identity.pytorch_version == "2.11.0.dev20260126+cu128"
    assert isinstance(registry, MappingProxyType)
    assert isinstance(registry["runtime_identity"], MappingProxyType)
    with pytest.raises(TypeError):
        registry["component_id"] = "hostile"
    with pytest.raises(TypeError):
        registry["runtime_identity"]["sif_sha256"] = "0" * 64
    with pytest.raises((AttributeError, TypeError)):
        identity.sif_sha256 = "0" * 64

    container_dir = tmp_path / "containers"
    container_dir.mkdir()
    (container_dir / identity.sif_name).write_bytes(b"installed")
    assert runtime.cm_analysis_runtime_registry_v1(container_dir) == {
        "container_name": "frustrampnn.sif",
        "container_sha256": SIF_SHA256,
    }


def test_apptainer_build_uses_one_verified_runtime_source_commit_for_install_and_weights() -> None:
    definition = (
        Path(__file__).resolve().parents[3] / "apptainer" / "frustrampnn.def"
    ).read_text(encoding="utf-8")
    commit = "bbae1d03edf33dbe6f645d45c5604eb4464962ca"
    assert f"FRUSTRAMPNN_SOURCE_COMMIT={commit}" in definition
    assert 'git checkout --detach "$FRUSTRAMPNN_SOURCE_COMMIT"' in definition
    assert 'test "$(git rev-parse HEAD)" = "$FRUSTRAMPNN_SOURCE_COMMIT"' in definition
    assert "uv pip install --compile /opt/frustrampnn" in definition
    assert "cp -r /opt/frustrampnn/weights/vanilla_model_weights" in definition
    assert "frustraMPNN/$FRUSTRAMPNN_SOURCE_COMMIT/weights/megascale_train_weights.ckpt" in definition
    assert (
        "-o /opt/frustrampnn_weights/megascale.ckpt\n"
        f"    echo \"{CHECKPOINT_SHA256}  /opt/frustrampnn_weights/megascale.ckpt\" | sha256sum --check -"
    ) in definition
    assert "git+https://github.com/schoederlab/frustraMPNN.git" not in definition
    assert "raw/main/weights" not in definition


@pytest.mark.parametrize(
    "path_builder",
    [
        lambda image: f"{image.parent}/./{image.name}",
        lambda image: f"{image.parent}/../{image.parent.name}/{image.name}",
        lambda image: f"{image.parent}//{image.name}",
        lambda image: f"{image.parent}\\{image.name}",
    ],
)
def test_verified_sif_open_rejects_lexically_ambiguous_paths(
    tmp_path: Path, path_builder,
) -> None:
    runtime = _runtime()
    image, digest = _qualified_image(tmp_path)
    with pytest.raises(runtime.RuntimeValidationError, match="lexical|component"):
        runtime.open_verified_container(path_builder(image), digest)


def test_verified_sif_open_rejects_symlinks_nonregular_files_and_bad_digests(tmp_path: Path) -> None:
    runtime = _runtime()
    image, digest = _qualified_image(tmp_path)
    linked = image.with_name("linked.sif")
    linked.symlink_to(image)
    parent_link = tmp_path / "linked-runtime"
    parent_link.symlink_to(image.parent, target_is_directory=True)
    fifo = image.with_name("fifo.sif")
    os.mkfifo(fifo)

    for path in (linked, parent_link / image.name):
        with pytest.raises(runtime.RuntimeValidationError, match="symlink|without following"):
            runtime.open_verified_container(path, digest)
    with pytest.raises(runtime.RuntimeValidationError, match="regular"):
        runtime.open_verified_container(fifo, digest)
    with pytest.raises(runtime.RuntimeValidationError, match="malformed"):
        runtime.open_verified_container(image, "bad")
    with pytest.raises(runtime.RuntimeValidationError, match="does not match"):
        runtime.open_verified_container(image, "0" * 64)


def test_verified_sif_descriptor_pins_one_generation_and_closes_explicitly(tmp_path: Path) -> None:
    runtime = _runtime()
    image, digest = _qualified_image(tmp_path)
    pinned = runtime.open_verified_container(image, digest)
    assert pinned.sha256 == digest
    assert pinned.proc_path == Path(f"/proc/self/fd/{pinned.fd}")
    image.unlink()
    image.write_bytes(b"replacement")
    assert runtime.sha256_fd(pinned.fd) == digest
    assert pinned.proc_path.read_bytes() == b"qualified-sif"
    pinned.close()
    assert pinned.closed is True
    with pytest.raises(OSError):
        os.fstat(pinned.fd)
    pinned.close()


def _fake_apptainer(tmp_path: Path, capture: Path) -> Path:
    executable = tmp_path / "fake-apptainer"
    executable.write_text(
        textwrap.dedent(
            f"""\
            #!/usr/bin/env python3
            import json, os, pathlib, sys
            args = sys.argv[1:]
            container = args[args.index('exec') + 1]
            internal = args[-1]
            record = {{'argv': args, 'container_exists': pathlib.Path(container).exists()}}
            pathlib.Path({str(capture)!r}).write_text(json.dumps(record), encoding='utf-8')
            values = {{
                '/opt/venv/bin/frustrampnn': {EXECUTABLE_SHA256!r},
                '/opt/frustrampnn_weights/megascale.ckpt': {CHECKPOINT_SHA256!r},
            }}
            print(values[internal], internal)
            """
        ),
        encoding="utf-8",
    )
    executable.chmod(executable.stat().st_mode | stat.S_IXUSR)
    return executable


def test_container_asset_hashes_use_pinned_sif_fd_and_match_registry(tmp_path: Path) -> None:
    runtime = _runtime()
    image, digest = _qualified_image(tmp_path)
    pinned = runtime.open_verified_container(image, digest)
    capture = tmp_path / "capture.json"
    apptainer = _fake_apptainer(tmp_path, capture)
    try:
        assets = runtime.verify_container_assets(apptainer, pinned)
    finally:
        pinned.close()

    assert assets == {
        "executable_sha256": EXECUTABLE_SHA256,
        "checkpoint_sha256": CHECKPOINT_SHA256,
    }
    observed = json.loads(capture.read_text(encoding="utf-8"))
    assert observed["container_exists"] is True
    assert observed["argv"][:2] == ["exec", str(pinned.proc_path)]


def test_container_asset_verification_fails_closed_on_hash_mismatch(tmp_path: Path) -> None:
    runtime = _runtime()
    image, digest = _qualified_image(tmp_path)
    pinned = runtime.open_verified_container(image, digest)
    capture = tmp_path / "capture.json"
    apptainer = _fake_apptainer(tmp_path, capture)
    hostile_identity = runtime.FrustraMPNNRuntimeIdentity(
        **{
            **runtime.runtime_identity_dict(),
            "executable_sha256": "0" * 64,
        }
    )
    try:
        with pytest.raises(runtime.RuntimeValidationError, match="executable.*does not match"):
            runtime.verify_container_assets(apptainer, pinned, identity=hostile_identity)
    finally:
        pinned.close()


def test_command_is_exact_gpu_safe_and_returns_receipt_metadata(tmp_path: Path) -> None:
    runtime = _runtime()
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    output_root.mkdir()
    normalized = input_root / "normalized.pdb"
    normalized.write_text("ATOM\n", encoding="utf-8")
    raw = output_root / "candidate" / "raw_frustrampnn.csv"
    pinned_container = Path("/proc/self/fd/41")

    invocation = runtime.build_frustrampnn_command(
        apptainer="/usr/bin/apptainer",
        container=pinned_container,
        normalized=normalized,
        raw=raw,
        output_root=output_root,
        physical_gpu_id=3,
    )

    assert list(invocation.argv) == [
        "/usr/bin/apptainer", "exec", "--containall", "--writable-tmpfs", "--nv",
        "--env", "CUDA_DEVICE_ORDER=PCI_BUS_ID",
        "--env", "CUDA_VISIBLE_DEVICES=3",
        "--bind", f"{normalized}:/bms/input/normalized.pdb:ro",
        "--bind", f"{output_root}:/bms/output:rw",
        str(pinned_container), "/opt/venv/bin/frustrampnn", "predict",
        "--pdb", "/bms/input/normalized.pdb",
        "--checkpoint", "/opt/frustrampnn_weights/megascale.ckpt",
        "--output", "/bms/output/candidate/raw_frustrampnn.csv",
        "--device", "cuda",
    ]
    assert "--gpu_id" not in invocation.argv
    assert "--gpu-id" not in invocation.argv
    assert invocation.receipt_metadata == {
        "physical_gpu_id": 3,
        "task_visible_gpu_id": 0,
    }


def test_predict_batch_command_is_exact_gpu_safe_and_product_owned(
    tmp_path: Path,
) -> None:
    runtime = _runtime()
    manifest = tmp_path / "batch.json"
    adapter = tmp_path / "run_frustrampnn_predict_batch.py"
    output = tmp_path / "output"
    pdb_paths = [tmp_path / "staged" / "alpha.pdb", tmp_path / "staged" / "beta.pdb"]
    pdb_paths[0].parent.mkdir()
    for path in pdb_paths:
        path.write_bytes(f"HEADER {path.stem}\nEND\n".encode("ascii"))
    manifest.write_text(
        json.dumps(
            {
                "schema_name": "frustrampnn_predict_batch_input",
                "schema_version": 1,
                "checkpoint_path": "/opt/frustrampnn_weights/megascale.ckpt",
                "device": "cuda:0",
                "records": [
                    {
                        "ordinal": ordinal,
                        "candidate_id": f"candidate-{path.stem}",
                        "invocation_id": f"invocation-{path.stem}",
                        "staged_pdb_path": str(path),
                        "source_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    }
                    for ordinal, path in enumerate(pdb_paths)
                ],
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n",
        encoding="utf-8",
    )
    adapter.write_text("print('adapter')\n", encoding="utf-8")
    output.mkdir()
    container = Path("/proc/self/fd/41")

    invocation = runtime.build_frustrampnn_predict_batch_command(
        apptainer="/usr/bin/apptainer",
        container=container,
        manifest=manifest,
        output_root=output,
        adapter=adapter,
        physical_gpu_id=5,
    )

    assert list(invocation.argv) == [
        "/usr/bin/apptainer",
        "exec",
        "--containall",
        "--writable-tmpfs",
        "--nv",
        "--env",
        "CUDA_DEVICE_ORDER=PCI_BUS_ID",
        "--env",
        "CUDA_VISIBLE_DEVICES=5",
        "--bind",
        f"{manifest}:/bms/batch/input.json:ro",
        "--bind",
        f"{adapter}:/bms/adapter/run_frustrampnn_predict_batch.py:ro",
        "--bind",
        f"{pdb_paths[0]}:{pdb_paths[0]}:ro",
        "--bind",
        f"{pdb_paths[1]}:{pdb_paths[1]}:ro",
        "--bind",
        f"{output}:/bms/output:rw",
        str(container),
        "/opt/venv/bin/python",
        "/bms/adapter/run_frustrampnn_predict_batch.py",
        "--manifest",
        "/bms/batch/input.json",
        "--output-dir",
        "/bms/output",
    ]
    assert invocation.receipt_metadata == {
        "physical_gpu_id": 5,
        "task_visible_gpu_id": 0,
    }
    assert invocation.argv.count("--bind") == 5


def _effective_settings(mode: str, chain_positions: dict[str, tuple[int, ...]]):
    settings = importlib.import_module("services.frustrampnn.settings")
    entities = []
    selectors = []
    resolved_chains = []
    for chain_id, positions in chain_positions.items():
        entity = {
            "entity_instance_id": f"entity-{chain_id}",
            "source_entity_id": chain_id,
            "label_asym_id": chain_id,
            "auth_asym_id": chain_id,
        }
        entities.append(entity)
        residues = []
        for position in positions:
            residue = {
                **entity,
                "auth_seq_id": position + 10,
                "insertion_code": "",
                "sequence_index": position + 1,
                "label_seq_id": position + 1,
                "wt": "G",
                "pdb_chain_id": chain_id,
                "pdb_residue_id": position + 10,
                "pdb_insertion_code": "",
                "model_position": position,
                "residue_name": "GLY",
            }
            residues.append(residue)
            selectors.append({
                key: residue[key]
                for key in (
                    "entity_instance_id", "source_entity_id", "label_asym_id",
                    "auth_asym_id", "auth_seq_id", "insertion_code", "sequence_index",
                )
            })
        resolved_chains.append(
            settings.FrustraMPNNResolvedChainSelection.model_validate({
                "entity": entity,
                "pdb_chain_id": chain_id,
                "residues": residues,
            })
        )
    selection = {"mode": mode, "entities": [], "residues": [], "regions": []}
    if mode == "selected_entities":
        selection["entities"] = entities
    elif mode == "selected_residues":
        selection["residues"] = selectors
    elif mode == "selected_regions":
        selection["regions"] = [
            {
                **entity,
                "sequence_start": min(positions) + 1,
                "sequence_end": max(positions) + 1,
            }
            for entity, positions in zip(
                entities, chain_positions.values(), strict=True
            )
        ]
    requested = settings.FrustraMPNNRequestedSettings.model_validate({
        "protein_selection": selection,
    })
    return settings._build_effective_settings(
        requested,
        resolved_chains=tuple(resolved_chains),
        resolution_identity=settings.FrustraMPNNResolutionIdentity(
            source_artifact_sha256="a" * 64,
            structure_map_sha256="b" * 64,
            normalized_pdb_sha256="c" * 64,
        ),
    )


def test_command_plan_compiles_all_and_selected_entities_without_positions() -> None:
    runtime = _runtime()
    canonical_sha256 = importlib.import_module(
        "services.frustrampnn.contracts"
    ).canonical_sha256

    all_plan = runtime.compile_frustrampnn_command_plan(
        _effective_settings("all_protein_entities", {"B": (0,), "A": (0,)})
    )
    assert all_plan.entries == (
        runtime.FrustraMPNNCommandPlanEntry(
            ordinal=0,
            chains=None,
            positions=None,
            shard_relative_path="raw_frustrampnn_shard_0000.csv",
        ),
    )
    assert all_plan.plan_sha256 == canonical_sha256({
        "entries": [{
            "ordinal": 0,
            "chains": None,
            "positions": None,
            "shard_relative_path": "raw_frustrampnn_shard_0000.csv",
        }]
    })

    entity_plan = runtime.compile_frustrampnn_command_plan(
        _effective_settings("selected_entities", {"B": (0,), "A": (0,)})
    )
    assert [(entry.ordinal, entry.chains, entry.positions) for entry in entity_plan.entries] == [
        (0, ("A", "B"), None),
    ]


def test_selected_entity_plan_matches_source_only_intent_to_generated_chains() -> None:
    runtime = _runtime()
    settings = importlib.import_module("services.frustrampnn.settings")
    exact_effective = _effective_settings("selected_entities", {"X": (0,)})
    source_only_entities = tuple(
        entity.model_copy(update={"label_asym_id": None, "auth_asym_id": None})
        for entity in exact_effective.requested_settings.protein_selection.entities
    )
    requested = exact_effective.requested_settings.model_copy(
        update={
            "protein_selection": exact_effective.requested_settings.protein_selection.model_copy(
                update={"entities": source_only_entities}
            )
        }
    )
    effective = settings._build_effective_settings(
        requested,
        resolved_chains=exact_effective.resolved_chains,
        resolution_identity=exact_effective.resolution_identity,
    )

    plan = runtime.compile_frustrampnn_command_plan(effective)

    assert [
        (entry.ordinal, entry.chains, entry.positions)
        for entry in plan.entries
    ] == [(0, ("X",), None)]


def test_selected_residue_plan_groups_only_identical_position_tuples_stably() -> None:
    runtime = _runtime()
    effective = _effective_settings(
        "selected_residues",
        {"C": (2, 0), "B": (1,), "A": (0, 2)},
    )

    plan = runtime.compile_frustrampnn_command_plan(effective)

    assert [
        (entry.ordinal, entry.chains, entry.positions, entry.shard_relative_path)
        for entry in plan.entries
    ] == [
        (0, ("A", "C"), (0, 2), "raw_frustrampnn_shard_0000.csv"),
        (1, ("B",), (1,), "raw_frustrampnn_shard_0001.csv"),
    ]
    assert plan == runtime.compile_frustrampnn_command_plan(effective)


def test_selected_region_plan_compiles_contiguous_sequence_ranges_to_model_positions() -> None:
    runtime = _runtime()
    effective = _effective_settings(
        "selected_regions",
        {"B": (4, 5, 6), "A": (4, 5, 6)},
    )

    plan = runtime.compile_frustrampnn_command_plan(effective)

    assert [
        (entry.ordinal, entry.chains, entry.positions)
        for entry in plan.entries
    ] == [(0, ("A", "B"), (4, 5, 6))]


def test_typed_selection_argv_preserves_base_and_binds_canonical_hash(tmp_path: Path) -> None:
    runtime = _runtime()
    canonical_sha256 = importlib.import_module(
        "services.frustrampnn.contracts"
    ).canonical_sha256
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    input_root.mkdir()
    output_root.mkdir()
    normalized = input_root / "normalized.pdb"
    normalized.write_text("ATOM\n", encoding="utf-8")

    base = runtime.build_frustrampnn_command(
        apptainer="apptainer", container=Path("/proc/self/fd/4"),
        normalized=normalized, raw=output_root / "base.csv", output_root=output_root,
        physical_gpu_id=0,
    )
    selected = runtime.build_frustrampnn_command(
        apptainer="apptainer", container=Path("/proc/self/fd/4"),
        normalized=normalized, raw=output_root / "base.csv", output_root=output_root,
        physical_gpu_id=0, chains=("B", "A"), positions=(2, 0),
    )

    assert selected.argv[:-4] == base.argv
    assert selected.argv[-4:] == ("--chains", "A,B", "--positions", "0,2")
    assert selected.argv_sha256 == canonical_sha256(list(selected.argv))


@pytest.mark.parametrize(
    ("chains", "positions"),
    [
        ((), None),
        (("A", "A"), None),
        (("A,B",), None),
        (None, (0,)),
        (("A",), ()),
        (("A",), (1, 1)),
        (("A",), (-1,)),
        (("A",), (True,)),
    ],
)
def test_command_rejects_invalid_typed_plan_grammar(
    tmp_path: Path,
    chains: tuple[str, ...] | None,
    positions: tuple[int, ...] | None,
) -> None:
    runtime = _runtime()
    normalized = tmp_path / "normalized.pdb"
    normalized.write_text("ATOM\n", encoding="utf-8")
    with pytest.raises(runtime.RuntimeValidationError, match="chain|position|selection"):
        runtime.build_frustrampnn_command(
            apptainer="apptainer", container=Path("/proc/self/fd/4"),
            normalized=normalized, raw=tmp_path / "raw.csv", output_root=tmp_path,
            physical_gpu_id=0, chains=chains, positions=positions,
        )


@pytest.mark.parametrize("gpu_id", [True, -1, 1.0, "1", None])
def test_command_rejects_invalid_physical_gpu_ids(tmp_path: Path, gpu_id: object) -> None:
    runtime = _runtime()
    normalized = tmp_path / "normalized.pdb"
    normalized.write_text("ATOM\n", encoding="utf-8")
    with pytest.raises(runtime.RuntimeValidationError, match="GPU"):
        runtime.build_frustrampnn_command(
            apptainer="apptainer", container=Path("/proc/self/fd/4"),
            normalized=normalized, raw=tmp_path / "raw.csv", output_root=tmp_path,
            physical_gpu_id=gpu_id,
        )


@pytest.mark.parametrize("case", ["input_is_output", "raw_escapes", "unsafe_bind"])
def test_command_rejects_path_collisions_and_unsafe_bind_paths(tmp_path: Path, case: str) -> None:
    runtime = _runtime()
    output_root = tmp_path / "output"
    output_root.mkdir()
    normalized = tmp_path / "normalized.pdb"
    normalized.write_text("ATOM\n", encoding="utf-8")
    raw = output_root / "raw.csv"
    if case == "input_is_output":
        normalized = raw
        normalized.write_text("ATOM\n", encoding="utf-8")
    elif case == "raw_escapes":
        raw = tmp_path / "elsewhere.csv"
    else:
        unsafe = tmp_path / "unsafe:input.pdb"
        unsafe.write_text("ATOM\n", encoding="utf-8")
        normalized = unsafe
    with pytest.raises(runtime.RuntimeValidationError, match="path|bind|output|collision"):
        runtime.build_frustrampnn_command(
            apptainer="apptainer", container=Path("/proc/self/fd/4"),
            normalized=normalized, raw=raw, output_root=output_root,
            physical_gpu_id=0,
        )


def test_cm_compatibility_wrappers_delegate_to_neutral_runtime() -> None:
    runtime = _runtime()
    cm = importlib.import_module("scripts.run_conformational_mapping_analysis_plane")
    assert cm._open_verified_container.__module__ == cm.__name__
    assert cm._sha256_fd.__module__ == cm.__name__
    assert cm._container_sha256.__module__ == cm.__name__
    assert cm._frustrampnn_command.__module__ == cm.__name__

    source = Path(cm.__file__).read_text(encoding="utf-8")
    wrapper_region = source[source.index("def _container_sha256"):source.index("def main")]
    assert "_frustrampnn_runtime.open_verified_container" in wrapper_region
    assert "_frustrampnn_runtime.sha256_fd" in wrapper_region
    assert "_frustrampnn_runtime.container_sha256" in wrapper_region
    assert "_frustrampnn_runtime.build_frustrampnn_command" in wrapper_region
    assert "O_NOFOLLOW" not in wrapper_region
    assert "hashlib.sha256" not in wrapper_region


def test_cm_router_uses_neutral_registry_projection() -> None:
    _runtime()
    router_source = (REPO_ROOT / "platform/api/routers/conformational_mapping.py").read_text(
        encoding="utf-8"
    )
    registry_region = router_source[
        router_source.index("def _runtime_registry"):router_source.index("@router.get", router_source.index("def _runtime_registry"))
    ]
    assert "_frustrampnn_runtime.cm_analysis_runtime_registry_v1" in registry_region
    assert "_FRUSTRAMPNN_IMAGE_SHA256" not in router_source


def test_bms_api_python_is_provisioned_once_for_nextflow_and_cm_host_scripts() -> None:
    launcher = (REPO_ROOT / "scripts/run_biomodstack_workflow_adapter.sh").read_text(
        encoding="utf-8"
    )
    config = (REPO_ROOT / "nextflow.config").read_text(encoding="utf-8")
    cm_module = (REPO_ROOT / "modules/conformational_mapping_frustrampnn.nf").read_text(
        encoding="utf-8"
    )

    assert launcher.index("provision_cm_api_runtime") < launcher.index(
        'export BMS_API_PYTHON="$CM_API_RUNTIME_DIR/current/venv/bin/python"'
    )
    assert 'export BMS_API_PYTHON="$CM_API_RUNTIME_DIR/current/venv/bin/python"' in launcher
    assert "uv sync --locked" in launcher
    assert "flock -x 9" in launcher
    assert "mv -Tf \"$next_link\" \"$CM_API_RUNTIME_DIR/current\"" in launcher
    assert "api_python = System.getenv('BMS_API_PYTHON')" in config
    assert "prepare_conformational_mapping_frustrampnn_v2.py" in cm_module
    assert "postprocess_conformational_mapping_frustrampnn_v2.py" in cm_module
    assert "run_conformational_mapping_analysis_plane.py" not in cm_module
    subprocess.run(["bash", "-n", str(REPO_ROOT / "scripts/run_biomodstack_workflow_adapter.sh")], check=True)


def test_cm_adapter_keeps_normalized_input_outside_rw_output_and_owns_pinned_fd() -> None:
    source = (REPO_ROOT / "scripts/run_conformational_mapping_analysis_plane.py").read_text(
        encoding="utf-8"
    )
    assert 'runtime_output = candidate_root / ".frustrampnn-runtime-output"' in source
    assert "raw_runtime = runtime_output / raw.name" in source
    assert "output_root=runtime_output" in source
    assert "os.replace(raw_runtime, raw)" in source
    assert "PinnedContainer(" in source
    assert "container_fd, container_sha256" in source
    assert "container_pin.close()" in source
