from __future__ import annotations

import hashlib
import importlib.util
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd
import pytest


REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / "scripts" / "run_frustrampnn_predict_batch.py"
CSV_COLUMNS = [
    "frustration_pred",
    "position",
    "wildtype",
    "mutation",
    "chain",
    "pdb",
]
SEQUENTIAL_SEMANTICS = (
    "Pinned upstream FrustraMPNN.predict_batch processes pdb_paths sequentially "
    "under one loaded model object, catches each per-structure exception, and omits "
    "failed structures from its returned DataFrame."
)


def _load_adapter():
    assert SCRIPT.is_file(), "product adapter script is missing"
    spec = importlib.util.spec_from_file_location(
        "run_frustrampnn_predict_batch_under_test", SCRIPT
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _canonical_json(value: dict[str, Any]) -> bytes:
    return (
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")


def _manifest(tmp_path: Path, *, count: int = 3) -> tuple[dict[str, Any], Path]:
    records = []
    for ordinal, stem in enumerate(("alpha", "beta", "gamma")[:count]):
        pdb_path = tmp_path / "staged" / f"{stem}.pdb"
        pdb_path.parent.mkdir(exist_ok=True)
        pdb_path.write_text(f"HEADER {stem}\nEND\n", encoding="ascii")
        records.append(
            {
                "ordinal": ordinal,
                "candidate_id": f"candidate-{stem}",
                "invocation_id": f"invocation-{stem}",
                "staged_pdb_path": str(pdb_path),
                "source_sha256": hashlib.sha256(pdb_path.read_bytes()).hexdigest(),
            }
        )
    manifest = {
        "schema_name": "frustrampnn_predict_batch_input",
        "schema_version": 1,
        "checkpoint_path": "/models/frustrampnn.ckpt",
        "device": "cuda:0",
        "records": records,
    }
    path = tmp_path / "batch.json"
    path.write_bytes(_canonical_json(manifest))
    return manifest, path


def _incrementing_clock():
    current = datetime(2026, 8, 26, 12, 0, 0, tzinfo=timezone.utc)

    def clock() -> datetime:
        nonlocal current
        value = current
        current += timedelta(seconds=1)
        return value

    return clock


class _FakeFrustraMPNNType:
    load_calls: list[tuple[str, str]] = []
    predict_calls: list[tuple[list[str], None, bool]] = []
    result = pd.DataFrame(columns=CSV_COLUMNS)

    @classmethod
    def reset(cls) -> None:
        cls.load_calls = []
        cls.predict_calls = []
        cls.result = pd.DataFrame(columns=CSV_COLUMNS)

    @classmethod
    def from_pretrained(cls, checkpoint_path: str, *, device: str):
        cls.load_calls.append((checkpoint_path, device))
        return cls()

    def predict_batch(
        self,
        pdb_paths: list[str],
        chains: None = None,
        show_progress: bool = False,
    ) -> pd.DataFrame:
        type(self).predict_calls.append((list(pdb_paths), chains, show_progress))
        return type(self).result.copy()


def _fake_module() -> SimpleNamespace:
    _FakeFrustraMPNNType.reset()
    return SimpleNamespace(FrustraMPNN=_FakeFrustraMPNNType)


def test_adapter_uses_one_upstream_batch_call_and_records_every_terminal_structure(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path)
    fake_module = _fake_module()
    _FakeFrustraMPNNType.result = pd.DataFrame(
        [
            {
                "frustration_pred": -1.25,
                "position": 8,
                "wildtype": "A",
                "mutation": "V",
                "pdb": "gamma",
                "chain": "B",
            },
            {
                "frustration_pred": 0.5,
                "position": 2,
                "wildtype": "G",
                "mutation": "L",
                "pdb": "alpha",
                "chain": "A",
            },
            {
                "frustration_pred": 0.75,
                "position": 3,
                "wildtype": "S",
                "mutation": "T",
                "pdb": "alpha",
                "chain": "A",
            },
        ],
        columns=[
            "frustration_pred",
            "position",
            "wildtype",
            "mutation",
            "pdb",
            "chain",
        ],
    )
    output_dir = tmp_path / "results"

    evidence = adapter.run_batch(
        manifest_path=manifest_path,
        output_dir=output_dir,
        frustrampnn_module=fake_module,
        clock=_incrementing_clock(),
    )

    assert _FakeFrustraMPNNType.load_calls == [
        (manifest["checkpoint_path"], manifest["device"])
    ]
    assert _FakeFrustraMPNNType.predict_calls == [
        (
            [record["staged_pdb_path"] for record in manifest["records"]],
            None,
            False,
        )
    ]
    alpha_csv = (
        "frustration_pred,position,wildtype,mutation,chain,pdb\n"
        "0.5,2,G,L,A,alpha\n"
        "0.75,3,S,T,A,alpha\n"
    ).encode("utf-8")
    gamma_csv = (
        "frustration_pred,position,wildtype,mutation,chain,pdb\n"
        "-1.25,8,A,V,B,gamma\n"
    ).encode("utf-8")
    assert (output_dir / "alpha.csv").read_bytes() == alpha_csv
    assert not (output_dir / "beta.csv").exists()
    assert (output_dir / "gamma.csv").read_bytes() == gamma_csv
    assert evidence == {
        "schema_name": "frustrampnn_batch_terminal_evidence",
        "schema_version": 1,
        "method_identity": "frustrampnn.FrustraMPNN.predict_batch",
        "upstream_sequential_semantics": SEQUENTIAL_SEMANTICS,
        "model_load_count": 1,
        "record_count": 3,
        "records": [
            {
                "ordinal": 0,
                "candidate_id": "candidate-alpha",
                "invocation_id": "invocation-alpha",
                "pdb_stem": "alpha",
                "source_sha256": manifest["records"][0]["source_sha256"],
                "started_at": "2026-08-26T12:00:00Z",
                "terminal_at": "2026-08-26T12:00:03Z",
                "status": "succeeded",
                "failure_code": None,
                "diagnostic": None,
                "row_count": 2,
                "output_csv": "alpha.csv",
                "output_sha256": hashlib.sha256(alpha_csv).hexdigest(),
            },
            {
                "ordinal": 1,
                "candidate_id": "candidate-beta",
                "invocation_id": "invocation-beta",
                "pdb_stem": "beta",
                "source_sha256": manifest["records"][1]["source_sha256"],
                "started_at": "2026-08-26T12:00:01Z",
                "terminal_at": "2026-08-26T12:00:04Z",
                "status": "failed",
                "failure_code": "upstream_output_omitted",
                "diagnostic": "upstream predict_batch returned no rows for this staged PDB",
                "row_count": None,
                "output_csv": None,
                "output_sha256": None,
            },
            {
                "ordinal": 2,
                "candidate_id": "candidate-gamma",
                "invocation_id": "invocation-gamma",
                "pdb_stem": "gamma",
                "source_sha256": manifest["records"][2]["source_sha256"],
                "started_at": "2026-08-26T12:00:02Z",
                "terminal_at": "2026-08-26T12:00:05Z",
                "status": "succeeded",
                "failure_code": None,
                "diagnostic": None,
                "row_count": 1,
                "output_csv": "gamma.csv",
                "output_sha256": hashlib.sha256(gamma_csv).hexdigest(),
            },
        ],
    }
    evidence_path = output_dir / "frustrampnn_batch_terminal_evidence_v1.json"
    assert evidence_path.read_bytes() == _canonical_json(evidence)


def test_adapter_rejects_source_tamper_before_loading_and_terminalizes_every_record(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path, count=2)
    Path(manifest["records"][0]["staged_pdb_path"]).write_bytes(b"TAMPERED\n")
    fake_module = _fake_module()
    output_dir = tmp_path / "results"

    with pytest.raises(adapter.BatchAdapterError, match="source SHA-256"):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=output_dir,
            frustrampnn_module=fake_module,
            clock=_incrementing_clock(),
        )

    assert _FakeFrustraMPNNType.load_calls == []
    assert _FakeFrustraMPNNType.predict_calls == []
    evidence = json.loads(
        (output_dir / "frustrampnn_batch_terminal_evidence_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert [record["ordinal"] for record in evidence["records"]] == [0, 1]
    assert {record["source_sha256"] for record in evidence["records"]} == {
        record["source_sha256"] for record in manifest["records"]
    }
    assert {record["status"] for record in evidence["records"]} == {"failed"}
    assert {record["failure_code"] for record in evidence["records"]} == {
        "source_verification_failed"
    }
    assert all(record["started_at"].endswith("Z") for record in evidence["records"])
    assert all(record["terminal_at"].endswith("Z") for record in evidence["records"])
    assert all(
        0 < len(record["diagnostic"]) <= 1024 for record in evidence["records"]
    )
    assert all(record["row_count"] is None for record in evidence["records"])
    assert all(record["output_csv"] is None for record in evidence["records"])
    assert all(record["output_sha256"] is None for record in evidence["records"])


def test_adapter_rejects_noncanonical_or_open_input_before_model_loading(
    tmp_path: Path,
) -> None:
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path, count=2)
    fake_module = _fake_module()

    manifest["unrecognized"] = True
    manifest_path.write_bytes(_canonical_json(manifest))
    with pytest.raises(adapter.BatchAdapterError, match="input manifest fields"):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=tmp_path / "open-results",
            frustrampnn_module=fake_module,
        )
    assert _FakeFrustraMPNNType.load_calls == []

    del manifest["unrecognized"]
    manifest["records"][0]["unrecognized"] = True
    manifest_path.write_bytes(_canonical_json(manifest))
    with pytest.raises(adapter.BatchAdapterError, match="record fields"):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=tmp_path / "open-record-results",
            frustrampnn_module=fake_module,
        )
    assert _FakeFrustraMPNNType.load_calls == []

    del manifest["records"][0]["unrecognized"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(adapter.BatchAdapterError, match="canonical JSON"):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=tmp_path / "noncanonical-results",
            frustrampnn_module=fake_module,
        )
    assert _FakeFrustraMPNNType.load_calls == []


