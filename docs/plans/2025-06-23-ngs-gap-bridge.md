# NGS Module Gap-Bridge Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Split the monolithic `nanopore_methylation.nf` into real, isolated workflow entrypoints starting with plasmid QC, fix confirmed bugs, add execution-level tests, and wire the result contract so the frontend knows how to display NGS analysis outputs.

**Architecture:** Each ONT workflow gets its own `.nf` file that directly includes only the modules it needs. The shared `nanopore_methylation.nf` remains as a compatibility wrapper for existing callers. A new `ont_plasmid_qc.nf` becomes the first-class entrypoint for FASTQ-to-reference plasmid QC. Tests graduate from "file exists" to "produces correct outputs with test data."

**Tech Stack:** Nextflow DSL2, Python 3.11, minimap2, samtools, awk, pytest, FastAPI TestClient, React/TypeScript

---

## Phase 1: Fix Foundation Bugs (Prerequisites)

### Task 1.1: Fix modkit FASTQ exclusion bug in `nanopore_methylation.nf`

**Objective:** Allow modkit to run when FASTQ is the sole input, or explicitly remove FASTQ from methylation `input_modes`.

**Files:**
- Modify: `workflows/ngs/nanopore_methylation.nf:242`
- Modify: `platform/api/services/ont_ngs_contract.py:153`

**Step 1: Understand the bug**

Current line 242:
```groovy
if (params.run_modkit != false && analysis_bam != null && (has_pod5 || has_bam)) {
```

When `has_fastq` is true and `has_pod5`/`has_bam` are false, modkit is skipped even though the registry says `fastq` is a valid input mode for `ont_methylation_analysis`.

**Step 2: Apply the fix**

Option A (recommended — allow FASTQ + modkit):
```groovy
if (params.run_modkit != false && analysis_bam != null && (has_pod5 || has_bam || has_fastq)) {
```

Option B (restrict registry):
```python
# In ont_ngs_contract.py, change ont_methylation_analysis input_modes from ("pod5", "bam", "fastq") to ("pod5", "bam")
```

**Decision:** Use Option A. FASTQ can produce a BAM via `FastqAlign`, and modkit can run on that BAM. The MM/ML tags won't exist (FASTQ doesn't have them), but `ModkitSummary` will still produce a summary showing no modified bases found, which is truthful behavior.

**Step 3: Verify**

```bash
cd /home/dalab/biomodstack/biomodstack
python -m pytest platform/api/tests/test_nanopore_nextflow.py -v
```
Expected: All tests pass (they only check file existence, but we ensure no syntax error was introduced).

**Step 4: Commit**
```bash
git add workflows/ngs/nanopore_methylation.nf
git commit -m "fix(ngs): allow modkit to run with fastq input in nanopore_methylation"
```

---

### Task 1.2: Remove FAST5 from registry until supported

**Objective:** Eliminate the gap between declared and handled input modes.

**Files:**
- Modify: `platform/api/services/ont_ngs_contract.py:101-102` and `109-110`

**Step 1: Apply the change**

```python
# ont_basecall_dna
input_modes=("pod5",),  # fast5 removed until dorado_basecall.nf handles it

# ont_basecall_rna  
input_modes=("pod5",),  # fast5 removed until dorado_basecall.nf handles it
```

**Step 2: Update test expectations**

In `test_ont_ngs_contract.py`, update `EXPECTED_CANONICAL_WORKFLOWS` if needed (it lists workflow IDs, not input modes, so no change needed).

**Step 3: Verify**
```bash
python -m pytest platform/api/tests/test_ont_ngs_contract.py -v
```
Expected: All tests pass.

**Step 4: Commit**
```bash
git add platform/api/services/ont_ngs_contract.py
git commit -m "fix(ngs): remove fast5 from registry until supported by workflow"
```

---

## Phase 2: Build Real `ont_plasmid_qc.nf` Entrypoint

