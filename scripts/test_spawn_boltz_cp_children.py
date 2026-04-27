from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest

SCRIPT_ROOT = Path(__file__).resolve().parent
FOLD_CP_ROOT = Path("/home/dalab/tmp/boltz-cp")
FOLD_CP_SRC = FOLD_CP_ROOT / "src"
if str(SCRIPT_ROOT) not in sys.path:
    sys.path.insert(0, str(SCRIPT_ROOT))
if str(FOLD_CP_SRC) not in sys.path:
    sys.path.insert(0, str(FOLD_CP_SRC))

import spawn_boltz_cp_children as spawn_module
from boltz.distributed.large_protein.tile_store import StoreLayout
from boltz.distributed.large_protein.worker import execute_bundle_worker


def _run_fold_cp_large_protein(*args: str) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUNBUFFERED", "1")
    return subprocess.run(
        ["uv", "run", "--extra", "test", "python", "-m", "boltz.distributed.main", "large-protein", *args],
        cwd=FOLD_CP_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


class _DummyResponse:
    def __init__(self, job_id: str) -> None:
        self.ok = True
        self.status_code = 200
        self.text = ""
        self._job_id = job_id

    def json(self) -> dict[str, str]:
        return {"id": self._job_id}


def _init_smoke_plan_store(tmp_path: Path, *, job_id: str, physical_gpu_ids: list[int]) -> tuple[Path, Path, Path]:
    input_yaml = tmp_path / "input.yaml"
    input_yaml.write_text(
        json.dumps(
            {
                "version": 1,
                "sequences": [
                    {"protein": {"id": ["A"], "sequence": "MKTIIALSYIFCLVFADYKDDDDA", "msa": "empty"}}
                ],
            }
        ),
        encoding="utf-8",
    )

    input_metadata = {
        "job_id": job_id,
        "input_path": str(input_yaml),
        "input_format": "config_files",
        "output_format": "mmcif",
        "repo_path": str(FOLD_CP_ROOT),
        "sequence_length": 24,
        "physical_gpu_ids": physical_gpu_ids,
    }
    init_plan = _run_fold_cp_large_protein(
        "init-plan",
        "--input-metadata-json",
        json.dumps(input_metadata, separators=(",", ":")),
        "--grid-size",
        "2x2",
        "--required-bytes",
        "1048576",
        "--fallback-root",
        str(tmp_path / "store"),
        "--configured-ram-root",
        str(tmp_path / "store"),
    )
    assert init_plan.returncode == 0, init_plan.stderr
    store_root = Path(init_plan.stdout.strip())
    assert store_root.exists()

    manifest_path = tmp_path / "boltz_cp_plan_manifest.json"
    plan_manifest_path = store_root / "metadata" / "plan_manifest.json"
    manifest_path.write_text(plan_manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

    plan_store_path = tmp_path / "boltz_cp_plan_store.json"
    plan_store_path.write_text(
        json.dumps(
            {
                "plan_id": job_id,
                "store_root": str(store_root),
                "plan_manifest_path": str(plan_manifest_path),
            }
        ),
        encoding="utf-8",
    )
    return store_root, manifest_path, plan_store_path


def _spawn_bundles(
    *,
    parent_job_id: str,
    manifest_path: Path,
    plan_store_path: Path,
    batch_name: str,
    monkeypatch,
) -> tuple[dict, list[dict], list[str]]:
    monkeypatch.setattr(spawn_module, "check_existing_children", lambda *args, **kwargs: (False, [], {}))

    captured_posts: list[dict] = []
    release_calls: list[str] = []

    def fake_post(url: str, json: dict | None = None, timeout: int = 10):
        if url.endswith("/api/jobs"):
            captured_posts.append(json or {})
            return _DummyResponse(f"child-{len(captured_posts)}")
        if url.endswith(f"/api/queue/{parent_job_id}/release-gpu"):
            release_calls.append(url)
            return _DummyResponse(parent_job_id)
        raise AssertionError(f"Unexpected POST url: {url}")

    monkeypatch.setattr(spawn_module.requests, "post", fake_post)

    spawn_result = spawn_module.spawn_boltz_cp_children(
        parent_job_id=parent_job_id,
        manifest_path=str(manifest_path),
        plan_store_path=str(plan_store_path),
        batch_name=batch_name,
        api_url="http://api.test",
    )
    return spawn_result, captured_posts, release_calls



def _seed_fake_shared_prediction(store_root: str | Path, *, sequence_length: int) -> None:
    layout = StoreLayout.from_root(store_root)
    if layout.shared_prediction_manifest_path.exists():
        return

    prediction_dir = layout.shared_prediction_dir()
    prediction_dir.mkdir(parents=True, exist_ok=True)

    residue_index = list(range(sequence_length))
    s = np.arange(sequence_length * 2, dtype=np.float32).reshape(1, sequence_length, 2)
    z = np.arange(sequence_length * sequence_length * 3, dtype=np.float32).reshape(1, sequence_length, sequence_length, 3)
    pae = np.arange(sequence_length * sequence_length, dtype=np.float32).reshape(sequence_length, sequence_length)
    pde = pae + 1000.0
    plddt = np.arange(sequence_length, dtype=np.float32)

    structure_path = prediction_dir / "fake_target_model_0.cif"
    confidence_path = prediction_dir / "confidence_fake_target_model_0.json"
    embeddings_path = prediction_dir / "embeddings_fake_target.npz"
    pae_path = prediction_dir / "pae_fake_target_model_0.npz"
    pde_path = prediction_dir / "pde_fake_target_model_0.npz"
    plddt_path = prediction_dir / "plddt_fake_target_model_0.npz"
    token_index_path = prediction_dir / "token_index.json"

    structure_path.write_text("data_fake_target\n", encoding="utf-8")
    confidence_path.write_text(json.dumps({"confidence_score": 0.91}), encoding="utf-8")
    np.savez_compressed(embeddings_path, s=s, z=z)
    np.savez_compressed(pae_path, pae=pae)
    np.savez_compressed(pde_path, pde=pde)
    np.savez_compressed(plddt_path, plddt=plddt)
    token_index_path.write_text(json.dumps({"token_count": sequence_length, "residue_index": residue_index}), encoding="utf-8")
    layout.shared_prediction_manifest_path.write_text(
        json.dumps(
            {
                "backend": "fake",
                "record_id": "fake_target",
                "artifacts": {
                    "structure_path": str(structure_path.relative_to(layout.root)),
                    "confidence_path": str(confidence_path.relative_to(layout.root)),
                    "embeddings_path": str(embeddings_path.relative_to(layout.root)),
                    "pae_path": str(pae_path.relative_to(layout.root)),
                    "pde_path": str(pde_path.relative_to(layout.root)),
                    "plddt_path": str(plddt_path.relative_to(layout.root)),
                    "token_index_path": str(token_index_path.relative_to(layout.root)),
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )



def _run_spawned_bundle_workers(captured_posts: list[dict]) -> list[int | None]:
    assigned_gpus: list[int | None] = []
    if captured_posts:
        first_params = captured_posts[0]["params"]
        _seed_fake_shared_prediction(first_params["bcp_store_root"], sequence_length=24)
    for job in captured_posts:
        params = job["params"]
        assigned_gpu = params.get("bcp_assigned_gpu")
        assigned_gpus.append(assigned_gpu)
        run_args = ["run-bundle", "--store-root", params["bcp_store_root"], "--bundle-id", params["bcp_bundle_id"]]
        if assigned_gpu is not None:
            run_args.extend(["--assigned-gpu", str(assigned_gpu)])
        run_bundle = _run_fold_cp_large_protein(*run_args)
        assert run_bundle.returncode == 0, run_bundle.stderr
    return assigned_gpus

def test_spawn_boltz_cp_children_propagates_store_root_and_assigned_gpu(tmp_path, monkeypatch) -> None:
    manifest_path = tmp_path / "boltz_cp_plan_manifest.json"
    manifest = {
        "plan_id": "parent-job-1",
        "shard_plan": {"name": "2x2"},
        "input_metadata": {
            "input_path": "/tmp/example-input.yaml",
            "input_format": "config_files",
            "output_format": "mmcif",
            "write_full_pae": False,
            "repo_path": "/repo/boltz-cp",
            "container_path": "/containers/boltz.sif",
            "physical_gpu_ids": [5, 6],
            "bcp_backend": "shared-cache-serial-output-tiling",
            "bcp_context_store_manifest_path": "/shared/context/manifest.json",
            "bcp_context_execution_mode": "cuda",
            "bcp_context_tile_tokens": 256,
            "bcp_context_key_tile_tokens": 128,
            "bcp_context_query_tile_tokens": 64,
        },
        "bundles": [
            {
                "bundle_id": "bundle-r00-c00",
                "row_index": 0,
                "col_index": 0,
                "row_range": [0, 10],
                "col_range": [0, 10],
            },
            {
                "bundle_id": "bundle-r00-c01",
                "row_index": 0,
                "col_index": 1,
                "row_range": [0, 10],
                "col_range": [10, 20],
            },
        ],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    plan_store_path = tmp_path / "boltz_cp_plan_store.json"
    plan_store = {
        "plan_id": "parent-job-1",
        "store_root": "/shared/boltz-cp/store/parent-job-1",
        "plan_manifest_path": str(manifest_path.resolve()),
    }
    plan_store_path.write_text(json.dumps(plan_store), encoding="utf-8")

    monkeypatch.setattr(spawn_module, "check_existing_children", lambda *args, **kwargs: (False, [], {}))

    captured_posts: list[tuple[str, dict | None, int]] = []

    def fake_post(url: str, json: dict | None = None, timeout: int = 10):
        captured_posts.append((url, json, timeout))
        if url.endswith("/api/jobs"):
            child_count = sum(1 for posted_url, _, _ in captured_posts if posted_url.endswith("/api/jobs"))
            return _DummyResponse(f"child-{child_count}")
        if url.endswith("/api/queue/parent-job-1/release-gpu"):
            return _DummyResponse("parent-job-1")
        raise AssertionError(f"Unexpected POST url: {url}")

    monkeypatch.setattr(spawn_module.requests, "post", fake_post)

    result = spawn_module.spawn_boltz_cp_children(
        parent_job_id="parent-job-1",
        manifest_path=str(manifest_path),
        plan_store_path=str(plan_store_path),
        batch_name="cp-parent-job-1",
        api_url="http://api.test",
    )

    child_posts = [payload for url, payload, _timeout in captured_posts if url.endswith("/api/jobs")]
    release_posts = [url for url, _payload, _timeout in captured_posts if url.endswith("/api/queue/parent-job-1/release-gpu")]

    assert result["status"] == "complete"
    assert result["spawned_jobs"] == 2
    assert len(child_posts) == 2
    assert release_posts == ["http://api.test/api/queue/parent-job-1/release-gpu"]

    first_job = child_posts[0]
    second_job = child_posts[1]

    assert first_job["params"]["bcp_store_root"] == "/shared/boltz-cp/store/parent-job-1"
    assert second_job["params"]["bcp_store_root"] == "/shared/boltz-cp/store/parent-job-1"
    assert first_job["params"]["bcp_plan_manifest_path"] == str(manifest_path.resolve())
    assert second_job["params"]["bcp_plan_manifest_path"] == str(manifest_path.resolve())
    assert first_job["params"]["bcp_bundle_id"] == "bundle-r00-c00"
    assert second_job["params"]["bcp_bundle_id"] == "bundle-r00-c01"
    assert first_job["params"]["bcp_assigned_gpu"] == 5
    assert second_job["params"]["bcp_assigned_gpu"] == 6
    assert first_job["params"]["bcp_gpu_ids"] == "5"
    assert second_job["params"]["bcp_gpu_ids"] == "6"
    assert first_job["params"]["pinned_gpus"] == [5]
    assert second_job["params"]["pinned_gpus"] == [6]
    assert first_job["params"]["bcp_size_cp"] == 1
    assert second_job["params"]["bcp_size_cp"] == 1
    assert first_job["params"]["bcp_backend"] == "shared-cache-serial-output-tiling"
    assert second_job["params"]["bcp_backend"] == "shared-cache-serial-output-tiling"
    assert first_job["params"]["bcp_context_store_manifest_path"] == "/shared/context/manifest.json"
    assert second_job["params"]["bcp_context_store_manifest_path"] == "/shared/context/manifest.json"
    assert first_job["params"]["bcp_context_execution_mode"] == "cuda"
    assert second_job["params"]["bcp_context_execution_mode"] == "cuda"
    assert first_job["params"]["bcp_context_tile_tokens"] == 256
    assert first_job["params"]["bcp_context_key_tile_tokens"] == 128
    assert first_job["params"]["bcp_context_query_tile_tokens"] == 64


def test_spawn_children_smoke_proves_shared_store_bundle_execution_and_finalize(tmp_path, monkeypatch) -> None:
    input_yaml = tmp_path / "input.yaml"
    input_yaml.write_text(
        json.dumps(
            {
                "version": 1,
                "sequences": [
                    {"protein": {"id": ["A"], "sequence": "MKTIIALSYIFCLVFADYKDDDDA", "msa": "empty"}}
                ],
            }
        ),
        encoding="utf-8",
    )

    input_metadata = {
        "job_id": "parent-job-smoke",
        "input_path": str(input_yaml),
        "input_format": "config_files",
        "output_format": "mmcif",
        "repo_path": str(FOLD_CP_ROOT),
        "sequence_length": 24,
        "physical_gpu_ids": [5, 6],
    }
    init_plan = _run_fold_cp_large_protein(
        "init-plan",
        "--input-metadata-json",
        json.dumps(input_metadata, separators=(",", ":")),
        "--grid-size",
        "2x2",
        "--required-bytes",
        "1048576",
        "--fallback-root",
        str(tmp_path / "store"),
        "--configured-ram-root",
        str(tmp_path / "store"),
    )
    assert init_plan.returncode == 0, init_plan.stderr
    store_root = Path(init_plan.stdout.strip())
    assert store_root.exists()

    manifest_path = tmp_path / "boltz_cp_plan_manifest.json"
    plan_manifest_path = store_root / "metadata" / "plan_manifest.json"
    manifest_path.write_text(plan_manifest_path.read_text(encoding="utf-8"), encoding="utf-8")

    plan_store_path = tmp_path / "boltz_cp_plan_store.json"
    plan_store_path.write_text(
        json.dumps(
            {
                "plan_id": "parent-job-smoke",
                "store_root": str(store_root),
                "plan_manifest_path": str(plan_manifest_path),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(spawn_module, "check_existing_children", lambda *args, **kwargs: (False, [], {}))

    captured_posts: list[dict] = []
    release_calls: list[str] = []

    def fake_post(url: str, json: dict | None = None, timeout: int = 10):
        if url.endswith("/api/jobs"):
            captured_posts.append(json or {})
            return _DummyResponse(f"child-{len(captured_posts)}")
        if url.endswith("/api/queue/parent-job-smoke/release-gpu"):
            release_calls.append(url)
            return _DummyResponse("parent-job-smoke")
        raise AssertionError(f"Unexpected POST url: {url}")

    monkeypatch.setattr(spawn_module.requests, "post", fake_post)

    spawn_result = spawn_module.spawn_boltz_cp_children(
        parent_job_id="parent-job-smoke",
        manifest_path=str(manifest_path),
        plan_store_path=str(plan_store_path),
        batch_name="cp-parent-job-smoke",
        api_url="http://api.test",
    )

    assert spawn_result["status"] == "complete"
    assert spawn_result["spawned_jobs"] == 4
    assert len(captured_posts) == 4
    assert release_calls == ["http://api.test/api/queue/parent-job-smoke/release-gpu"]

    assigned_gpus = _run_spawned_bundle_workers(captured_posts)

    finalize = _run_fold_cp_large_protein("finalize", "--store-root", str(store_root))
    assert finalize.returncode == 0, finalize.stderr
    summary = json.loads(finalize.stdout.strip())

    assert assigned_gpus == [5, 6, 5, 6]
    assert summary["status"] == "complete"
    assert summary["bundle_count"] == 4
    assert summary["completed_bundle_count"] == 4
    assert summary["failed_bundle_count"] == 0
    assert set(summary["results"]) == {
        "bundle-r00-c00",
        "bundle-r00-c01",
        "bundle-r01-c00",
        "bundle-r01-c01",
    }
    assert summary["results"]["bundle-r00-c00"]["assigned_gpu"] == 5
    assert summary["results"]["bundle-r00-c01"]["assigned_gpu"] == 6
    assert summary["results"]["bundle-r01-c00"]["assigned_gpu"] == 5
    assert summary["results"]["bundle-r01-c01"]["assigned_gpu"] == 6
    assert (store_root / "metadata" / "summary.json").exists()


def test_spawn_children_keep_logical_bundle_geometry_invariant_across_gpu_pools(tmp_path, monkeypatch) -> None:
    observed_bundle_geometry: dict[str, list[tuple[str, tuple[int, int], tuple[int, int]]]] = {}
    observed_result_geometry: dict[str, dict[str, tuple[tuple[int, int], tuple[int, int]]]] = {}
    observed_assignments: dict[str, list[int | None]] = {}

    for case_name, physical_gpu_ids, expected_assignments in (
        ("one_gpu", [7], [7, 7, 7, 7]),
        ("two_gpus", [11, 12], [11, 12, 11, 12]),
    ):
        case_dir = tmp_path / case_name
        case_dir.mkdir()
        store_root, manifest_path, plan_store_path = _init_smoke_plan_store(
            case_dir,
            job_id=f"parent-job-{case_name}",
            physical_gpu_ids=physical_gpu_ids,
        )
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        observed_bundle_geometry[case_name] = [
            (bundle["bundle_id"], tuple(bundle["row_range"]), tuple(bundle["col_range"])) for bundle in manifest["bundles"]
        ]

        spawn_result, captured_posts, release_calls = _spawn_bundles(
            parent_job_id=f"parent-job-{case_name}",
            manifest_path=manifest_path,
            plan_store_path=plan_store_path,
            batch_name=f"cp-parent-job-{case_name}",
            monkeypatch=monkeypatch,
        )
        assert spawn_result["status"] == "complete"
        assert spawn_result["spawned_jobs"] == 4
        assert release_calls == [f"http://api.test/api/queue/parent-job-{case_name}/release-gpu"]

        observed_assignments[case_name] = _run_spawned_bundle_workers(captured_posts)
        assert observed_assignments[case_name] == expected_assignments

        finalize = _run_fold_cp_large_protein("finalize", "--store-root", str(store_root))
        assert finalize.returncode == 0, finalize.stderr
        summary = json.loads(finalize.stdout.strip())
        observed_result_geometry[case_name] = {
            bundle_id: (tuple(result["row_range"]), tuple(result["col_range"]))
            for bundle_id, result in summary["results"].items()
        }
        assert summary["status"] == "complete"
        assert summary["bundle_ids"] == [
            "bundle-r00-c00",
            "bundle-r00-c01",
            "bundle-r01-c00",
            "bundle-r01-c01",
        ]

    assert observed_bundle_geometry["one_gpu"] == observed_bundle_geometry["two_gpus"]
    assert observed_result_geometry["one_gpu"] == observed_result_geometry["two_gpus"]



def test_spawn_children_finalize_surfaces_injected_bundle_failure_from_shared_store(tmp_path, monkeypatch) -> None:
    store_root, manifest_path, plan_store_path = _init_smoke_plan_store(
        tmp_path,
        job_id="parent-job-failure",
        physical_gpu_ids=[5, 6],
    )
    spawn_result, captured_posts, release_calls = _spawn_bundles(
        parent_job_id="parent-job-failure",
        manifest_path=manifest_path,
        plan_store_path=plan_store_path,
        batch_name="cp-parent-job-failure",
        monkeypatch=monkeypatch,
    )
    assert spawn_result["status"] == "complete"
    assert spawn_result["spawned_jobs"] == 4
    assert release_calls == ["http://api.test/api/queue/parent-job-failure/release-gpu"]

    failing_params = captured_posts[0]["params"]

    def failing_executor(_context):
        raise RuntimeError("injected bundle failure")

    with pytest.raises(RuntimeError, match="injected bundle failure"):
        execute_bundle_worker(
            store_root=failing_params["bcp_store_root"],
            bundle_id=failing_params["bcp_bundle_id"],
            assigned_gpu=failing_params["bcp_assigned_gpu"],
            executor=failing_executor,
        )

    completed_assignments = _run_spawned_bundle_workers(captured_posts[1:])
    assert completed_assignments == [6, 5, 6]

    finalize = _run_fold_cp_large_protein("finalize", "--store-root", str(store_root))
    assert finalize.returncode != 0
    summary = json.loads(finalize.stdout.strip())

    assert summary["status"] == "failed"
    assert summary["bundle_count"] == 4
    assert summary["completed_bundle_count"] == 3
    assert summary["failed_bundle_count"] == 1
    assert summary["failed_bundle_ids"] == ["bundle-r00-c00"]
    assert summary["pending_bundle_ids"] == []
    assert summary["running_bundle_ids"] == []
    assert summary["failures"]["bundle-r00-c00"]["error"] == "injected bundle failure"
    assert summary["failures"]["bundle-r00-c00"]["kind"] == "RuntimeError"
    assert summary["results"]["bundle-r00-c01"]["assigned_gpu"] == 6
    assert summary["results"]["bundle-r01-c00"]["assigned_gpu"] == 5
    assert summary["results"]["bundle-r01-c01"]["assigned_gpu"] == 6
    assert (store_root / "bundles" / "bundle-r00-c00" / "failure.json").exists()