@pytest.mark.parametrize(
    "mutation, message",
    [
        (lambda manifest: manifest.update(records=manifest["records"][:1]), "2..250"),
        (
            lambda manifest: manifest["records"][1].update(
                ordinal=manifest["records"][0]["ordinal"]
            ),
            "ordinal",
        ),
        (
            lambda manifest: manifest["records"][1].update(
                candidate_id=manifest["records"][0]["candidate_id"]
            ),
            "candidate_id",
        ),
        (
            lambda manifest: manifest["records"][1].update(
                invocation_id=manifest["records"][0]["invocation_id"]
            ),
            "invocation_id",
        ),
        (
            lambda manifest: manifest["records"][1].update(
                staged_pdb_path=manifest["records"][0]["staged_pdb_path"]
            ),
            "PDB stem",
        ),
        (
            lambda manifest: manifest["records"][0].update(
                staged_pdb_path=str(
                    Path(manifest["records"][0]["staged_pdb_path"]).with_name(
                        "unsafe.name.pdb"
                    )
                )
            ),
            "safe staged PDB",
        ),
    ],
)
def test_adapter_rejects_invalid_cardinality_or_nonunique_unsafe_identity(
    tmp_path: Path, mutation, message: str
) -> None:
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path, count=2)
    fake_module = _fake_module()
    mutation(manifest)
    manifest_path.write_bytes(_canonical_json(manifest))

    with pytest.raises(adapter.BatchAdapterError, match=message):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=tmp_path / "results",
            frustrampnn_module=fake_module,
        )
    assert _FakeFrustraMPNNType.load_calls == []