### Task 2.1: Create standalone `ont_plasmid_qc.nf`

**Objective:** Build a first-class plasmid QC workflow that directly includes only the modules it needs, without the `NANOPORE_METHYLATION` indirection.

**Files:**
- Create: `workflows/ngs/ont_plasmid_qc.nf` (replace the 18-line wrapper)

**Step 1: Write the workflow**

```groovy
#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'

// Product-specific ONT/NGS entrypoint for plasmid QC.
// These defaults make direct CLI launches match the API registry.
params.ont_workflow_id = params.ont_workflow_id ?: 'ont_plasmid_qc'
params.ont_molecule_type = params.ont_molecule_type ?: 'dna'
params.run_modkit = params.run_modkit != null ? params.run_modkit : false
params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : true
params.modified_bases = params.modified_bases ?: 'none'
params.fastq_minimap2_preset = params.fastq_minimap2_preset ?: 'map-ont'
params.manifest_contract = params.manifest_contract ?: 'sequence_qc.manifest.v1'

workflow ONT_PLASMID_QC {
    main:
    def has_fastq = params.fastq_path && params.fastq_path.toString().trim()
    def has_reference = params.reference_fasta && params.reference_fasta.toString().trim()

    if (!has_fastq) {
        error("FASTQ input is required for ont_plasmid_qc mode (--fastq_path)")
    }
    if (!has_reference) {
        error("Reference FASTA is required for ont_plasmid_qc mode (--reference_fasta)")
    }

    def fastq_input = file(params.fastq_path)
    if (!fastq_input.exists()) {
        error("FASTQ file not found: ${params.fastq_path}")
    }

    def reference_file = file(params.reference_fasta)
    if (!reference_file.exists()) {
        error("Reference FASTA not found: ${params.reference_fasta}")
    }

    def allowed_presets = ['map-ont', 'map-hifi', 'map-pb', 'sr'] as Set
    def preset = (params.fastq_minimap2_preset ?: 'map-ont').toString().trim()
    if (!allowed_presets.contains(preset)) {
        error("Unsupported --fastq_minimap2_preset '${preset}'. Supported: ${allowed_presets.join(', ')}")
    }

    FastqAlign(
        Channel.of(fastq_input),
        Channel.of(reference_file)
    )

    FastqPlasmidQC(
        FastqAlign.out.aligned,
        Channel.of(reference_file),
        Channel.of(fastq_input)
    )
}

workflow {
    ONT_PLASMID_QC()
}
```

**Step 2: Verify syntax**

```bash
cd /home/dalab/biomodstack/biomodstack
nextflow run workflows/ngs/ont_plasmid_qc.nf -help 2>&1 | head -20
```
Expected: Nextflow loads without syntax errors (it will fail on missing params, which is expected for `-help`).

**Step 3: Commit**
```bash
git add workflows/ngs/ont_plasmid_qc.nf
git commit -m "feat(ngs): standalone ont_plasmid_qc workflow entrypoint"
```

---

### Task 2.2: Update `WORKFLOW_ENTRYPOINTS` to route to real entrypoint

**Objective:** Ensure the API service routes `ont_plasmid_qc` to the new standalone file.

**Files:**
- Modify: `platform/api/services/nextflow.py` (check if `WORKFLOW_ENTRYPOINTS` already maps correctly)

**Step 1: Verify current mapping**

```bash
grep -n "ont_plasmid_qc" platform/api/services/nextflow.py
```

Expected: It should already map to `workflows/ngs/ont_plasmid_qc.nf` based on `test_ont_ngs_workflow_products.py`. If not, update it.

**Step 2: Update test expectations**

In `test_ont_ngs_workflow_products.py`, update the assertion that checks for `NANOPORE_METHYLATION` in the workflow text. The new standalone workflow does NOT contain `NANOPORE_METHYLATION`, so the test needs to be split:

```python
def test_all_canonical_ont_products_have_direct_entrypoints() -> None:
    assert set(CANONICAL_ONT_WORKFLOW_IDS) == set(EXPECTED_ONT_ENTRYPOINTS)

    for workflow_id, rel_path in EXPECTED_ONT_ENTRYPOINTS.items():
        assert WORKFLOW_ENTRYPOINTS[workflow_id] == rel_path
        assert resolve_nextflow_entrypoint(effective_profile=workflow_id) == rel_path
        workflow_text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert "workflow {" in workflow_text
        assert f"params.ont_workflow_id = params.ont_workflow_id ?: '{workflow_id}'" in workflow_text
        assert "params.manifest_contract = params.manifest_contract ?: 'sequence_qc.manifest.v1'" in workflow_text
        # Legacy monolithic workflows still include NANOPORE_METHYLATION; standalone ones don't
        if workflow_id in {"ont_methylation_analysis", "ont_basecall_dna", "ont_basecall_rna", "ont_fastq_qc", "ont_construct_screening"}:
            assert "NANOPORE_METHYLATION" in workflow_text
```

**Step 3: Verify**
```bash
python -m pytest platform/api/tests/test_ont_ngs_workflow_products.py -v
```
Expected: Tests pass after adjustment.

**Step 4: Commit**
```bash
git add platform/api/tests/test_ont_ngs_workflow_products.py
git commit -m "test(ngs): adjust entrypoint test for standalone ont_plasmid_qc"
```

---

### Task 2.3: Create minimal test data for plasmid QC

**Objective:** Create a tiny FASTQ + reference that can exercise the full pipeline in under 30 seconds.

**Files:**
- Create: `platform/api/tests/fixtures/ngs/test_reference.fasta`
- Create: `platform/api/tests/fixtures/ngs/test_reads.fastq`

**Step 1: Create reference**

```fasta
>test_plasmid_pUC19_fragment
GAGATACCTACAGCGTGAGCTATGACTGGAGTGCCAACTCCTCAAGCGTATTCAATCA
TATGCTTCCCGCCGCCCAGAATGCGATGGCTCCTGCAAGTTAAATATTTAGCCTTATT
```
(120 bp — small enough for instant alignment)

**Step 2: Create FASTQ reads**

```fastq
@read1_pUC19_forward
GAGATACCTACAGCGTGAGCTATGACTGGAGTGCCAACTCCTCAAGCGTATTCAATCA
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII
@read2_pUC19_reverse
TGATTGAATACGCTTGAGGAGTTGGCACTCCAGTCATAGCTCACGCTGTAGGTATCTC
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII
@read3_pUC19_with_error
GAGATACCTACAGCGTGAGCTATGACTGGAGTGCCAACTCCTCAAGCGTATTCAATCA
+
IIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIIII
```

**Step 3: Verify files exist**
```bash
ls -la platform/api/tests/fixtures/ngs/
```

**Step 4: Commit**
```bash
git add platform/api/tests/fixtures/ngs/
git commit -m "test(ngs): add minimal plasmid QC test fixtures"
```

---

### Task 2.4: Add execution-level test for `ont_plasmid_qc`

**Objective:** Write a test that actually runs the Nextflow workflow and asserts output files exist.

**Files:**
- Create: `platform/api/tests/test_ont_plasmid_qc_execution.py`

**Step 1: Write the test**

