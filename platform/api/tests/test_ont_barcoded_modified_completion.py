"""A barcoded run called with modifications completes when its preflight records the models the job asked for."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import test_ont_ngs_capability_lifecycle as lifecycle
from database import Job
from services import ont_ngs_completion, ont_ngs_contract


def _tree(tmp_path, monkeypatch, called_with: str):
    def dumps(payload, *args, **kwargs):
        if isinstance(payload, dict) and payload.get("schema") == "biomodstack.dorado_preflight.v1":
            payload["selection"]["modified_bases"] = called_with
        return json.dumps(payload, *args, **kwargs)

    monkeypatch.setattr(lifecycle, "json", SimpleNamespace(dumps=dumps, loads=json.loads))
    output = tmp_path / "current"
    _, params = lifecycle._write_terminal_product_tree(output, lock_path=ont_ngs_contract.DORADO_LOCK_PATH)
    params.update(resume_job_id="legacy", resume_work_dir="work")
    return output, params


def _complete(output: Path, params: dict):
    job = Job(id="barcoded-modified", model_id="nanopore", mode="basecall_dna", params=params, output_dir=str(output))
    return ont_ngs_completion.prepare_dorado_demux_products(job, read_root=output, persisted_root=output)


def test_barcoded_run_called_with_modifications_completes(tmp_path, monkeypatch) -> None:
    output, params = _tree(tmp_path, monkeypatch, "6mA")
    params["modified_bases"] = "6mA"
    anchor = _complete(output, params)
    assert anchor["stage"] == "dorado_demux" and anchor["identities"]["unit_count"] == 1


def test_products_called_with_other_models_than_the_job_asked_for_are_refused(tmp_path, monkeypatch) -> None:
    output, params = _tree(tmp_path, monkeypatch, "6mA")
    params["modified_bases"] = "5mC_5hmC"
    with pytest.raises(ont_ngs_completion.DoradoProductsUnavailable):
        _complete(output, params)
