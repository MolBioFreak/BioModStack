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
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
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
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="fast",
            cpu_only=True,
        )

    assert search_calls, "expected the UniRef search step to run"
    assert search_calls[0][2] == str(db_dir / "uniref30_2302_db")


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
        "--allow-degraded-quality",
    ])

    assert exit_code == 0
    assert captured["target_shard_mode"] == "required"
    assert captured["target_shards"] == 2
    assert captured["target_shard_min_size_gb"] == 0
    assert captured["allow_degraded_quality"] is True



def test_run_colabfold_msa_workflow_reuses_runtime_preferred_gpus_for_envdb_reclaim(
    monkeypatch,
    tmp_path: Path,
) -> None:
    sentinel = RuntimeError("envdb reclaim reached")

    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _prepare_gpu_target_db(db_dir, "uniref30_2302_db")
    envdb_gpu_prefix = _prepare_gpu_target_db(db_dir, "colabfold_envdb_202108_db")

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
    def fake_run_mmseqs(mmseqs_bin, args, env):
        if args and args[0] == "search":
            # GPU-padded search now must leave a real alignment-result DB so the
            # remap precondition is exercised before the test reaches EnvDB reclaim.
            _write_mmseqs_result_db(Path(args[3]), [(0, b"0\t99\t1\t1\n\x00")])

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    def fake_reclaim_conflicting_gpuserver_instances(*, target_db, preferred_gpus, cache_dir):
        assert target_db == envdb_gpu_prefix
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



def _prepare_full_colabfold_db(db_dir: Path) -> None:
    db_dir.mkdir(parents=True, exist_ok=True)
    for stem in ("uniref30_2302_db", "colabfold_envdb_202108_db"):
        (db_dir / stem).write_text("", encoding="utf-8")
        (db_dir / f"{stem}.dbtype").write_text("", encoding="utf-8")
        (db_dir / f"{stem}.index").write_text("0\t0\t1\n1\t1\t1\n", encoding="utf-8")
        (db_dir / f"{stem}_seq").write_text("sequence-payload", encoding="utf-8")
        (db_dir / f"{stem}_seq.dbtype").write_text("", encoding="utf-8")
        (db_dir / f"{stem}_seq.index").write_text("0\t0\t1\n1\t1\t1\n2\t2\t1\n", encoding="utf-8")
        (db_dir / f"{stem}_aln").write_text("alignment-payload", encoding="utf-8")
        (db_dir / f"{stem}_aln.dbtype").write_text("", encoding="utf-8")
        (db_dir / f"{stem}_aln.index").write_text("0\t0\t1\n1\t1\t1\n", encoding="utf-8")


def _prepare_gpu_target_db(db_dir: Path, stem: str) -> Path:
    gpu_prefix = db_dir / f"{stem}_gpu"
    gpu_prefix.write_text("gpu-padded-payload", encoding="utf-8")
    Path(str(gpu_prefix) + ".dbtype").write_text("", encoding="utf-8")
    Path(str(gpu_prefix) + ".index").write_text("0\t0\t1\n1\t1\t1\n", encoding="utf-8")
    (db_dir / f"{stem}_gpu_h").write_text("gpu-header-payload", encoding="utf-8")
    (db_dir / f"{stem}_gpu_h.dbtype").write_text("", encoding="utf-8")
    (db_dir / f"{stem}_gpu_h.index").write_text("0\t0\t1\n1\t1\t1\n", encoding="utf-8")
    Path(str(gpu_prefix) + ".lookup").write_text("0\tgpu-hit-0\t0\n1\tgpu-hit-1\t1\n", encoding="utf-8")
    return gpu_prefix


def _install_no_cache_cpu_runtime(monkeypatch) -> None:
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
            "isolated_task_context": False,
            "effective_preferred_gpus": [],
        },
    )