```python
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

NGS_FIXTURES = REPO_ROOT / "platform" / "api" / "tests" / "fixtures" / "ngs"


def test_ont_plasmid_qc_produces_expected_outputs() -> None:
    """Run ont_plasmid_qc with test fixtures and verify all expected artifacts."""
    out_dir = REPO_ROOT / ".test_out_ont_plasmid_qc"
    fastq = NGS_FIXTURES / "test_reads.fastq"
    reference = NGS_FIXTURES / "test_reference.fasta"

    cmd = [
        "nextflow", "run",
        str(REPO_ROOT / "workflows" / "ngs" / "ont_plasmid_qc.nf"),
        "-profile", "workstation_ryzen7960x",
        "--fastq_path", str(fastq),
        "--reference_fasta", str(reference),
        "--out_dir", str(out_dir),
        "--job_id", "test-plasmid-qc-1",
        "-with-trace", str(out_dir / "trace.txt"),
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=300,  # 5 minutes max
    )

    assert result.returncode == 0, f"Nextflow failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    fastq_qc_dir = out_dir / "fastq_qc"
    expected_files = {
        "read_lengths.tsv",
        "fastq_qc_summary.tsv",
        "fastq_alignment_stats.tsv",
        "fastq_coverage.tsv",
        "per_base_support.tsv",
        "qc_manifest.json",
        "reference_qc.fasta",
        "reference_qc.fasta.fai",
        "igv_coverage_depth.bedgraph",
        "igv_position_gradient.bedgraph",
        "igv_gc_content.bedgraph",
        "igv_gc_zscore.bedgraph",
        "igv_split_read_density.bedgraph",
        "igv_softclip_density.bedgraph",
        "igv_junction_hotspots.bed",
        "igv_report_sites.bed",
        "igv_report_sites.tsv",
        "igv_track_config.json",
        "igv_report.html",
        "fastq_consensus.fasta",
        "fastq_consensus.fasta.fai",
        "fastq_consensus.log",
        "fastq_qc.log",
        "aligned.bam",
        "aligned.bam.bai",
    }

    found_files = {p.name for p in fastq_qc_dir.iterdir() if p.is_file()}
    missing = expected_files - found_files
    assert not missing, f"Missing expected output files: {missing}"

    # Verify manifest is valid JSON and has expected structure
    manifest_path = fastq_qc_dir / "qc_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_schema_version"] == 1
    assert manifest["job_id"] == "test-plasmid-qc-1"
    assert manifest["reference"]["length"] == 120
    assert manifest["consensus"]["status"] in {"ok", "pileup_majority_fallback", "reference_copy_fallback"}
    assert len(manifest["artifacts"]) >= 10

    # Verify per_base_support has correct structure
    support_path = fastq_qc_dir / "per_base_support.tsv"
    support_lines = support_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(support_lines) == 121  # header + 120 positions
    header = support_lines[0].split("\t")
    assert header[0] == "chrom"
    assert "consensus_base" in header
    assert "major_allele_fraction" in header

    # Cleanup
    subprocess.run(["rm", "-rf", str(out_dir)], check=False)


def test_ont_plasmid_qc_fails_without_reference() -> None:
    """Verify the workflow fails fast when reference is missing."""
    out_dir = REPO_ROOT / ".test_out_ont_plasmid_qc_no_ref"
    fastq = NGS_FIXTURES / "test_reads.fastq"

    cmd = [
        "nextflow", "run",
        str(REPO_ROOT / "workflows" / "ngs" / "ont_plasmid_qc.nf"),
        "-profile", "workstation_ryzen7960x",
        "--fastq_path", str(fastq),
        "--out_dir", str(out_dir),
        "--job_id", "test-plasmid-qc-no-ref",
    ]

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
        timeout=60,
    )

    assert result.returncode != 0
    assert "Reference FASTA is required" in result.stderr or "Reference FASTA is required" in result.stdout
    subprocess.run(["rm", "-rf", str(out_dir)], check=False)
```

**Step 2: Run the test (expect initial failure if minimap2/samtools not in PATH)**

```bash
cd /home/dalab/biomodstack/biomodstack
python -m pytest platform/api/tests/test_ont_plasmid_qc_execution.py -v --tb=short
```

Expected: If minimap2/samtools are available, tests pass. If not, we need to document runtime requirements.

**Step 3: Commit**
```bash
git add platform/api/tests/test_ont_plasmid_qc_execution.py
git commit -m "test(ngs): add execution-level test for ont_plasmid_qc"
```

---

## Phase 3: Build `ont_fastq_qc.nf` (Reference-Optional Variant)

