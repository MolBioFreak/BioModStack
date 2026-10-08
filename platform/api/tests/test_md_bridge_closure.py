from __future__ import annotations

import copy
import hashlib
import json
import shutil
from pathlib import Path

import pytest

from scripts.bms_md.closure import aggregate, complete, load, validate_replicas
from scripts.bms_md.aggregate_children import ImmutableCollectionConflict
from scripts.bms_md.analysis import write_analysis_report

ROOT = Path(__file__).resolve().parents[3]
FIXTURES = ROOT / "tests/fixtures/bms_md_analysis"


def fixture_replicas(tmp_path, engine="openmm", count=2):
    source = FIXTURES / ("pdb_dcd_1u19_format_smoke" if engine == "openmm" else "gromacs_1u19_format_smoke")
    config = load(source / "manifest.json")["config"]
    config.update(job_id="closure-fixture", replicas=count, random_seed=17)
    directories = []
    for index in range(count):
        directory = tmp_path / f"replica_{index}"
        shutil.copytree(source, directory)
        record = load(directory / "manifest.json")
        record["artifacts"]["atom_order_manifest"]["atom_order_identity"] = record["artifacts"]["analysis_topology"]["atom_order_identity"]
        mda = pytest.importorskip("MDAnalysis")
        topology = directory / record["artifacts"]["analysis_topology"]["path"]
        trajectory = directory / record["artifacts"]["analysis_trajectory"]["path"]
        universe = mda.Universe(str(topology), str(trajectory))
        universe.trajectory[-1]
        final = directory / 'fixture_final.pdb'
        universe.atoms.write(str(final))
        record["artifacts"]["representative_structure"] = {
            "path": final.name, "bytes": final.stat().st_size,
            "sha256": hashlib.sha256(final.read_bytes()).hexdigest(),
            "semantic_role": "representative_structure",
            "selection_method": "completed_production_final_coordinates",
            "source_frame": len(universe.trajectory) - 1,
            "time_ps": float(universe.trajectory.ts.time),
            "source_trajectory_sha256": record["artifacts"]["analysis_trajectory"]["sha256"],
        }
        universe.trajectory.close()
        record.update(config=copy.deepcopy(config), job_id=config["job_id"], replica_index=index, replica_seed=17+index)
        (directory / "manifest.json").write_text(json.dumps(record))
        directories.append(directory)
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps(config))
    return config_path, directories


def native_analysis(root, tmp_path):
    # Actual pinned native algorithm over licensed, checked-in GRO/XTC or PDB/DCD
    # bytes. Fixture trajectories are not new MD engine/inference acceptance.
    pytest.importorskip("MDAnalysis")
    directories = []
    for replica in sorted((root / "replicas").iterdir()):
        index = load(replica / "manifest.json")["replica_index"]
        directory = tmp_path / f"analysis_{index}"
        directory.mkdir()
        _, success = write_analysis_report(
            replica / "manifest.json", directory / f"md_analysis_replica_{index}.json",
            runtime_sha256="3a74031e20dbd5012b7e532134f81816d596521dde47c4439fd1d6ae54fa5c68",
        )
        assert success, load(directory / f"md_analysis_replica_{index}.json")
        directories.append(directory)
    return directories


@pytest.mark.parametrize("engine", ["openmm", "gromacs"])
def test_native_fixture_exact_join_analysis_seal_restart(tmp_path, engine):
    config, replicas = fixture_replicas(tmp_path, engine)
    root = tmp_path / "aggregate"
    result = aggregate(config, list(reversed(replicas)), root)
    assert [r["replica_seed"] for r in result["replicas"]] == [17, 18]
    analyses = native_analysis(root, tmp_path)
    barrier = complete(root, analyses)
    sealed_bytes = (root / "md_completion_barrier.json").read_bytes()
    assert complete(root, list(reversed(analyses))) == barrier
    assert (root / "md_completion_barrier.json").read_bytes() == sealed_bytes
    aggregate(config, replicas, root)  # host reconnect/replayed aggregation is exact
    assert load(root / "analysis/manifest.json")["completed_analysis_children"] == 2
    # Exercise the unchanged native host completion/read-model contract, not a
    # bridge-specific reduced schema.
    from types import SimpleNamespace
    import services.md.results as results
    job = SimpleNamespace(id='closure-fixture', model_id='molecular_dynamics',
                          output_dir=str(root), child_output_dir=None,
                          params={'md_job_spec': load(config)}, provenance=None)
    # The test tree is outside the service data root by design.
    from unittest.mock import patch
    with patch.object(results, 'get_data_root', return_value=tmp_path):
        assert results.completion_barrier(job)['state'] == 'completed'


