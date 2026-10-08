import importlib.util
import json
import sys
from pathlib import Path


MODULE_PATH = Path(__file__).with_name("prepare_protenix_msa.py")
SCRIPT_DIR = MODULE_PATH.parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location("prepare_protenix_msa_module", MODULE_PATH)
prepare_protenix_msa = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(prepare_protenix_msa)

_write_sanitized_a3m = prepare_protenix_msa._write_sanitized_a3m
prepare_with_local_msa = prepare_protenix_msa.prepare_with_local_msa
write_msa_report = prepare_protenix_msa.write_msa_report


def test_write_sanitized_a3m_drops_inconsistent_aligned_rows(tmp_path: Path) -> None:
    src = tmp_path / "input.a3m"
    dst = tmp_path / "output.a3m"
    src.write_text(
        "\n".join(
                [
                    ">query",
                    "ABCD",
                    ">good_hit",
                    "ABcCD",
                    ">bad_hit",
                    "ABC",
                "",
            ]
        ),
        encoding="utf-8",
    )

    _write_sanitized_a3m(src, dst, query_sequence="ABCD")

    assert dst.read_text(encoding="utf-8").splitlines() == [
        ">query",
        "ABCD",
        ">good_hit",
        "ABcCD",
    ]


def test_write_sanitized_a3m_prunes_low_coverage_and_caps_rows(tmp_path: Path) -> None:
    src = tmp_path / "input.a3m"
    dst = tmp_path / "output.a3m"
    src.write_text(
        "\n".join(
            [
                ">query",
                "ABCDEFGH",
                ">dup_query",
                "ABCDEFGH",
                ">low_cov",
                "ABCD----",
                ">good_hit_1",
                "ABcCDEFGH",
                ">good_hit_2",
                "ABCDEFG-",
                ">good_hit_3",
                "ABCDeFGH",
                "",
            ]
        ),
        encoding="utf-8",
    )

    _write_sanitized_a3m(
        src,
        dst,
        query_sequence="ABCDEFGH",
        max_rows=3,
        min_residue_coverage_fraction=0.75,
    )

    assert dst.read_text(encoding="utf-8").splitlines() == [
        ">query",
        "ABCDEFGH",
        ">good_hit_1",
        "ABcCDEFGH",
        ">good_hit_2",
        "ABCDEFG-",
    ]


def test_prepare_with_local_msa_keeps_separate_profiles_for_binder_and_target_roles(
    tmp_path: Path,
    monkeypatch,
) -> None:
    raw_query_a = tmp_path / "raw_query_a.a3m"
    raw_query_b = tmp_path / "raw_query_b.a3m"
    raw_query_a.write_text(
        "\n".join(
                [
                    ">query",
                    "ABCDEFGH",
                    ">hit1",
                    "ABcCDEFGH",
                    ">hit2",
                    "ABCdDEFGH",
                    "",
                ]
            ),
        encoding="utf-8",
    )
    raw_query_b.write_text(
        "\n".join(
            [
                ">query",
                "QRSTUVWX",
                ">hit1",
                "QRsTUVWX",
                "",
            ]
        ),
        encoding="utf-8",
    )

    def _fake_run_batch_msa(**kwargs):
        sequences = kwargs["sequences"]
        names = {item["sequence"]: item["name"] for item in sequences}
        return {
            "sequences": [
                {"name": names["ABCDEFGH"], "success": True, "msa_path": str(raw_query_a)},
                {"name": names["QRSTUVWX"], "success": True, "msa_path": str(raw_query_b)},
            ]
        }

    monkeypatch.setattr(prepare_protenix_msa, "run_batch_msa", _fake_run_batch_msa)

    payload = [
        {
            "name": "task-1",
            "sequences": [
                {"proteinChain": {"id": ["E"], "sequence": "ABCDEFGH", "count": 1}},
                {"proteinChain": {"id": ["A"], "sequence": "ABCDEFGH", "count": 1}},
                {"proteinChain": {"id": ["B"], "sequence": "QRSTUVWX", "count": 1}},
            ],
        }
    ]
    output_json = tmp_path / "prepared.json"

    prepare_with_local_msa(
        payload=payload,
        output_json=output_json,
        work_dir=tmp_path / "msa_work",
        db_path="/tmp/fake_db",
        cache_dir=str(tmp_path / "cache"),
        threads=2,
        preset="fast",
        cpu_only=False,
        gpu_mode="off",
        gpu_threshold=80,
        preferred_gpus=None,
        excluded_gpus=None,
        gpu_server_mode="off",
        gpu_server_wait_timeout=1,
        gpu_server_db_load_mode=0,
        gpu_server_startup_wait=0.1,
        allow_cpu_fallback=False,
        local_msa_timeout_seconds=30,
        cache_only=False,
        binder_chain_ids=["E"],
        binder_max_unpaired_rows=2,
        binder_min_residue_coverage_fraction=0.8,
    )

    prepared = json.loads(output_json.read_text(encoding="utf-8"))
    binder_chain = prepared[0]["sequences"][0]["proteinChain"]
    target_chain = prepared[0]["sequences"][1]["proteinChain"]

    binder_lines = Path(binder_chain["unpairedMsaPath"]).read_text(encoding="utf-8").splitlines()
    target_lines = Path(target_chain["unpairedMsaPath"]).read_text(encoding="utf-8").splitlines()

    assert binder_chain["unpairedMsaPath"] != target_chain["unpairedMsaPath"]
    assert binder_lines == [
        ">query",
        "ABCDEFGH",
        ">hit1",
        "ABcCDEFGH",
    ]
    assert target_lines == [
        ">query",
        "ABCDEFGH",
        ">hit1",
        "ABcCDEFGH",
        ">hit2",
        "ABCdDEFGH",
    ]


