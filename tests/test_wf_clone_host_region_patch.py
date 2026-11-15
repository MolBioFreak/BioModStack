"""The clone source edition fixes upstream's host-region retention and is pinned by the lock."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PATCH = ROOT / "patches/wf-clone-validation-v1.8.4-nextflow25.patch"
LOCK = json.loads((ROOT / "config/ngs/wf_clone_validation_v1.8.4.lock.json").read_text())


def test_host_filter_reads_the_host_bam_its_step_writes() -> None:
    text = PATCH.read_text()
    assert "-        bedtools intersect -a mapped.bam -b $regs -wa \\\n" in text
    assert "+        bedtools intersect -a ${name}.host.bam -b $regs -wa \\\n" in text


def test_lock_names_this_patch_and_a_source_directory_of_its_own() -> None:
    patch, source = LOCK["compatibility_patch"], LOCK["patched_source"]
    assert patch["sha256"] == hashlib.sha256(PATCH.read_bytes()).hexdigest()
    assert (patch["result_commit"], patch["result_tree"]) == (source["commit"], source["tree"])
    assert patch["base_commit"] == LOCK["upstream"]["commit"]
    # A changed source never reuses the directory of the edition before it.
    assert source["path"].endswith("/v1.8.4-bms.3")
