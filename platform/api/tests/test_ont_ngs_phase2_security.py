from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

import routers.ont_runs as ont_runs
from routers.ont_runs import OntNgsSubmitRequest, _job_create_for_ont_submit


def request_for(params: dict[str, object]) -> OntNgsSubmitRequest:
    return OntNgsSubmitRequest(params=params)


@pytest.fixture(autouse=True)
def _unit_contract_paths_are_prevalidated(monkeypatch):
    monkeypatch.setattr(ont_runs, "_confine_submitted_path", lambda value, _label, **_kwargs: str(value))


def test_reference_digest_is_server_controlled() -> None:
    with pytest.raises(ValueError, match="server-controlled.*provenance"):
        _job_create_for_ont_submit(
            "ont_fastq_qc",
            request_for(
                {
                    "fastq_path": "/tmp/reads.fastq",
                    "reference_fasta": "/tmp/reference.fasta",
                    "reference_sequence_sha256": "0" * 64,
                }
            ),
        )


def test_existing_reference_is_bound_to_normalized_sequence_digest(tmp_path: Path) -> None:
    reference = tmp_path / "reference.fasta"
    reference.write_text(">expected\nacgt\nacgt\n", encoding="utf-8")
    job = _job_create_for_ont_submit(
        "ont_fastq_qc",
        request_for(
            {
                "fastq_path": "/tmp/reads.fastq",
                "reference_fasta": str(reference),
            }
        ),
    )
    expected = hashlib.sha256(b"ACGTACGT").hexdigest()
    assert job.params["reference_sequence_sha256"] == expected


def test_inaccessible_reference_remains_explicitly_unbound() -> None:
    job = _job_create_for_ont_submit(
        "ont_fastq_qc",
        request_for(
            {
                "fastq_path": "/tmp/reads.fastq",
                "reference_fasta": "/not-mounted/reference.fasta",
            }
        ),
    )
    assert "reference_sequence_sha256" not in job.params


@pytest.mark.parametrize(
    "runtime_param",
    [
        "code_root",
        "container_dir",
        "data_root",
        "out_dir",
        "output_dir",
        "work_dir",
        "nxf_home",
        "singularity_cache",
        "wf_clone_nxf_home",
        "wf_clone_singularity_cache",
    ],
)
def test_generic_submit_rejects_server_runtime_roots(runtime_param: str) -> None:
    with pytest.raises(ValueError, match="server-controlled.*runtime"):
        _job_create_for_ont_submit(
            "ont_fastq_qc",
            request_for(
                {
                    "fastq_path": "/tmp/reads.fastq",
                    "reference_fasta": "/tmp/reference.fasta",
                    runtime_param: "$(touch /tmp/should-not-run)",
                }
            ),
        )


def test_construct_verify_shell_quotes_every_runtime_derived_executable_path() -> None:
    module = Path(__file__).resolve().parents[3] / "modules" / "ngs" / "construct_verify.nf"
    source = module.read_text(encoding="utf-8")

    assert 'def topologyScript = shellQuote("${codeRoot}/scripts/build_construct_topology_evidence.py")' in source
    assert 'def verifierScript = shellQuote("${codeRoot}/scripts/verify_construct.py")' in source
    assert 'def profileConfig = shellQuote("${codeRoot}/config/ngs/construct_verify_profiles.json")' in source
    assert 'def doradoImage = shellQuote("${containerDir}/dorado.sif")' in source
    assert '"${codeRoot}/scripts/build_construct_topology_evidence.py" \\' not in source
    assert '[[ -f "${containerDir}/dorado.sif" ]]' not in source
