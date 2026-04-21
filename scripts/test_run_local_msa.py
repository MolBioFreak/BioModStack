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
