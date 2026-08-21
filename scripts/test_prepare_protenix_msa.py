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


def _write_cache_a3m(cache_root: Path, sequence: str, text: str) -> Path:
    full_hash = prepare_protenix_msa.hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    dest = cache_root / full_hash[:2] / f"{full_hash}.a3m.gz"
    dest.parent.mkdir(parents=True, exist_ok=True)
    import gzip

    with gzip.open(dest, "wt", encoding="utf-8") as handle:
        handle.write(text)
    return dest


def _single_chain_payload(sequence: str) -> list[dict]:
    return [
        {
            "name": "task-1",
            "sequences": [{"proteinChain": {"id": ["A"], "sequence": sequence, "count": 1}}],
        }
    ]


def test_hydrate_chains_from_shared_cache_materializes_cached_a3m(tmp_path: Path) -> None:
    sequence = "ABCDEFGH"
    _write_cache_a3m(tmp_path / "cache", sequence, ">query\nABCDEFGH\n>hit1\nAB-DEFGH\n")
    payload = _single_chain_payload(sequence)

    hydrated = prepare_protenix_msa.hydrate_chains_from_shared_cache(
        payload, tmp_path / "out", str(tmp_path / "cache")
    )

    assert hydrated == 1
    chain = payload[0]["sequences"][0]["proteinChain"]
    non_pairing = prepare_protenix_msa.Path(chain["unpairedMsaPath"])
    pairing = prepare_protenix_msa.Path(chain["pairedMsaPath"])
    assert non_pairing.exists() and pairing.exists()
    assert non_pairing.read_text(encoding="utf-8").splitlines() == [
        ">query",
        "ABCDEFGH",
        ">hit1",
        "AB-DEFGH",
    ]
    assert pairing.read_text(encoding="utf-8") == f">query\n{sequence}\n"


def test_full_cache_hit_short_circuits_colabfold_api_backend(tmp_path: Path, monkeypatch) -> None:
    sequence = "ABCDEFGH"
    cache_root = tmp_path / "cache"
    _write_cache_a3m(cache_root, sequence, ">query\nABCDEFGH\n>hit1\nABcDEFGH\n")
    input_json = tmp_path / "input.json"
    output_json = tmp_path / "output.json"
    report_json = tmp_path / "msa_report.json"
    input_json.write_text(json.dumps(_single_chain_payload(sequence)), encoding="utf-8")

    def _explode(*args, **kwargs):
        raise AssertionError("colabfold API must not be called when the shared cache covers every chain")

    monkeypatch.setattr(prepare_protenix_msa, "prepare_with_colabfold_api", _explode)
    monkeypatch.setattr(
        "sys.argv",
        [
            "prepare_protenix_msa.py",
            "--input_json",
            str(input_json),
            "--output_json",
            str(output_json),
            "--out_dir",
            str(tmp_path / "out"),
            "--backend",
            "colabfold_api",
            "--cache-dir",
            str(cache_root),
            "--report_json",
            str(report_json),
        ],
    )

    prepare_protenix_msa.main()

    prepared = json.loads(output_json.read_text(encoding="utf-8"))
    chain = prepared[0]["sequences"][0]["proteinChain"]
    assert chain["unpairedMsaPath"].endswith("non_pairing.a3m")
    assert json.loads(report_json.read_text(encoding="utf-8"))["backend"] == "cache"


def test_cache_colabfold_results_persists_api_a3m_and_never_overwrites(tmp_path: Path) -> None:
    sequence = "ABCDEFGH"
    cache_root = tmp_path / "cache"
    fetched = tmp_path / "fetched_non_pairing.a3m"
    fetched.write_text(">query\nABCDEFGH\n>hit1\nABcDEFGH\n", encoding="utf-8")
    payload = _single_chain_payload(sequence)
    payload[0]["sequences"][0]["proteinChain"]["unpairedMsaPath"] = str(fetched)

    persisted = prepare_protenix_msa.cache_colabfold_results(payload, str(cache_root))

    assert persisted == 1
    full_hash = prepare_protenix_msa.hashlib.sha256(sequence.encode("utf-8")).hexdigest()
    dest = cache_root / full_hash[:2] / f"{full_hash}.a3m.gz"
    import gzip

    with gzip.open(dest, "rt", encoding="utf-8") as handle:
        assert handle.read() == ">query\nABCDEFGH\n>hit1\nABcDEFGH\n"
    dest.write_text("PROTECTED", encoding="utf-8")  # simulate a concurrent/older write
    assert prepare_protenix_msa.cache_colabfold_results(payload, str(cache_root)) == 0
    assert dest.read_text(encoding="utf-8") == "PROTECTED"
