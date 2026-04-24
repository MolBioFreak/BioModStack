import importlib.util
from pathlib import Path

import pytest


MODULE_PATH = Path(__file__).with_name("run_local_msa.py")
SPEC = importlib.util.spec_from_file_location("run_local_msa_module", MODULE_PATH)
run_local_msa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(run_local_msa)


def test_main_does_not_pass_local_only_kwargs_to_colabfold_api(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_api_workflow(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(run_local_msa, "run_colabfold_api_msa_workflow", fake_api_workflow)
    monkeypatch.setattr(
        run_local_msa,
        "run_colabfold_msa_workflow",
        lambda **kwargs: pytest.fail("local workflow should not be called for colabfold_api provider"),
    )

    exit_code = run_local_msa.main([
        "--sequence",
        "ACDEFGHIK",
        "--name",
        "api_job",
        "--out_dir",
        str(tmp_path),
        "--msa-provider",
        "colabfold_api",
        "--disallow-cpu-fallback",
    ])

    assert exit_code == 0
    assert captured["sequence"] == "ACDEFGHIK"
    assert captured["job_name"] == "api_job"
    assert "disallow_cpu_fallback" not in captured


def test_run_colabfold_msa_workflow_binds_uniref_db_for_search(monkeypatch, tmp_path: Path) -> None:
    search_calls = []
    sentinel = RuntimeError("stop after search")

    monkeypatch.setattr(run_local_msa, "acquire_msa_lock", lambda path: object())
    monkeypatch.setattr(run_local_msa, "release_msa_lock", lambda fd: None)
    monkeypatch.setattr(run_local_msa, "check_cache", lambda **kwargs: None)
    monkeypatch.setattr(run_local_msa, "touch_gpuserver_query_activity", lambda **kwargs: None)
    monkeypatch.setattr(
        run_local_msa,
        "resolve_mmseqs_binaries",
        lambda db_path: (Path("/bin/echo"), Path("/bin/echo")),
    )
    monkeypatch.setattr(
        run_local_msa,
        "inspect_mmseqs_runtime",
        lambda **kwargs: {
            "status": "cpu_forced",
            "mmseqs_bin": "/bin/echo",
            "summary_message": "CPU forced",
            "normalized_gpu_mode": "cpu",
            "normalized_gpu_server_mode": "off",
            "effective_gpu_server_wait_timeout": 0,
            "selected_gpu_id": None,
            "use_gpu_mmseqs": False,
        },
    )

    def fake_run_mmseqs(mmseqs_bin, args, env):
        if args[0] == "search":
            search_calls.append(args)
            raise sentinel

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    with pytest.raises(RuntimeError, match="stop after search"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="local_job",
            out_dir=str(tmp_path / "out"),
            db_path=str(tmp_path / "db"),
            cache_dir=str(tmp_path / "cache"),
            preset="fast",
            cpu_only=True,
        )

    assert search_calls, "expected the UniRef search step to run"
    assert search_calls[0][2] == str(tmp_path / "db" / "uniref30_2302_db")


def test_is_matching_gpuserver_process_rejects_empty_cmdline(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(run_local_msa, "_pid_is_alive", lambda pid: True)
    monkeypatch.setattr(run_local_msa, "_read_proc_cmdline", lambda pid: "")

    assert run_local_msa._is_matching_gpuserver_process(123, tmp_path / "uniref30_2302_db") is False


def test_inspect_mmseqs_runtime_keeps_requested_gpuserver_wait_timeout(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        run_local_msa,
        "resolve_mmseqs_binaries",
        lambda db_path: (Path("/bin/echo"), Path("/bin/echo")),
    )

    runtime = run_local_msa.inspect_mmseqs_runtime(
        db_path=str(tmp_path),
        cache_dir=str(tmp_path / "cache"),
        cpu_only=True,
        gpu_server_wait_timeout=120,
        verbose=False,
    )

    assert runtime["effective_gpu_server_wait_timeout"] == 120


def test_build_arg_parser_defaults_gpuserver_client_db_load_mode_to_fast_path() -> None:
    parser = run_local_msa.build_arg_parser()

    args = parser.parse_args([
        "--sequence",
        "ACDEFGHIK",
        "--name",
        "parser_defaults",
        "--out_dir",
        "/tmp/out",
    ])

    assert args.gpu_server_db_load_mode == 2


def test_build_arg_parser_defaults_target_sharding_to_adaptive_auto() -> None:
    parser = run_local_msa.build_arg_parser()

    args = parser.parse_args([
        "--sequence",
        "ACDEFGHIK",
        "--name",
        "parser_defaults",
        "--out_dir",
        "/tmp/out",
    ])

    assert args.target_shard_mode == "auto"
    assert args.target_shards == 4


def test_main_passes_target_sharding_options_to_local_executor(monkeypatch, tmp_path: Path) -> None:
    captured = {}

    def fake_local_workflow(**kwargs):
        captured.update(kwargs)

    monkeypatch.setattr(run_local_msa, "run_colabfold_msa_workflow", fake_local_workflow)
    monkeypatch.setattr(
        run_local_msa,
        "run_colabfold_api_msa_workflow",
        lambda **kwargs: pytest.fail("remote API workflow should not be called for local provider"),
    )

    exit_code = run_local_msa.main([
        "--sequence",
        "ACDEFGHIK",
        "--name",
        "sharded_local_job",
        "--out_dir",
        str(tmp_path),
        "--target-shard-mode",
        "required",
        "--target-shards",
        "2",
        "--target-shard-min-size-gb",
        "0",
    ])

    assert exit_code == 0
    assert captured["target_shard_mode"] == "required"
    assert captured["target_shards"] == 2
    assert captured["target_shard_min_size_gb"] == 0



def test_run_colabfold_msa_workflow_reuses_runtime_preferred_gpus_for_envdb_reclaim(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sentinel = RuntimeError("envdb reclaim reached")

    db_dir = tmp_path / "db"
    db_dir.mkdir()
    (db_dir / "colabfold_envdb_202108_db").write_text("", encoding="utf-8")
    (db_dir / "colabfold_envdb_202108_db.dbtype").write_text("", encoding="utf-8")

    monkeypatch.setattr(run_local_msa, "acquire_msa_lock", lambda path: object())
    monkeypatch.setattr(run_local_msa, "release_msa_lock", lambda fd: None)
    monkeypatch.setattr(run_local_msa, "check_cache", lambda **kwargs: None)
    monkeypatch.setattr(run_local_msa, "touch_gpuserver_query_activity", lambda **kwargs: None)
    monkeypatch.setattr(
        run_local_msa,
        "resolve_mmseqs_binaries",
        lambda db_path: (Path("/bin/echo"), Path("/bin/echo")),
    )
    monkeypatch.setattr(
        run_local_msa,
        "inspect_mmseqs_runtime",
        lambda **kwargs: {
            "status": "gpu_ready",
            "mmseqs_bin": "/bin/echo",
            "summary_message": "Using GPU mmseqs on device 1",
            "normalized_gpu_mode": "opportunistic",
            "normalized_gpu_server_mode": "persistent",
            "effective_gpu_server_wait_timeout": 120,
            "selected_gpu_id": 1,
            "use_gpu_mmseqs": True,
            "isolated_task_context": False,
            "effective_preferred_gpus": [1],
        },
    )
    monkeypatch.setattr(
        run_local_msa,
        "ensure_persistent_mmseqs_gpuserver",
        lambda **kwargs: {"reused": True, "pid": 1234},
    )
    monkeypatch.setattr(run_local_msa, "run_mmseqs", lambda *args, **kwargs: None)

    def fake_reclaim_conflicting_gpuserver_instances(*, target_db, preferred_gpus, cache_dir):
        assert target_db.name == "colabfold_envdb_202108_db"
        assert preferred_gpus == [1]
        raise sentinel

    monkeypatch.setattr(
        run_local_msa,
        "reclaim_conflicting_gpuserver_instances",
        fake_reclaim_conflicting_gpuserver_instances,
    )

    with pytest.raises(RuntimeError, match="envdb reclaim reached"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="envdb_reclaim",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="balanced",
            preferred_gpus=[1],
            use_gpu=True,
        )