def _touch_mmseqs_db(path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("", encoding="utf-8")
    Path(str(path) + ".dbtype").write_text("", encoding="utf-8")
    Path(str(path) + ".index").write_text("", encoding="utf-8")


def _write_mmseqs_result_db(prefix: Path, records: list[tuple[int, bytes]]) -> None:
    prefix.parent.mkdir(parents=True, exist_ok=True)
    payload = bytearray()
    index_rows = []
    for query_key, record in records:
        offset = len(payload)
        payload.extend(record)
        index_rows.append(f"{query_key}\t{offset}\t{len(record)}\n")
    prefix.write_bytes(bytes(payload))
    Path(str(prefix) + ".index").write_text("".join(index_rows), encoding="utf-8")
    Path(str(prefix) + ".dbtype").write_bytes((5).to_bytes(4, "little"))


def _read_mmseqs_records(prefix: Path) -> list[bytes]:
    data = prefix.read_bytes()
    records = []
    for row in Path(str(prefix) + ".index").read_text(encoding="utf-8").splitlines():
        _key, offset, length = row.split("\t")[:3]
        records.append(data[int(offset): int(offset) + int(length)])
    return records


def test_remap_gpu_padded_result_db_target_keys_uses_lookup_filenumber(tmp_path: Path) -> None:
    source = tmp_path / "res_gpu"
    output = tmp_path / "res_logical"
    gpu_target = tmp_path / "uniref30_2302_db_gpu"
    _write_mmseqs_result_db(
        source,
        [
            (
                0,
                b"7\t187\t0.885\t1e-49\t0\t10\t20\t0\t10\t11\t11M\n"
                b"12345\t42\t0.500\t2e-10\t1\t9\t20\t0\t8\t9\t9M\n\x00",
            ),
            (3, b"9\t30\t0.250\t3e-5\t2\t8\t20\t0\t6\t7\t7M\n\x00"),
        ],
    )
    Path(str(gpu_target) + ".lookup").write_text(
        "7\tUniRef100_A\t70\n12345\tUniRef100_B\t8\n9\tUniRef100_C\t900000\n",
        encoding="utf-8",
    )

    report = run_local_msa.remap_mmseqs_result_target_keys_from_gpu_lookup(
        result_db=source,
        gpu_target_db=gpu_target,
        output_db=output,
        stage="unit_test",
    )

    assert report["stage"] == "unit_test"
    assert report["result_records"] == 2
    assert report["target_hits"] == 3
    assert report["remapped_hits"] == 3
    assert report["output_db"] == str(output)
    assert Path(str(output) + ".dbtype").read_bytes() == Path(str(source) + ".dbtype").read_bytes()
    assert _read_mmseqs_records(output) == [
        b"70\t187\t0.885\t1e-49\t0\t10\t20\t0\t10\t11\t11M\n"
        b"8\t42\t0.500\t2e-10\t1\t9\t20\t0\t8\t9\t9M\n\x00",
        b"900000\t30\t0.250\t3e-5\t2\t8\t20\t0\t6\t7\t7M\n\x00",
    ]


def test_remap_gpu_padded_result_db_target_keys_fails_on_missing_lookup_key(tmp_path: Path) -> None:
    source = tmp_path / "res_gpu"
    output = tmp_path / "res_logical"
    gpu_target = tmp_path / "uniref30_2302_db_gpu"
    _write_mmseqs_result_db(source, [(0, b"7\t187\t0.885\t1e-49\t0\t10\t20\t0\t10\t11\t11M\n\x00")])
    Path(str(gpu_target) + ".lookup").write_text("8\tUniRef100_other\t80\n", encoding="utf-8")

    with pytest.raises(RuntimeError, match="missing 1 target key"):
        run_local_msa.remap_mmseqs_result_target_keys_from_gpu_lookup(
            result_db=source,
            gpu_target_db=gpu_target,
            output_db=output,
        )


def _fake_successful_mmseqs_until_unpack(commands: list[list[str]]):
    sentinel = RuntimeError("stop at unpackdb")

    def fake_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        commands.append(args)
        op = args[0]
        if op == "createdb":
            _touch_mmseqs_db(args[2])
        elif op == "search":
            _touch_mmseqs_db(args[3])
            latest = Path(args[4]) / "latest"
            latest.mkdir(parents=True, exist_ok=True)
            # Create both profile_1 and profile_3 so this catches code that
            # incorrectly prefers the final iteration profile for EnvDB align.
            for iteration in (1, 3):
                _touch_mmseqs_db(latest / f"profile_{iteration}")
        elif op == "mvdb":
            _touch_mmseqs_db(args[2])
        elif op == "lndb":
            _touch_mmseqs_db(args[2])
        elif op == "expandaln":
            _touch_mmseqs_db(args[5])
        elif op == "align":
            _touch_mmseqs_db(args[4])
        elif op == "filterresult":
            _touch_mmseqs_db(args[4])
        elif op == "result2profile":
            _touch_mmseqs_db(args[4])
        elif op == "result2msa":
            _touch_mmseqs_db(args[4])
        elif op == "mergedbs":
            _touch_mmseqs_db(args[2])
        elif op == "unpackdb":
            raise sentinel

    return fake_run_mmseqs, sentinel


def test_high_quality_local_commands_match_official_colabfold_semantics(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _install_no_cache_cpu_runtime(monkeypatch)
    commands: list[list[str]] = []
    fake_run_mmseqs, sentinel = _fake_successful_mmseqs_until_unpack(commands)
    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    with pytest.raises(RuntimeError, match=str(sentinel)):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="official_semantics",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="maximum",
            cpu_only=True,
            target_shard_mode="off",
        )

    search_calls = [cmd for cmd in commands if cmd[0] == "search"]
    assert search_calls, "expected UniRef and EnvDB search commands"
    for cmd in search_calls:
        assert "-s" in cmd
        assert cmd[cmd.index("-s") + 1] == "8.0"

    filter_calls = [cmd for cmd in commands if cmd[0] == "filterresult"]
    assert filter_calls, "expected high-quality filterresult commands"
    for cmd in filter_calls:
        assert cmd[cmd.index("--qsc") + 1] == "0.8"
        assert cmd[cmd.index("--max-seq-id") + 1] == "1.0"

    result2msa_calls = [cmd for cmd in commands if cmd[0] == "result2msa"]
    assert result2msa_calls, "expected result2msa commands"
    for cmd in result2msa_calls:
        assert cmd[cmd.index("--msa-format-mode") + 1] == "6"

    env_align_calls = [
        cmd
        for cmd in commands
        if cmd[0] == "align" and "colabfold_envdb_202108_db_seq" in cmd[2]
    ]
    assert env_align_calls, "expected EnvDB realignment command"
    assert env_align_calls[0][1].endswith("tmp_env/latest/profile_1")


def test_gpu_server_search_includes_effective_sensitivity(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
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
    sentinel = RuntimeError("stop after gpuserver search")
    search_calls = []

    def fake_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        if args[0] == "search":
            search_calls.append(args)
            raise sentinel

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    with pytest.raises(RuntimeError, match="stop after gpuserver search"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="gpu_semantics",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="maximum",
            preferred_gpus=[1],
            use_gpu=True,
        )

    assert search_calls, "expected gpuserver-backed search"
    assert "-s" in search_calls[0]
    assert search_calls[0][search_calls[0].index("-s") + 1] == "8.0"


def test_maximum_expansion_failure_is_fatal_by_default(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _install_no_cache_cpu_runtime(monkeypatch)

    def fake_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        if args[0] == "createdb":
            _touch_mmseqs_db(args[2])
        elif args[0] == "search":
            _touch_mmseqs_db(args[3])
            latest = Path(args[4]) / "latest"
            latest.mkdir(parents=True, exist_ok=True)
            _touch_mmseqs_db(latest / "profile_1")
        elif args[0] == "mvdb":
            _touch_mmseqs_db(args[2])
        elif args[0] == "lndb":
            _touch_mmseqs_db(args[2])
        elif args[0] == "expandaln":
            raise RuntimeError("Invalid alignment result record")

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    with pytest.raises(RuntimeError, match="Alignment expansion failed"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="fatal_expansion",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="maximum",
            cpu_only=True,
            target_shard_mode="off",
        )


def test_maximum_missing_alignment_db_is_fatal_by_default(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    for suffix in ("", ".dbtype", ".index"):
        (db_dir / f"uniref30_2302_db_aln{suffix}").unlink()
    _install_no_cache_cpu_runtime(monkeypatch)

    def fake_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        if args[0] == "createdb":
            _touch_mmseqs_db(args[2])
        elif args[0] == "search":
            _touch_mmseqs_db(args[3])
            latest = Path(args[4]) / "latest"
            latest.mkdir(parents=True, exist_ok=True)
            _touch_mmseqs_db(latest / "profile_1")
        elif args[0] == "mvdb":
            _touch_mmseqs_db(args[2])
        elif args[0] == "lndb":
            _touch_mmseqs_db(args[2])

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    with pytest.raises(RuntimeError, match="UniRef alignment DB prefix is missing"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="missing_aln",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="maximum",
            cpu_only=True,
            target_shard_mode="off",
        )


def test_high_quality_missing_envdb_is_fatal_by_default(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    for suffix in ("", ".dbtype", ".index"):
        path = db_dir / f"colabfold_envdb_202108_db{suffix}"
        if path.exists():
            path.unlink()
    _install_no_cache_cpu_runtime(monkeypatch)
    monkeypatch.setattr(
        run_local_msa,
        "run_mmseqs",
        lambda *args, **kwargs: pytest.fail("high-quality run should fail before MMseqs when EnvDB is missing"),
    )

    with pytest.raises(RuntimeError, match="Environmental DB prefix is missing"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="missing_envdb",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="balanced",
            cpu_only=True,
        )


def test_high_quality_missing_envdb_sequence_db_is_fatal_before_mmseqs(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    for suffix in ("", ".dbtype", ".index"):
        path = db_dir / f"colabfold_envdb_202108_db_seq{suffix}"
        if path.exists():
            path.unlink()
    _install_no_cache_cpu_runtime(monkeypatch)
    monkeypatch.setattr(
        run_local_msa,
        "run_mmseqs",
        lambda *args, **kwargs: pytest.fail("high-quality run should fail before MMseqs when EnvDB _seq is missing"),
    )

    with pytest.raises(RuntimeError, match="EnvDB sequence DB prefix is missing"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="missing_envdb_seq",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="balanced",
            cpu_only=True,
        )


def test_quality_report_records_local_db_integrity_preflight(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _install_no_cache_cpu_runtime(monkeypatch)

    def fake_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        op = args[0]
        if op == "createdb":
            _touch_mmseqs_db(args[2])
        elif op == "search":
            _touch_mmseqs_db(args[3])
            latest = Path(args[4]) / "latest"
            latest.mkdir(parents=True, exist_ok=True)
            _touch_mmseqs_db(latest / "profile_1")
        elif op == "mvdb":
            _touch_mmseqs_db(args[2])
        elif op == "lndb":
            _touch_mmseqs_db(args[2])
        elif op == "filterresult":
            _touch_mmseqs_db(args[4])
        elif op == "result2profile":
            _touch_mmseqs_db(args[4])
        elif op == "result2msa":
            _touch_mmseqs_db(args[4])
        elif op == "mergedbs":
            _touch_mmseqs_db(args[2])
        elif op == "unpackdb":
            unpack_dir = Path(args[2])
            unpack_dir.mkdir(parents=True, exist_ok=True)
            (unpack_dir / "0.a3m").write_text(">query\nACDEFGHIK\n>hit1\nACDEFGHIK\n", encoding="utf-8")

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    run_local_msa.run_colabfold_msa_workflow(
        sequence="ACDEFGHIK",
        job_name="reported_integrity",
        out_dir=str(tmp_path / "out"),
        db_path=str(db_dir),
        cache_dir=str(tmp_path / "cache"),
        preset="balanced",
        cpu_only=True,
        target_shard_mode="off",
    )

    report = run_local_msa.json.loads((tmp_path / "out" / "reported_integrity_msa_quality.json").read_text())
    assert report["db_integrity"]["checked"] is True
    assert report["db_integrity"]["compatible"] is True
    assert report["db_integrity"]["required_families"] == ["uniref30_2302_db", "colabfold_envdb_202108_db"]
    assert report["db_integrity"]["families"]["colabfold_envdb_202108_db"]["sequence_db_ready"] is True
    assert report["db_integrity"]["families"]["colabfold_envdb_202108_db"]["alignment_keyspace_compatible"] is None


def _install_no_cache_gpu_runtime(monkeypatch, gpu_bin: Path, cpu_bin: Path) -> None:
    monkeypatch.setattr(run_local_msa, "acquire_msa_lock", lambda path: object())
    monkeypatch.setattr(run_local_msa, "release_msa_lock", lambda fd: None)
    monkeypatch.setattr(run_local_msa, "check_cache", lambda **kwargs: None)
    monkeypatch.setattr(run_local_msa, "touch_gpuserver_query_activity", lambda **kwargs: None)
    monkeypatch.setattr(
        run_local_msa,
        "resolve_mmseqs_binaries",
        lambda db_path: (cpu_bin, gpu_bin),
    )
    monkeypatch.setattr(
        run_local_msa,
        "inspect_mmseqs_runtime",
        lambda **kwargs: {
            "status": "gpu_ready",
            "mmseqs_bin": str(gpu_bin),
            "summary_message": "Using GPU mmseqs on device 1",
            "normalized_gpu_mode": "required",
            "normalized_gpu_server_mode": kwargs.get("gpu_server_mode") or "off",
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


def test_resolve_mmseqs_gpu_target_db_requires_padded_prefix_not_marker(tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    (db_dir / "colabfold_envdb_202108_db.GPU_READY").write_text("", encoding="utf-8")

    assert run_local_msa.resolve_mmseqs_gpu_target_db(db_dir / "colabfold_envdb_202108_db") is None

    gpu_prefix = _prepare_gpu_target_db(db_dir, "colabfold_envdb_202108_db")
    assert run_local_msa.resolve_mmseqs_gpu_target_db(db_dir / "colabfold_envdb_202108_db") == gpu_prefix
def test_envdb_target_split_uses_gpu_padded_target_binary_and_flags_when_gpu_requested(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _prepare_gpu_target_db(db_dir, "uniref30_2302_db")
    envdb_gpu_prefix = _prepare_gpu_target_db(db_dir, "colabfold_envdb_202108_db")
    gpu_bin = tmp_path / "mmseqs-gpu-blackwell" / "bin" / "mmseqs"
    cpu_bin = tmp_path / "mmseqs-cpu" / "bin" / "mmseqs"
    gpu_bin.parent.mkdir(parents=True)
    cpu_bin.parent.mkdir(parents=True)
    gpu_bin.write_text("", encoding="utf-8")
    cpu_bin.write_text("", encoding="utf-8")
    _install_no_cache_gpu_runtime(monkeypatch, gpu_bin, cpu_bin)

    commands: list[tuple[str, list[str]]] = []
    fake_run_mmseqs, sentinel = _fake_successful_mmseqs_until_unpack([])

    def recording_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        commands.append((str(mmseqs_bin), args))
        return fake_run_mmseqs(mmseqs_bin, args, env)

    monkeypatch.setattr(run_local_msa, "run_mmseqs", recording_run_mmseqs)

    with pytest.raises(RuntimeError, match=str(sentinel)):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="gpu_native_split",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="balanced",
            use_gpu=True,
            gpu_server_mode="off",
            target_shard_mode="required",
            target_shards=4,
            target_shard_min_size_gb=0,
        )

    envdb_searches = [
        (binary, cmd)
        for binary, cmd in commands
        if cmd[0] == "search" and cmd[2] == str(envdb_gpu_prefix)
    ]
    assert envdb_searches, "expected EnvDB search command"
    envdb_binary, envdb_cmd = envdb_searches[-1]
    assert envdb_binary == str(gpu_bin)
    assert "--gpu" in envdb_cmd
    assert envdb_cmd[envdb_cmd.index("--gpu") + 1] == "1"
    assert "--prefilter-mode" in envdb_cmd
    assert envdb_cmd[envdb_cmd.index("--prefilter-mode") + 1] == "1"
    assert envdb_cmd[envdb_cmd.index("--split") + 1] == "4"
    assert envdb_cmd[envdb_cmd.index("--split-mode") + 1] == "0"
    assert envdb_cmd[envdb_cmd.index("--threads") + 1] == "32"


def test_gpu_padded_search_rebuilds_profiles_against_logical_targets(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _prepare_gpu_target_db(db_dir, "uniref30_2302_db")
    _prepare_gpu_target_db(db_dir, "colabfold_envdb_202108_db")
    gpu_bin = tmp_path / "mmseqs-gpu-blackwell" / "bin" / "mmseqs"
    cpu_bin = tmp_path / "mmseqs-cpu" / "bin" / "mmseqs"
    gpu_bin.parent.mkdir(parents=True)
    cpu_bin.parent.mkdir(parents=True)
    gpu_bin.write_text("", encoding="utf-8")
    cpu_bin.write_text("", encoding="utf-8")
    _install_no_cache_gpu_runtime(monkeypatch, gpu_bin, cpu_bin)

    commands: list[list[str]] = []
    remap_calls: list[dict[str, str]] = []
    fake_run_mmseqs, sentinel = _fake_successful_mmseqs_until_unpack([])

    def fake_remap_gpu_result(**kwargs):
        output_db = Path(kwargs["output_db"])
        remap_calls.append({key: str(value) for key, value in kwargs.items()})
        _touch_mmseqs_db(output_db)
        return {
            "stage": kwargs.get("stage"),
            "result_db": str(kwargs["result_db"]),
            "gpu_target_db": str(kwargs["gpu_target_db"]),
            "output_db": str(output_db),
            "result_records": 1,
            "target_hits": 1,
            "remapped_hits": 1,
        }

    def recording_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        commands.append(args)
        return fake_run_mmseqs(mmseqs_bin, args, env)

    monkeypatch.setattr(run_local_msa, "run_mmseqs", recording_run_mmseqs)
    monkeypatch.setattr(run_local_msa, "remap_mmseqs_result_target_keys_from_gpu_lookup", fake_remap_gpu_result, raising=False)

    with pytest.raises(RuntimeError, match=str(sentinel)):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="gpu_profile_rebuild",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="maximum",
            use_gpu=True,
            gpu_server_mode="off",
            target_shard_mode="required",
            target_shards=4,
            target_shard_min_size_gb=0,
        )

    logical_uniref = str(db_dir / "uniref30_2302_db")
    logical_envdb = str(db_dir / "colabfold_envdb_202108_db")
    result2profile_calls = [cmd for cmd in commands if cmd[0] == "result2profile"]

    assert any(cmd[2] == logical_uniref for cmd in result2profile_calls)
    assert any(cmd[2] == logical_envdb for cmd in result2profile_calls)
    assert [call["stage"] for call in remap_calls] == ["uniref_search", "envdb_search"]
    uniref_remap_output = remap_calls[0]["output_db"]
    envdb_remap_output = remap_calls[1]["output_db"]
    assert any(cmd[2] == logical_uniref and cmd[3] == uniref_remap_output for cmd in result2profile_calls)
    assert any(cmd[2] == logical_envdb and cmd[3] == envdb_remap_output for cmd in result2profile_calls)
    assert any(cmd[0] == "expandaln" and cmd[3] == uniref_remap_output for cmd in commands)
    assert any(cmd[0] == "expandaln" and cmd[3] == envdb_remap_output for cmd in commands)
    assert not any(cmd[0] == "mvdb" and "/tmp/latest/profile_" in cmd[1] for cmd in commands)

    env_align_calls = [
        cmd
        for cmd in commands
        if cmd[0] == "align" and cmd[2] == str(db_dir / "colabfold_envdb_202108_db_seq")
    ]
    assert env_align_calls
    assert env_align_calls[0][1].endswith("prof_env_res")


def test_gpu_required_envdb_target_split_does_not_cpu_fallback(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _prepare_gpu_target_db(db_dir, "uniref30_2302_db")
    envdb_gpu_prefix = _prepare_gpu_target_db(db_dir, "colabfold_envdb_202108_db")
    gpu_bin = tmp_path / "mmseqs-gpu-blackwell" / "bin" / "mmseqs"
    cpu_bin = tmp_path / "mmseqs-cpu" / "bin" / "mmseqs"
    gpu_bin.parent.mkdir(parents=True)
    cpu_bin.parent.mkdir(parents=True)
    gpu_bin.write_text("", encoding="utf-8")
    cpu_bin.write_text("", encoding="utf-8")
    _install_no_cache_gpu_runtime(monkeypatch, gpu_bin, cpu_bin)

    cpu_envdb_attempts: list[list[str]] = []

    def fake_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        op = args[0]
        if op == "createdb":
            _touch_mmseqs_db(args[2])
        elif op == "search":
            if args[2] == str(envdb_gpu_prefix):
                if str(mmseqs_bin) == str(gpu_bin):
                    raise RuntimeError("Database colabfold_envdb_202108_db_gpu is not a valid GPU database")
                cpu_envdb_attempts.append(args)
            _touch_mmseqs_db(args[3])
            latest = Path(args[4]) / "latest"
            latest.mkdir(parents=True, exist_ok=True)
            _touch_mmseqs_db(latest / "profile_1")
        elif op == "mvdb":
            _touch_mmseqs_db(args[2])
        elif op == "lndb":
            _touch_mmseqs_db(args[2])
        elif op == "filterresult":
            _touch_mmseqs_db(args[4])
        elif op == "result2profile":
            _touch_mmseqs_db(args[4])
        elif op == "result2msa":
            _touch_mmseqs_db(args[4])
        elif op == "mergedbs":
            _touch_mmseqs_db(args[2])
        elif op == "unpackdb":
            unpack_dir = Path(args[2])
            unpack_dir.mkdir(parents=True, exist_ok=True)
            (unpack_dir / "0.a3m").write_text(">query\nACDEFGHIK\n", encoding="utf-8")

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    with pytest.raises(RuntimeError, match="GPU EnvDB native target split required"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="gpu_required_no_fallback",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="balanced",
            use_gpu=True,
            gpu_mode="required",
            gpu_server_mode="off",
            target_shard_mode="required",
            target_shards=4,
            target_shard_min_size_gb=0,
        )

    assert cpu_envdb_attempts == []


def test_gpu_required_missing_padded_targets_fails_before_mmseqs(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    (db_dir / "uniref30_2302_db.GPU_READY").write_text("", encoding="utf-8")
    (db_dir / "colabfold_envdb_202108_db.GPU_READY").write_text("", encoding="utf-8")
    gpu_bin = tmp_path / "mmseqs-gpu-blackwell" / "bin" / "mmseqs"
    cpu_bin = tmp_path / "mmseqs-cpu" / "bin" / "mmseqs"
    gpu_bin.parent.mkdir(parents=True)
    cpu_bin.parent.mkdir(parents=True)
    gpu_bin.write_text("", encoding="utf-8")
    cpu_bin.write_text("", encoding="utf-8")
    _install_no_cache_gpu_runtime(monkeypatch, gpu_bin, cpu_bin)

    commands: list[list[str]] = []

    def recording_run_mmseqs(mmseqs_bin, args, env):
        commands.append([str(part) for part in args])

    monkeypatch.setattr(run_local_msa, "run_mmseqs", recording_run_mmseqs)

    with pytest.raises(RuntimeError, match="GPU MMseqs target DB not prepared"):
        run_local_msa.run_colabfold_msa_workflow(
            sequence="ACDEFGHIK",
            job_name="gpu_missing_padded",
            out_dir=str(tmp_path / "out"),
            db_path=str(db_dir),
            cache_dir=str(tmp_path / "cache"),
            preset="balanced",
            use_gpu=True,
            gpu_mode="required",
            gpu_server_mode="off",
            target_shard_mode="required",
            target_shards=4,
            target_shard_min_size_gb=0,
        )

    assert commands == []


def test_target_split_semantics_marks_gpu_split_as_top_level_not_internal_proof() -> None:
    semantics = run_local_msa.describe_target_split_semantics(
        {
            "stage": "envdb_search",
            "module": "search",
            "uses_gpu_flag": True,
            "uses_gpu_server": False,
            "split_count": 4,
            "split_mode": 0,
        }
    )

    assert semantics["requested"] is True
    assert semantics["scope"] == "top_level_mmseqs_search_argv"
    assert semantics["internal_child_split_proven"] is False
    assert "ungappedprefilter" in semantics["caveat"]


def test_target_split_semantics_for_unsharded_search_is_not_requested() -> None:
    semantics = run_local_msa.describe_target_split_semantics(
        {
            "stage": "envdb_search",
            "module": "search",
            "uses_gpu_flag": True,
            "split_count": None,
        }
    )

    assert semantics == {
        "requested": False,
        "scope": "not_requested",
        "internal_child_split_proven": None,
        "caveat": None,
    }


def test_cpu_target_split_quality_report_records_acceleration_truth(monkeypatch, tmp_path: Path) -> None:
    db_dir = tmp_path / "db"
    _prepare_full_colabfold_db(db_dir)
    _install_no_cache_cpu_runtime(monkeypatch)

    def fake_run_mmseqs(mmseqs_bin, args, env):
        args = [str(arg) for arg in args]
        op = args[0]
        if op == "createdb":
            _touch_mmseqs_db(args[2])
        elif op == "search":
            _touch_mmseqs_db(args[3])
            latest = Path(args[4]) / "latest"
            latest.mkdir(parents=True, exist_ok=True)
            _touch_mmseqs_db(latest / "profile_1")
        elif op == "mvdb":
            _touch_mmseqs_db(args[2])
        elif op == "lndb":
            _touch_mmseqs_db(args[2])
        elif op == "filterresult":
            _touch_mmseqs_db(args[4])
        elif op == "result2profile":
            _touch_mmseqs_db(args[4])
        elif op == "result2msa":
            _touch_mmseqs_db(args[4])
        elif op == "mergedbs":
            _touch_mmseqs_db(args[2])
        elif op == "unpackdb":
            unpack_dir = Path(args[2])
            unpack_dir.mkdir(parents=True, exist_ok=True)
            (unpack_dir / "0.a3m").write_text(">query\nACDEFGHIK\n>hit1\nACDEFGHIK\n", encoding="utf-8")

    monkeypatch.setattr(run_local_msa, "run_mmseqs", fake_run_mmseqs)

    run_local_msa.run_colabfold_msa_workflow(
        sequence="ACDEFGHIK",
        job_name="cpu_native_split_truth",
        out_dir=str(tmp_path / "out"),
        db_path=str(db_dir),
        cache_dir=str(tmp_path / "cache"),
        preset="balanced",
        cpu_only=True,
        target_shard_mode="required",
        target_shards=4,
        target_shard_min_size_gb=0,
    )

    report = run_local_msa.json.loads((tmp_path / "out" / "cpu_native_split_truth_msa_quality.json").read_text())
    assert report["envdb_acceleration"]["backend"] == "cpu_native_split"
    assert report["envdb_acceleration"]["effective_gpu"] is False
    assert report["envdb_acceleration"]["target_split"] is True
    assert report["envdb_acceleration"]["split_semantics"]["requested"] is True
    assert report["envdb_acceleration"]["split_semantics"]["scope"] == "top_level_mmseqs_search_argv"
    assert report["target_sharding"]["implementation_scope"] == "top_level_mmseqs_search_argv"
    assert report["target_sharding"]["internal_split_proven"] is None
    assert report["effective_gpu_stages"] == []
    envdb_stages = [stage for stage in report["mmseqs_stage_reports"] if stage["stage"] == "envdb_search"]
    assert envdb_stages
    assert envdb_stages[-1]["uses_gpu_flag"] is False
    assert envdb_stages[-1]["split_count"] == 4
    assert envdb_stages[-1]["threads"] == 32
    sidecar_path = Path(report["mmseqs_stage_report_path"])
    assert sidecar_path.exists()
    sidecar = run_local_msa.json.loads(sidecar_path.read_text())
    assert sidecar["effective_gpu_stages"] == []
    assert any(stage["stage"] == "envdb_search" for stage in sidecar["stages"])