def test_prepare_with_local_msa_passes_threads_to_true_batch_msa(tmp_path: Path, monkeypatch) -> None:
    raw_query = tmp_path / "raw_query.a3m"
    raw_query.write_text(
        "\n".join(
            [
                ">query",
                "ABCDEFGH",
                ">hit1",
                "ABcCDEFGH",
                "",
            ]
        ),
        encoding="utf-8",
    )
    captured = {}

    def _fake_run_batch_msa(**kwargs):
        captured.update(kwargs)
        return {
            "sequences": [
                {"name": item["name"], "success": True, "msa_path": str(raw_query)}
                for item in kwargs["sequences"]
            ]
        }

    monkeypatch.setattr(prepare_protenix_msa, "run_batch_msa", _fake_run_batch_msa)

    payload = [
        {
            "name": "task-1",
            "sequences": [
                {"proteinChain": {"id": ["A"], "sequence": "ABCDEFGH", "count": 1}},
                {"proteinChain": {"id": ["B"], "sequence": "QRSTUVWX", "count": 1}},
            ],
        }
    ]

    prepare_with_local_msa(
        payload=payload,
        output_json=tmp_path / "prepared.json",
        work_dir=tmp_path / "msa_work",
        db_path="/tmp/fake_db",
        cache_dir=str(tmp_path / "cache"),
        threads=9,
        preset="fast",
        cpu_only=False,
        gpu_mode="off",
        gpu_threshold=80,
        preferred_gpus=None,
        excluded_gpus=None,
        gpu_server_mode="off",
        gpu_server_wait_timeout=1,
        gpu_server_db_load_mode=0,
        gpu_server_startup_wait=0.1,
        allow_cpu_fallback=False,
        local_msa_timeout_seconds=30,
        cache_only=False,
    )

    assert captured["threads"] == 9


def test_write_msa_report_records_local_runtime_contract(tmp_path: Path) -> None:
    report_path = tmp_path / "msa_report.json"
    payload = [
        {
            "name": "task-1",
            "sequences": [
                {
                    "proteinChain": {
                        "id": ["A"],
                        "sequence": "ABCDEFGH",
                        "count": 1,
                    }
                }
            ],
        }
    ]

    write_msa_report(
        report_path,
        payload,
        "local",
        {"tasks": 1, "protein_chains": 1, "unique_sequences": 1, "total_residues": 8},
        local_msa_runtime_contract={
            "requested_gpu_server_mode": "persistent",
            "effective_gpu_server_mode": "off",
            "selected_gpu_id": 2,
        },
    )

    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert report["local_msa_runtime_contract"] == {
        "requested_gpu_server_mode": "persistent",
        "effective_gpu_server_mode": "off",
        "selected_gpu_id": 2,
    }