@pytest.mark.parametrize(
    "result, message",
    [
        (
            pd.DataFrame(
                [
                    {
                        "frustration_pred": 0.0,
                        "position": 1,
                        "wildtype": "A",
                        "mutation": "V",
                        "chain": "A",
                        "pdb": "alpha",
                        "extra": "forbidden",
                    }
                ]
            ),
            "output fields",
        ),
        (
            pd.DataFrame(
                [
                    {
                        "frustration_pred": 0.0,
                        "position": 1,
                        "wildtype": "A",
                        "mutation": "V",
                        "chain": "A",
                        "pdb": "unexpected",
                    }
                ]
            ),
            "unexpected pdb identity",
        ),
        (
            pd.DataFrame(
                [
                    {
                        "frustration_pred": 0.0,
                        "position": 1,
                        "wildtype": "A",
                        "mutation": "V",
                        "chain": "A",
                        "pdb": "../alpha",
                    }
                ]
            ),
            "unsafe pdb identity",
        ),
    ],
)
def test_adapter_rejects_unrecognized_unexpected_or_unsafe_upstream_output(
    tmp_path: Path, result: pd.DataFrame, message: str
) -> None:
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path, count=2)
    fake_module = _fake_module()
    _FakeFrustraMPNNType.result = result
    output_dir = tmp_path / "results"

    with pytest.raises(adapter.BatchAdapterError, match=message):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=output_dir,
            frustrampnn_module=fake_module,
        )

    assert _FakeFrustraMPNNType.load_calls == [
        (manifest["checkpoint_path"], manifest["device"])
    ]
    assert len(_FakeFrustraMPNNType.predict_calls) == 1
    assert not list(output_dir.glob("*.csv"))
    evidence = json.loads(
        (output_dir / "frustrampnn_batch_terminal_evidence_v1.json").read_text(
            encoding="utf-8"
        )
    )
    assert [record["ordinal"] for record in evidence["records"]] == [0, 1]
    assert {record["status"] for record in evidence["records"]} == {"failed"}
    assert {record["failure_code"] for record in evidence["records"]} == {
        "upstream_output_invalid"
    }
    assert all(record["source_sha256"] for record in evidence["records"])
    assert all(record["started_at"].endswith("Z") for record in evidence["records"])
    assert all(record["terminal_at"].endswith("Z") for record in evidence["records"])
    assert all(
        0 < len(record["diagnostic"]) <= 1024 for record in evidence["records"]
    )
    assert evidence["model_load_count"] == 1