### Task 3.1: Create `ont_fastq_qc.nf` — read stats without alignment

**Objective:** A lightweight workflow that produces read-length/Q-score/yield stats from FASTQ without requiring alignment.

**Files:**
- Create: `workflows/ngs/ont_fastq_qc.nf` (replace the 18-line wrapper)
- Create: `modules/ngs/fastq_stats.nf`

**Step 1: Create `fastq_stats.nf` module**

```groovy
process FastqStats {
    label 'local_cpu'
    publishDir "${params.out_dir}/fastq_stats", mode: 'copy'
    tag "fastq_stats"

    input:
    path fastq

    output:
    path "read_lengths.tsv", emit: lengths
    path "fastq_stats_summary.tsv", emit: summary
    path "fastq_stats.log", emit: log

    script:
    def minReadLength = (params.min_fastq_read_length ?: 0) as Integer
    """
    set -euo pipefail

    printf "read_id\tlength_bp\n" > read_lengths.tsv

    if [[ "${fastq}" == *.gz ]]; then
        reader="zcat"
    else
        reader="cat"
    fi

    \${reader} "${fastq}" | awk -v minlen=${minReadLength} '
        NR % 4 == 1 {
            id = substr(\$0, 2)
            split(id, parts, /[ \\t]/)
            read_id = parts[1]
        }
        NR % 4 == 2 {
            len = length(\$0)
            if (len >= minlen) print read_id "\t" len
        }
    ' >> read_lengths.tsv

    total_reads=\$(awk 'NR > 1 {c++} END {print c + 0}' read_lengths.tsv)
    total_bases=\$(awk 'NR > 1 {s += \$2} END {print s + 0}' read_lengths.tsv)
    mean_read_length=\$(awk 'NR > 1 {s += \$2; c++} END {if (c > 0) printf "%.2f", s / c; else printf "0"}' read_lengths.tsv)
    median_read_length=\$(awk 'NR > 1 {print \$2}' read_lengths.tsv | LC_ALL=C sort -n | awk '
        {v[NR] = \$1}
        END {
            if (NR == 0) { print 0 }
            else if (NR % 2 == 1) { print v[(NR + 1) / 2] }
            else { printf "%.2f", (v[NR / 2] + v[(NR / 2) + 1]) / 2 }
        }
    ')

    if [[ "\${total_bases}" -gt 0 ]]; then
        n50_read_length=\$(awk 'NR > 1 {print \$2}' read_lengths.tsv | LC_ALL=C sort -nr | awk -v half="\${total_bases}" '
            BEGIN { threshold = half / 2.0 }
            { cumulative += \$1; if (cumulative >= threshold) { print \$1; found = 1; exit } }
            END { if (!found) print 0 }
        ')
    else
        n50_read_length=0
    fi

    {
        echo -e "metric\tvalue"
        echo -e "total_reads\t\${total_reads}"
        echo -e "total_bases\t\${total_bases}"
        echo -e "mean_read_length_bp\t\${mean_read_length}"
        echo -e "median_read_length_bp\t\${median_read_length}"
        echo -e "n50_read_length_bp\t\${n50_read_length}"
        echo -e "min_read_length_filter\t${minReadLength}"
    } > fastq_stats_summary.tsv

    {
        echo "FASTQ stats complete"
        echo "Reads: \${total_reads}; Bases: \${total_bases}"
        echo "Mean/Median/N50: \${mean_read_length}/\${median_read_length}/\${n50_read_length} bp"
    } > fastq_stats.log
    """
}
```

**Step 2: Create `ont_fastq_qc.nf` workflow**

