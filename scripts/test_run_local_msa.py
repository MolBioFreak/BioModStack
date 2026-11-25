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


def test_local_mmseqs_provider_fails_closed(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="Local MSA search is disabled"):
        run_local_msa.run_colabfold_msa_workflow(sequence="ACDEFGHIK", job_name="x", out_dir=str(tmp_path))

    with pytest.raises(ValueError, match="Local MSA search is disabled"):
        run_local_msa.main([
            "--sequence",
            "ACDEFGHIK",
            "--name",
            "local_job",
            "--out_dir",
            str(tmp_path),
            "--msa-provider",
            "local",
        ])
