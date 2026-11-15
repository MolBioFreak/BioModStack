"""The saved topology of a reference reaches the analysis; a linear one turns rotation off."""
from __future__ import annotations

import hashlib
from types import SimpleNamespace

import pytest

from services import molbio_ngs_receipts as receipts
from services import ont_barcode_batches as batches
from services.ont_ngs_contract import normalize_ont_launch_params


def test_a_linear_reference_turns_rotation_off_and_leaves_the_junction_screen():
    plain = normalize_ont_launch_params("ont_plasmid_qc", {})
    assert plain["enable_rotating_reference_frames"] is True and "reference_topology" not in plain
    assert normalize_ont_launch_params("ont_plasmid_qc", {"reference_topology": "circular"})["enable_rotating_reference_frames"] is True
    linear = normalize_ont_launch_params("ont_plasmid_qc", {"reference_topology": "linear"})
    assert linear["enable_rotating_reference_frames"] is False and linear["reference_topology"] == "linear"
    assert linear["single_ref_split_min_mapq"] == plain["single_ref_split_min_mapq"]
    with pytest.raises(ValueError, match="linear or circular"):
        normalize_ont_launch_params("ont_plasmid_qc", {"reference_topology": "round"})


class _Session:
    def add(self, row):
        pass

    async def flush(self):
        return None


@pytest.mark.asyncio
@pytest.mark.parametrize("snapshot, expected", [({"is_circular": False}, "linear"), ({"is_circular": True}, "circular"), ({}, None)])
async def test_a_receipt_carries_the_topology_the_library_saved(tmp_path, monkeypatch, snapshot, expected):
    monkeypatch.setattr(receipts, "get_inputs_dir", lambda: tmp_path)
    revision = SimpleNamespace(id="rev", content_sha256=hashlib.sha256(b"ACGT").hexdigest(),
                               snapshot={"sequence": "ACGT", "sequence_type": "dna", **snapshot})
    receipt = await receipts.issue_molbio_ngs_receipt(_Session(), sequence_id="seq", revision=revision)
    assert receipts.receipt_reference_topology(receipt) == expected


def test_a_barcode_analysis_is_told_the_kits_its_source_run_named():
    preflight = {"inputs": {"sequencing_kits": ["sqk-rbk114-96"]}, "barcoding": {"kit": "SQK-RBK114-96"}}
    assert batches._sequencing_kits(preflight) == ["SQK-RBK114-96", "sqk-rbk114-96"]
    assert batches._sequencing_kits({}) == []
