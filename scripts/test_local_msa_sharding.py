from __future__ import annotations

import os
import sys
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parent
LIB_DIR = SCRIPT_DIR / "lib"
if str(LIB_DIR) not in sys.path:
    sys.path.insert(0, str(LIB_DIR))

from local_msa.sharding import (  # noqa: E402
    build_target_shard_plan,
    ensure_target_shards,
    run_native_target_split_search,
    run_sharded_target_search,
)


def test_quality_envdb_auto_plan_shards_32_threads_into_four_workers(tmp_path: Path) -> None:
    target_db = tmp_path / "colabfold_envdb_202108_db"
    target_db.write_bytes(b"x")
    (tmp_path / "colabfold_envdb_202108_db.dbtype").write_text("1", encoding="utf-8")

    plan = build_target_shard_plan(
        mode="auto",
        preset="maximum",
        use_env=True,
        env_available=True,
        target_db=target_db,
        total_threads=32,
        requested_shards=4,
        min_target_size_bytes=0,
    )

    assert plan.enabled is True
    assert plan.mode == "auto"
    assert plan.shard_count == 4
    assert plan.threads_per_worker == 8
    assert plan.total_threads == 32
    assert plan.fallback_allowed is True


def test_auto_plan_does_not_shard_fast_or_missing_envdb(tmp_path: Path) -> None:
    target_db = tmp_path / "colabfold_envdb_202108_db"
    target_db.write_bytes(b"x")
    (tmp_path / "colabfold_envdb_202108_db.dbtype").write_text("1", encoding="utf-8")

    fast_plan = build_target_shard_plan(
        mode="auto",
        preset="fast",
        use_env=True,
        env_available=True,
        target_db=target_db,
        total_threads=32,
        requested_shards=4,
        min_target_size_bytes=0,
    )
    missing_env_plan = build_target_shard_plan(
        mode="auto",
        preset="balanced",
        use_env=True,
        env_available=False,
        target_db=target_db,
        total_threads=32,
        requested_shards=4,
        min_target_size_bytes=0,
    )

    assert fast_plan.enabled is False
    assert "fast" in fast_plan.reason
    assert missing_env_plan.enabled is False
    assert "EnvDB" in missing_env_plan.reason


def test_required_plan_raises_when_thread_budget_cannot_support_requested_shards(tmp_path: Path) -> None:
    target_db = tmp_path / "colabfold_envdb_202108_db"
    target_db.write_bytes(b"x")
    (tmp_path / "colabfold_envdb_202108_db.dbtype").write_text("1", encoding="utf-8")

    try:
        build_target_shard_plan(
            mode="required",
            preset="balanced",
            use_env=True,
            env_available=True,
            target_db=target_db,
            total_threads=2,
            requested_shards=4,
            min_threads_per_worker=1,
            min_target_size_bytes=0,
        )
    except ValueError as exc:
        assert "requested_shards" in str(exc)
    else:
        raise AssertionError("required sharding should reject more shards than available threads")


def test_ensure_target_shards_builds_manifest_and_reuses_valid_split(tmp_path: Path) -> None:
    target_db = tmp_path / "colabfold_envdb_202108_db"
    target_db.write_bytes(b"target")
    (tmp_path / "colabfold_envdb_202108_db.dbtype").write_text("1", encoding="utf-8")
    calls: list[list[str]] = []

    def fake_run_mmseqs(_mmseqs_bin, args, _env):
        calls.append([str(part) for part in args])
        assert args[0] == "splitdb"
        prefix = Path(args[2])
        shard_count = int(args[4])
        for idx in range(shard_count):
            shard = Path(f"{prefix}_{idx}_{shard_count}")
            shard.write_bytes(b"shard")
            Path(str(shard) + ".dbtype").write_text("1", encoding="utf-8")

    first = ensure_target_shards(
        mmseqs_bin="mmseqs",
        target_db=target_db,
        shard_count=4,
        shard_cache_dir=tmp_path / "cache",
        env=os.environ.copy(),
        run_mmseqs=fake_run_mmseqs,
    )
    second = ensure_target_shards(
        mmseqs_bin="mmseqs",
        target_db=target_db,
        shard_count=4,
        shard_cache_dir=tmp_path / "cache",
        env=os.environ.copy(),
        run_mmseqs=fake_run_mmseqs,
    )

    assert len(calls) == 1
    assert first.manifest_path == second.manifest_path
    assert [path.name for path in first.shards] == [
        "target_0_4",
        "target_1_4",
        "target_2_4",
        "target_3_4",
    ]