```groovy
#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { FastqStats } from '../../modules/ngs/fastq_stats.nf'
include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'

params.ont_workflow_id = params.ont_workflow_id ?: 'ont_fastq_qc'
params.ont_molecule_type = params.ont_molecule_type ?: 'dna'
params.run_modkit = params.run_modkit != null ? params.run_modkit : false
params.run_fastq_qc = params.run_fastq_qc != null ? params.run_fastq_qc : true
params.modified_bases = params.modified_bases ?: 'none'
params.fastq_minimap2_preset = params.fastq_minimap2_preset ?: 'map-ont'
params.manifest_contract = params.manifest_contract ?: 'sequence_qc.manifest.v1'

workflow ONT_FASTQ_QC {
    main:
    def has_fastq = params.fastq_path && params.fastq_path.toString().trim()
    def has_reference = params.reference_fasta && params.reference_fasta.toString().trim()

    if (!has_fastq) {
        error("FASTQ input is required for ont_fastq_qc mode (--fastq_path)")
    }

    def fastq_input = file(params.fastq_path)
    if (!fastq_input.exists()) {
        error("FASTQ file not found: ${params.fastq_path}")
    }

    // Always produce read stats
    FastqStats(Channel.of(fastq_input))

    // If reference provided, also run alignment + plasmid QC
    if (has_reference) {
        def reference_file = file(params.reference_fasta)
        if (!reference_file.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }

        def allowed_presets = ['map-ont', 'map-hifi', 'map-pb', 'sr'] as Set
        def preset = (params.fastq_minimap2_preset ?: 'map-ont').toString().trim()
        if (!allowed_presets.contains(preset)) {
            error("Unsupported --fastq_minimap2_preset '${preset}'")
        }

        FastqAlign(
            Channel.of(fastq_input),
            Channel.of(reference_file)
        )

        FastqPlasmidQC(
            FastqAlign.out.aligned,
            Channel.of(reference_file),
            Channel.of(fastq_input)
        )
    }
}

workflow {
    ONT_FASTQ_QC()
}
```

**Step 3: Verify**
```bash
nextflow run workflows/ngs/ont_fastq_qc.nf -help 2>&1 | head -10
```

**Step 4: Commit**
```bash
git add modules/ngs/fastq_stats.nf workflows/ngs/ont_fastq_qc.nf
git commit -m "feat(ngs): standalone ont_fastq_qc with optional alignment"
```

---

### Task 3.2: Add execution test for `ont_fastq_qc`

**Objective:** Test both the reference-optional and reference-present paths.

**Files:**
- Create: `platform/api/tests/test_ont_fastq_qc_execution.py`

**Step 1: Write the test**

```python
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

API_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = API_ROOT.parent.parent

if str(API_ROOT) not in sys.path:
    sys.path.insert(0, str(API_ROOT))

NGS_FIXTURES = REPO_ROOT / "platform" / "api" / "tests" / "fixtures" / "ngs"


def test_ont_fastq_qc_without_reference_produces_stats_only() -> None:
    """Run ont_fastq_qc without reference and verify only stats are produced."""
    out_dir = REPO_ROOT / ".test_out_ont_fastq_qc"
    fastq = NGS_FIXTURES / "test_reads.fastq"

    cmd = [
        "nextflow", "run",
        str(REPO_ROOT / "workflows" / "ngs" / "ont_fastq_qc.nf"),
        "-profile", "workstation_ryzen7960x",
        "--fastq_path", str(fastq),
        "--out_dir", str(out_dir),
        "--job_id", "test-fastq-qc-1",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
    assert result.returncode == 0, f"Nextflow failed:\n{result.stderr}"

    stats_dir = out_dir / "fastq_stats"
    assert (stats_dir / "read_lengths.tsv").exists()
    assert (stats_dir / "fastq_stats_summary.tsv").exists()
    assert not (out_dir / "fastq_qc").exists()  # No reference = no plasmid QC

    subprocess.run(["rm", "-rf", str(out_dir)], check=False)


def test_ont_fastq_qc_with_reference_produces_full_qc() -> None:
    """Run ont_fastq_qc with reference and verify full plasmid QC outputs."""
    out_dir = REPO_ROOT / ".test_out_ont_fastq_qc_ref"
    fastq = NGS_FIXTURES / "test_reads.fastq"
    reference = NGS_FIXTURES / "test_reference.fasta"

    cmd = [
        "nextflow", "run",
        str(REPO_ROOT / "workflows" / "ngs" / "ont_fastq_qc.nf"),
        "-profile", "workstation_ryzen7960x",
        "--fastq_path", str(fastq),
        "--reference_fasta", str(reference),
        "--out_dir", str(out_dir),
        "--job_id", "test-fastq-qc-2",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300)
    assert result.returncode == 0, f"Nextflow failed:\n{result.stderr}"

    assert (out_dir / "fastq_stats" / "read_lengths.tsv").exists()
    assert (out_dir / "fastq_qc" / "per_base_support.tsv").exists()
    assert (out_dir / "fastq_qc" / "qc_manifest.json").exists()

    subprocess.run(["rm", "-rf", str(out_dir)], check=False)
```

