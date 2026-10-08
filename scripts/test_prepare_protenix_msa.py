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
    # Lowercase insertion does not consume a query column.
    _write_cache_a3m(cache_root, sequence, ">query\nABCDEFGH\n>hit1\nABcCDEFGH\n")
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


def test_precomputed_directory_does_not_overwrite_supplied_role(tmp_path):
    supplied = tmp_path / 'supplied.a3m'
    supplied.write_text('>query\nACDE\n>native\nVCDE\n')
    legacy = tmp_path / 'legacy'
    legacy.mkdir()
    (legacy / 'non_pairing.a3m').write_text('>query\nACDE\n')
    chain = {'sequence': 'ACDE', 'unpairedMsaPath': str(supplied),
             'msa': {'precomputed_msa_dir': str(legacy)}}
    prepare_protenix_msa._hydrate_old_precomputed_dir(chain)
    assert chain['unpairedMsaPath'] == str(supplied)
    assert supplied.read_text() == '>query\nACDE\n>native\nVCDE\n'


def test_prepared_complex_batch_cli_binds_native_task_names(tmp_path, monkeypatch):
    import copy
    import shutil
    from biomodstack_msa_handoff import package_alignments, digest
    tasks, chains = [], []
    for task_index, name in enumerate(('one', 'two')):
        task = {'name': name, 'modelSeeds': [7, 19], 'sequences': []}
        for chain_index, (cid, sequence) in enumerate((('B', 'ACDE'), ('A', 'FGHI'))):
            task['sequences'].append({'proteinChain': {'id': [cid], 'count': 2, 'sequence': sequence}})
            source = tmp_path / f'{name}-{cid}.a3m'
            source.write_text(f'>query\n{sequence}\n>{name}-{cid}\n{sequence}\n')
            chains.append(dict(chain_id=f'{task_index}:{chain_index}', role='unpaired', sequence=sequence, source=str(source)))
        task['sequences'].append({'ion': {'ion': 'ZN', 'count': 1}})
        tasks.append(task)
    host = tmp_path / 'host'
    manifest = package_alignments(host, chains=chains, settings={'fixture': True}, provenance={'fixture': True})
    manifest['model_input'] = tasks
    (host / 'msa-inputs.json').write_text(json.dumps(manifest))
    sha = digest((host / 'msa-inputs.json').read_bytes())
    worker = tmp_path / 'worker'
    shutil.copytree(host, worker)
    shutil.rmtree(host)
    payload = copy.deepcopy([tasks[1], tasks[0]])
    payload[0]['modelSeeds'] = [31]
    input_json, output_json = tmp_path / 'input.json', tmp_path / 'out.json'
    input_json.write_text(json.dumps(payload))
    monkeypatch.setattr(prepare_protenix_msa, 'prepare_with_colabfold_api', lambda *a, **k: (_ for _ in ()).throw(AssertionError('worker provider forbidden')))
    monkeypatch.setattr(sys, 'argv', ['prepare_protenix_msa.py', '--input_json', str(input_json),
        '--output_json', str(output_json), '--out_dir', str(tmp_path / 'out'),
        '--prepared-inputs', str(worker), '--prepared-sha256', sha])
    prepare_protenix_msa.main()
    result = json.loads(output_json.read_text())
    assert [t['name'] for t in result] == ['two', 'one']
    assert result[0]['modelSeeds'] == [31]
    for task in result:
        assert task['sequences'][2] == {'ion': {'ion': 'ZN', 'count': 1}}
        for chain_index, cid in enumerate(('B', 'A')):
            chain = task['sequences'][chain_index]['proteinChain']
            assert chain['id'] == [cid] and chain['count'] == 2
            path = Path(chain['unpairedMsaPath'])
            assert path.is_relative_to(worker)
            assert f">{task['name']}-{cid}" in path.read_text()


def test_prepared_batch_rejects_missing_or_ambiguous_task_identity(tmp_path):
    import copy
    import pytest
    from biomodstack_msa_handoff import package_alignments, digest, hydrate_prepared_protenix_task
    source = tmp_path / 'q.a3m'
    source.write_text('>q\nACDE\n')
    task = {'name': 'same', 'sequences': [{'proteinChain': {'sequence': 'ACDE', 'id': ['A'], 'count': 1}}]}
    tasks = [copy.deepcopy(task), copy.deepcopy(task)]
    root = tmp_path / 'prepared'
    manifest = package_alignments(root, chains=[dict(chain_id=f'{i}:0', role='unpaired',
        sequence='ACDE', source=str(source)) for i in range(2)], settings={}, provenance={})
    manifest['model_input'] = tasks
    for name in ('same', 'renamed'):
        (root / 'msa-inputs.json').write_text(json.dumps(manifest))
        sha = digest((root / 'msa-inputs.json').read_bytes())
        payload = [copy.deepcopy(task)]
        payload[0]['name'] = name
        with pytest.raises(ValueError, match='identity is missing, ambiguous'):
            hydrate_prepared_protenix_task(payload, root, sha)
        assert 'unpairedMsaPath' not in payload[0]['sequences'][0]['proteinChain']