@pytest.mark.parametrize("failure", ["missing", "duplicate", "seed", "settings", "failed", "corrupt"])
def test_replica_join_fails_closed(tmp_path, failure):
    config, replicas = fixture_replicas(tmp_path)
    if failure == "missing":
        replicas.pop()
    elif failure == "duplicate":
        replicas[1] = replicas[0]
    else:
        manifest = replicas[1] / "manifest.json"
        value = load(manifest)
        if failure == "seed": value["replica_seed"] += 1
        if failure == "settings": value["config"]["random_seed"] += 1
        if failure == "failed": value["status"] = "failed"
        if failure == "corrupt": (replicas[1] / "trajectory.dcd").write_bytes(b"corrupt fixture")
        manifest.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        aggregate(config, replicas, tmp_path / "aggregate")
    assert not (tmp_path / "aggregate/manifest.json").exists()


@pytest.mark.parametrize("failure", ["missing", "duplicate", "failed", "cancelled", "corrupt"])
def test_analysis_failure_and_cancellation_never_seal(tmp_path, failure):
    config, replicas = fixture_replicas(tmp_path)
    root = tmp_path / "aggregate"
    aggregate(config, replicas, root)
    analyses = native_analysis(root, tmp_path)
    if failure == "missing": analyses.pop()
    if failure == "duplicate": analyses[1] = analyses[0]
    if failure == "failed":
        sidecar = analyses[1] / "md_analysis_replica_1.artifacts.json"
        value = load(sidecar); value["status"] = "failed"; sidecar.write_text(json.dumps(value))
    if failure == "corrupt":
        (analyses[1] / "md_analysis_replica_1.timeseries.parquet").write_bytes(b"bad")
    with pytest.raises(ValueError):
        complete(root, analyses, cancelled=failure == "cancelled")
    assert not (root / "md_completion_barrier.json").exists()


def test_crash_before_barrier_replays_native_collection(tmp_path, monkeypatch):
    import scripts.bms_md.closure as closure
    config, replicas = fixture_replicas(tmp_path, count=1)
    root = tmp_path / "aggregate"
    aggregate(config, replicas, root)
    analyses = native_analysis(root, tmp_path)
    publish = closure.publish_json_immutable
    def crash(payload, destination):
        if destination.name == "md_completion_barrier.json":
            raise RuntimeError("injected crash after collection")
        return publish(payload, destination)
    with monkeypatch.context() as context:
        context.setattr(closure, "publish_json_immutable", crash)
        with pytest.raises(RuntimeError, match="injected crash"):
            complete(root, analyses)
    assert not (root / "md_completion_barrier.json").exists()
    assert complete(root, analyses)["status"] == "completed"
    barrier = root / "md_completion_barrier.json"
    barrier.write_text('{"conflicting": true}')
    with pytest.raises(ImmutableCollectionConflict):
        complete(root, analyses)


def test_plan_projection_preserves_complete_request_and_exact_dependencies():
    from scripts.bms_md.closure_plan import compile_md_closure
    config = load(FIXTURES / 'pdb_dcd_1u19_format_smoke/manifest.json')['config']
    config.update(replicas=3, random_seed=2147483647)
    original = copy.deepcopy(config)
    plan = compile_md_closure(config)
    assert config == original == plan['requested_config']
    nodes = {node['id']: node for node in plan['components']}
    prefix = config['job_id'] + ':'
    assert [nodes[prefix + f'md_replica:{i}']['replica_seed'] for i in range(3)] == [2147483647, 1, 2]
    assert nodes[prefix + 'md_aggregation']['requires'] == [prefix + f'md_replica:{i}' for i in range(3)]
    assert nodes[prefix + 'md_completion']['requires'] == [prefix + 'md_aggregation', *[prefix + f'md_analysis:{i}' for i in range(3)]]
    assert all(node['required'] for node in nodes.values())


def test_orchestrator_uses_native_dag_not_host_callbacks():
    source = (ROOT / "workflows/experimental/molecular_dynamics/orchestrator.nf").read_text()
    for forbidden in ("api_url", "SPAWN_", "WAIT_FOR_", "wait_for_children", "requests.post"):
        assert forbidden not in source
    for required in ("MD_PREPARE_CONFIG", "MD_GROMACS_REPLICA", "MD_OPENMM_REPLICA", "MD_JOIN_REPLICAS", "MD_ANALYZE_REPLICA", "MD_SEAL_RESULTS"):
        assert required in source
    assert "out.artifacts).toList()" in source
    assert "out.native_results.toList()" in source