**Step 2: Commit**
```bash
git add platform/api/tests/test_ont_fastq_qc_execution.py
git commit -m "test(ngs): add execution tests for ont_fastq_qc"
```

---

## Phase 4: Add ONT Result Contract

### Task 4.1: Add `sequence_qc_v1` to `result_contracts.py`

**Objective:** The frontend result viewer needs to know how to analyze NGS jobs.

**Files:**
- Modify: `platform/api/services/result_contracts.py`

**Step 1: Add the contract definition**

```python
    ResultContractDefinition(
        contract_id="sequence_qc_v1",
        model_ids=["nanopore"],
        stage_families=["ont_ngs"],
        stage_modes=["plasmid_qc", "fastq_qc", "construct_screening", "methylation_analysis", "basecall_dna", "basecall_rna"],
        artifact_classes=["sequence_qc_manifest"],
        result_sets=["sequence_qc"],
        supported_analyzers=["sequence_qc_v1"],
        viewer_capabilities=["result_filter", "sequence_qc_metrics", "igv_viewer", "manifest_artifact_browser"],
        required_fields=["artifact_class", "result_set", "job_id"],
        required_artifacts=["qc_manifest"],
        notes="ONT/NGS sequence QC outputs including per-base support, consensus, IGV tracks, and methylation evidence.",
    ),
```

Add this to `_RESULT_CONTRACT_DEFINITIONS` after `confornets_monomer_v1`.

**Step 2: Verify**
```bash
python -c "from platform.api.services.result_contracts import get_result_contract_definitions; print([d.contract_id for d in get_result_contract_definitions()])"
```
Expected: `sequence_qc_v1` appears in the list.

**Step 3: Commit**
```bash
git add platform/api/services/result_contracts.py
git commit -m "feat(ngs): add sequence_qc_v1 result contract for ONT analysis"
```

---

## Phase 5: Decompose NGSToolkit.tsx (Optional but Recommended)

### Task 5.1: Extract IGV viewer into `ngs/IgvViewer.tsx`

**Objective:** Reduce the 5,066-line `NGSToolkit.tsx` into focused sub-components.

**Files:**
- Create: `platform/frontend/src/components/ngs/IgvViewer.tsx`
- Create: `platform/frontend/src/components/ngs/useIgvArtifacts.ts`
- Modify: `platform/frontend/src/components/NGSToolkit.tsx` (remove extracted code)

**Step 1: Extract IGV-related types and hooks**

Move `IgvArtifacts`, `IgvAlignmentSource`, `IgvReferenceSource`, and the IGV initialization logic from `NGSToolkit.tsx` into `IgvViewer.tsx`.

**Step 2: Extract artifact resolution hook**

Move the `useSequenceQcManifest` consumption and IGV artifact URL building into `useIgvArtifacts.ts`.

**Step 3: Update NGSToolkit.tsx imports**

```typescript
import { IgvViewer } from './ngs/IgvViewer';
import { useIgvArtifacts } from './ngs/useIgvArtifacts';
```