def test_native_adapter_preserves_original_complex_construction(tmp_path):
    # Expected values retain the original PrepProtenixComplex construction,
    # including CCD prefixing, count coercion, ID lists and default naming.
    components = [
        {"type": "protein", "id": "B", "sequence": "ACDE", "count": 2},
        {"type": "peptide", "id": "A", "sequence": "FGHI"},
        {"type": "dna", "id": "D", "sequence": "ACGT", "count": "2"},
        {"type": "rna", "id": "R", "sequence": "ACGU"},
        {"type": "ligand", "id": "L", "ccd": "ATP"},
        {"type": "ligand", "smiles": "CCO", "count": 0},
        {"type": "ion", "id": "Z", "element": "zn", "count": "invalid"},
    ]
    source = tmp_path / "native-name.json"
    source.write_text(json.dumps({"components": components}, indent=2))
    original = source.read_bytes()
    payload = prepare_protenix_msa.load_native_protenix_input({
        "complex_json_path": str(source), "protenix_seeds": "7,19"})
    assert payload == [{"name": "native-name", "modelSeeds": [7, 19], "sequences": [
        {"proteinChain": {"sequence": "ACDE", "count": 2, "id": ["B"]}},
        {"proteinChain": {"sequence": "FGHI", "count": 1, "id": ["A"]}},
        {"dnaSequence": {"sequence": "ACGT", "count": 2, "id": ["D"]}},
        {"rnaSequence": {"sequence": "ACGU", "count": 1, "id": ["R"]}},
        {"ligand": {"ligand": "CCD_ATP", "count": 1, "id": ["L"]}},
        {"ligand": {"ligand": "CCO", "count": 1}},
        {"ion": {"ion": "ZN", "count": 1, "id": ["Z"]}},
    ]}]
    assert source.read_bytes() == original
    assert prepare_protenix_msa.build_native_protenix_input(
        seeds=[7, 19], complexes=[("native-name", {"components": components})]) == payload


def test_native_adapter_named_batches_and_collision_rejection(tmp_path):
    import pytest
    folder = tmp_path / "complexes"
    folder.mkdir()
    for filename, name in (("002.json", "second"), ("001.json", "first")):
        (folder / filename).write_text(json.dumps({"name": name, "components": [
            {"type": "protein", "id": "A", "sequence": "ACDE"},
            {"type": "protein", "id": "B", "sequence": "ACDE"}]}))
    params = {"complex_batch_dir": str(folder), "protenix_seeds": "5,8"}
    payload = prepare_protenix_msa.load_native_protenix_input(params)
    assert [task["name"] for task in payload] == ["first", "second"]
    assert payload[0]["sequences"] == [
        {"proteinChain": {"sequence": "ACDE", "count": 1, "id": ["A"]}},
        {"proteinChain": {"sequence": "ACDE", "count": 1, "id": ["B"]}}]
    manifest = tmp_path / "sequence_batch_manifest.json"
    manifest.write_text(json.dumps([{"name": "v_001_A", "sequence": "FGHI"},
                                    {"name": "v_002_A", "sequence": "ACDE"}]))
    result = prepare_protenix_msa.load_native_protenix_input({
        "sequence_batch_json_path": str(manifest), "protenix_seeds": "5,8"})
    assert [task["name"] for task in result] == ["v_001_A", "v_002_A"]
    assert [task["sequences"][0]["proteinChain"]["sequence"] for task in result] == ["FGHI", "ACDE"]
    (folder / "002.json").write_bytes((folder / "001.json").read_bytes())
    with pytest.raises(ValueError, match="unique"):
        prepare_protenix_msa.load_native_protenix_input(params)
    with pytest.raises(ValueError, match="compiled native"):
        prepare_protenix_msa.load_native_protenix_input({"sequence_batch_entries": [{"sequence": "ACDE"}]})


def test_native_adapter_generated_roster_copy_preserves_science():
    import copy
    tasks = [{"name": "candidate-pdb-stem", "modelSeeds": [3, 11],
              "templatesPath": "/native/templates", "constraint": {"pocket": [{"binder_chain": 1}]},
              "sequences": [{"proteinChain": {"sequence": "ACDE", "count": 2, "id": ["B"]}},
                            {"ion": {"ion": "ZN", "count": 3}}]}]
    original = copy.deepcopy(tasks)
    result = prepare_protenix_msa.build_native_protenix_input(seeds=[99], native_payload=tasks)
    assert result == original
    result[0]["sequences"][0]["proteinChain"]["id"].append("C")
    assert tasks == original


def test_native_adapter_reordered_entities_cannot_consume_prepared(tmp_path):
    import copy
    import pytest
    from biomodstack_msa_handoff import package_alignments, digest, hydrate_prepared_protenix_task
    payload = prepare_protenix_msa.build_native_protenix_input(seeds=[7], complexes=[("heteromer", {
        "components": [{"type": "protein", "id": "B", "sequence": "ACDE"},
                       {"type": "protein", "id": "A", "sequence": "FGHI"}]})])
    chains = []
    for index, sequence in enumerate(("ACDE", "FGHI")):
        source = tmp_path / f"{index}.a3m"
        source.write_text(f">q\n{sequence}\n")
        chains.append(dict(chain_id=f"0:{index}", role="unpaired", sequence=sequence, source=str(source)))
    root = tmp_path / "prepared"
    manifest = package_alignments(root, chains=chains, settings={}, provenance={})
    manifest["model_input"] = payload
    (root / "msa-inputs.json").write_text(json.dumps(manifest))
    changed = copy.deepcopy(payload)
    changed[0]["sequences"].reverse()
    with pytest.raises(ValueError, match="identity"):
        hydrate_prepared_protenix_task(changed, root, digest((root / "msa-inputs.json").read_bytes()))