def test_run_sharded_target_search_searches_each_shard_then_merges(tmp_path: Path) -> None:
    shards = []
    for idx in range(4):
        shard = tmp_path / f"target_{idx}_4"
        shard.write_bytes(b"shard")
        (tmp_path / f"target_{idx}_4.dbtype").write_text("1", encoding="utf-8")
        shards.append(shard)

    calls: list[list[str]] = []

    def fake_run_mmseqs(_mmseqs_bin, args, _env):
        args = [str(part) for part in args]
        calls.append(args)
        if args[0] == "search":
            result_db = Path(args[3])
            result_db.write_bytes(b"result")
            Path(str(result_db) + ".dbtype").write_text("1", encoding="utf-8")
        elif args[0] == "mergedbs":
            merged = Path(args[2])
            merged.write_bytes(b"merged")
            Path(str(merged) + ".dbtype").write_text("1", encoding="utf-8")

    run_sharded_target_search(
        mmseqs_bin="mmseqs",
        query_db=str(tmp_path / "qdb"),
        target_db=str(tmp_path / "target"),
        result_db=str(tmp_path / "merged_result"),
        tmp_dir=str(tmp_path / "tmp"),
        base_search_params=[
            "search",
            str(tmp_path / "qdb"),
            str(tmp_path / "target"),
            str(tmp_path / "merged_result"),
            str(tmp_path / "tmp_env"),
            "--num-iterations",
            "2",
            "-a",
            "-e",
            "0.1",
            "--max-seqs",
            "300",
        ],
        shards=tuple(shards),
        threads_per_worker=8,
        env=os.environ.copy(),
        run_mmseqs=fake_run_mmseqs,
        extra_search_params=["--db-load-mode", "2", "-s", "8.0"],
    )

    search_calls = [call for call in calls if call[0] == "search"]
    merge_calls = [call for call in calls if call[0] == "mergedbs"]
    assert len(search_calls) == 4
    assert all(call[-2:] == ["--threads", "8"] for call in search_calls)
    assert [Path(call[2]).name for call in search_calls] == [path.name for path in shards]
    assert len(merge_calls) == 1
    assert merge_calls[0][2] == str(tmp_path / "merged_result")
    assert merge_calls[0][3:] == [str(tmp_path / f"tmp/shard_{idx}_result") for idx in range(4)]


def test_run_native_target_split_search_uses_one_mmseqs_search_with_native_target_split(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_mmseqs(_mmseqs_bin, args, _env):
        calls.append([str(part) for part in args])

    run_native_target_split_search(
        mmseqs_bin="mmseqs",
        base_search_params=[
            "search",
            str(tmp_path / "qdb"),
            str(tmp_path / "target"),
            str(tmp_path / "res"),
            str(tmp_path / "tmp"),
            "--num-iterations",
            "2",
            "-a",
            "--threads",
            "999",
            "--split",
            "99",
        ],
        split_count=4,
        total_threads=32,
        env=os.environ.copy(),
        run_mmseqs=fake_run_mmseqs,
        extra_search_params=["--db-load-mode", "2", "--split-mode", "1"],
    )

    assert len(calls) == 1
    call = calls[0]
    assert call[:5] == [
        "search",
        str(tmp_path / "qdb"),
        str(tmp_path / "target"),
        str(tmp_path / "res"),
        str(tmp_path / "tmp"),
    ]
    assert call.count("--split") == 1
    assert call[call.index("--split") + 1] == "4"
    assert call.count("--split-mode") == 1
    assert call[call.index("--split-mode") + 1] == "0"
    assert call.count("--threads") == 1
    assert call[call.index("--threads") + 1] == "32"
    assert "--db-load-mode" in call


def test_run_native_target_split_search_can_append_gpu_controls(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def fake_run_mmseqs(_mmseqs_bin, args, _env):
        calls.append([str(part) for part in args])

    run_native_target_split_search(
        mmseqs_bin="mmseqs-gpu",
        base_search_params=[
            "search",
            str(tmp_path / "qdb"),
            str(tmp_path / "envdb"),
            str(tmp_path / "res"),
            str(tmp_path / "tmp_env"),
            "--num-iterations",
            "3",
            "-a",
            "--threads",
            "999",
            "--split",
            "99",
            "--gpu",
            "0",
            "--prefilter-mode",
            "0",
        ],
        split_count=4,
        total_threads=32,
        env=os.environ.copy(),
        run_mmseqs=fake_run_mmseqs,
        extra_search_params=[
            "--db-load-mode",
            "2",
            "--gpu",
            "1",
            "--prefilter-mode",
            "1",
        ],
        split_mode=0,
    )

    assert len(calls) == 1
    call = calls[0]
    assert call.count("--split") == 1
    assert call[call.index("--split") + 1] == "4"
    assert call.count("--split-mode") == 1
    assert call[call.index("--split-mode") + 1] == "0"
    assert call.count("--threads") == 1
    assert call[call.index("--threads") + 1] == "32"
    assert call.count("--gpu") == 1
    assert call[call.index("--gpu") + 1] == "1"
    assert call.count("--prefilter-mode") == 1
    assert call[call.index("--prefilter-mode") + 1] == "1"
    assert call.count("--db-load-mode") == 1
    assert call[call.index("--db-load-mode") + 1] == "2"