**Step 4: Verify build**
```bash
cd platform/frontend
npm run build 2>&1 | tail -20
```
Expected: Build succeeds with no TypeScript errors.

**Step 5: Commit**
```bash
git add platform/frontend/src/components/ngs/IgvViewer.tsx platform/frontend/src/components/ngs/useIgvArtifacts.ts platform/frontend/src/components/NGSToolkit.tsx
git commit -m "refactor(frontend): extract IGV viewer from NGSToolkit"
```

---

## Phase 6: Update Registry and Documentation

### Task 6.1: Update `ont_ngs_contract.py` lifecycle states

**Objective:** Reflect that `ont_plasmid_qc` and `ont_fastq_qc` are now `seed` (real implementation) not `planned`.

**Files:**
- Modify: `platform/api/services/ont_ngs_contract.py`

**Step 1: Update lifecycle fields**

```python
    "ont_fastq_qc": OntWorkflowSpec(
        ...
        lifecycle="seed",  # was "planned"
    ),
```

`ont_construct_screening`, `ont_basecall_dna`, `ont_basecall_rna` remain `planned` until implemented.

**Step 2: Verify**
```bash
python -m pytest platform/api/tests/test_ont_ngs_contract.py -v
```

**Step 3: Commit**
```bash
git add platform/api/services/ont_ngs_contract.py
git commit -m "docs(ngs): update ont_fastq_qc lifecycle to seed"
```

---

## Verification Checklist

After all phases:

- [ ] `pytest platform/api/tests/test_ont_ngs_contract.py -v` passes
- [ ] `pytest platform/api/tests/test_ont_ngs_workflow_products.py -v` passes
- [ ] `pytest platform/api/tests/test_nanopore_nextflow.py -v` passes
- [ ] `pytest platform/api/tests/test_ont_plasmid_qc_execution.py -v` passes (requires minimap2/samtools)
- [ ] `pytest platform/api/tests/test_ont_fastq_qc_execution.py -v` passes (requires minimap2/samtools)
- [ ] `nextflow run workflows/ngs/ont_plasmid_qc.nf -help` loads without syntax errors
- [ ] `nextflow run workflows/ngs/ont_fastq_qc.nf -help` loads without syntax errors
- [ ] Frontend build succeeds (`npm run build` in `platform/frontend`)
- [ ] `ont_plasmid_qc` no longer includes `NANOPORE_METHYLATION`
- [ ] `ont_fastq_qc` no longer includes `NANOPORE_METHYLATION`
- [ ] `sequence_qc_v1` appears in result contract definitions

---

## Rollback Plan

If any phase causes regressions:

1. **Phase 1 (bug fixes):** Safe to keep; no behavior changes for existing paths
2. **Phase 2 (standalone plasmid QC):** The old `nanopore_methylation.nf` still exists and works. Revert `ont_plasmid_qc.nf` to the 18-line wrapper if needed
3. **Phase 3 (fastq QC):** Same — revert to wrapper if issues arise
4. **Phase 4 (result contract):** Removing `sequence_qc_v1` from the list is safe; no data migration needed
5. **Phase 5 (frontend refactor):** Git revert the commit; `NGSToolkit.tsx` was only refactored, not functionally changed

---

## Non-Goals (Explicitly Out of Scope)

- **RNA basecalling model selection** (`rna002`, `rna004`) — remains `planned`
- **Construct screening expected-sequence comparison** — remains `planned`
- **Dorado duplex basecalling** — `ont_basecall_dna`/`ont_basecall_rna` remain wrappers
- **FAST5 input support** — explicitly removed from registry
- **wf-clone-validation execution tests** — module exists but remains untested
- **fastq_dimer_qc.nf tests** — 1,426-line module, out of scope for this plan
- **Real MinKNOW instrument integration tests** — host-agent mocking is sufficient

---

**Plan complete. Ready to execute using subagent-driven-development.**
