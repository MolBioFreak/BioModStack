import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("check_protenix_msa_preflight.py")
SCRIPT_DIR = MODULE_PATH.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("check_protenix_msa_preflight_module", MODULE_PATH)
check_protenix_msa_preflight = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(check_protenix_msa_preflight)


def test_preflight_uses_effective_protenix_gpu_server_mode_and_reports_it(
    tmp_path: Path,
    monkeypatch,
) -> None:
    captured = {}

    monkeypatch.setattr(check_protenix_msa_preflight, "load_json", lambda _path: [])
    monkeypatch.setattr(
        check_protenix_msa_preflight,
        "summarize_payload",
        lambda _payload: {"tasks": 1, "protein_chains": 1, "unique_sequences": 1, "total_residues": 9},
    )
    monkeypatch.setattr(check_protenix_msa_preflight, "choose_backend", lambda **_kwargs: "local")

    def _fake_inspect_mmseqs_runtime(**kwargs):
        captured.update(kwargs)
        return {
            "status": "ready",
            "use_gpu_mmseqs": True,
            "summary_message": "GPU MMseqs ready",
            "effective_preferred_gpus": [0],
            "effective_excluded_gpus": [],
            "selected_gpu_id": 0,
        }

    monkeypatch.setattr(check_protenix_msa_preflight, "inspect_mmseqs_runtime", _fake_inspect_mmseqs_runtime)

    input_json = tmp_path / "input.json"
    input_json.write_text("[]", encoding="utf-8")
    output_path = tmp_path / "report.json"

    argv = [
        "check_protenix_msa_preflight.py",
        "--input_json",
        str(input_json),
        "--output",
        str(output_path),
        "--backend",
        "local",
        "--db-path",
        "/custom/db",
        "--cache-dir",
        "/custom/cache",
        "--gpu-server-mode",
        "persistent",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    check_protenix_msa_preflight.main()

    report = json.loads(output_path.read_text(encoding="utf-8"))
    assert captured["gpu_server_mode"] == "off"
    assert report["local_msa_runtime_contract"] == {
        "requested_gpu_server_mode": "persistent",
        "effective_gpu_server_mode": "off",
    }
    assert report["local_msa_runtime"]["selected_gpu_id"] == 0