def test_adapter_rejects_duplicate_or_non_dataframe_batch_return(tmp_path: Path) -> None:
    adapter = _load_adapter()
    _, manifest_path = _manifest(tmp_path, count=2)

    class DuplicateColumnsModel(_FakeFrustraMPNNType):
        result = pd.DataFrame([[0.0, 1, "A", "V", "A", "alpha", "alpha"]])
        result.columns = [*CSV_COLUMNS, "pdb"]

    duplicate_module = SimpleNamespace(FrustraMPNN=DuplicateColumnsModel)
    output_dir = tmp_path / "duplicate-results"
    with pytest.raises(adapter.BatchAdapterError, match="duplicate output fields"):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=output_dir,
            frustrampnn_module=duplicate_module,
        )

    class NonDataFrameModel(_FakeFrustraMPNNType):
        def predict_batch(self, pdb_paths, chains=None, show_progress=False):
            type(self).predict_calls.append((list(pdb_paths), chains, show_progress))
            return [{"pdb": "alpha"}]

    non_frame_module = SimpleNamespace(FrustraMPNN=NonDataFrameModel)
    with pytest.raises(adapter.BatchAdapterError, match="DataFrame"):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=tmp_path / "non-frame-results",
            frustrampnn_module=non_frame_module,
        )


def test_adapter_enforces_the_upper_batch_bound_before_model_loading(tmp_path: Path) -> None:
    adapter = _load_adapter()
    manifest, manifest_path = _manifest(tmp_path, count=2)
    template = manifest["records"][0]
    manifest["records"] = [
        {
            "ordinal": ordinal,
            "candidate_id": f"candidate-{ordinal}",
            "invocation_id": f"invocation-{ordinal}",
            "staged_pdb_path": str(
                Path(template["staged_pdb_path"]).with_name(f"safe_{ordinal}.pdb")
            ),
            "source_sha256": template["source_sha256"],
        }
        for ordinal in range(251)
    ]
    manifest_path.write_bytes(_canonical_json(manifest))
    fake_module = _fake_module()

    with pytest.raises(adapter.BatchAdapterError, match="2..250"):
        adapter.run_batch(
            manifest_path=manifest_path,
            output_dir=tmp_path / "results",
            frustrampnn_module=fake_module,
        )
    assert _FakeFrustraMPNNType.load_calls == []
