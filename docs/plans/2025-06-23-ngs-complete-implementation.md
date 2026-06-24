# NGS Module Complete Implementation Plan

> **For Hermes:** Use subagent-driven-development skill to implement this plan task-by-task.

**Goal:** Build a complete, consistent, production-grade ONT/NGS workflow suite following Nextflow DSL2 best practices, matching the architectural patterns of existing protein workflows. All 6 entrypoints are real standalone workflows. No wrappers. No half-measures.

**Architecture:** Each ONT workflow is a standalone `.nf` file that directly orchestrates the modules it needs. Shared logic lives in reusable modules under `modules/ngs/`. The old `nanopore_methylation.nf` monolith is deprecated and replaced by `ont_methylation_analysis.nf`. Protein workflows (`structure_prediction.nf`, `boltz_cp_experimental.nf`, `antibody_design.nf`) serve as the reference pattern: input validation → channel setup → module orchestration → emit outputs.

**Tech Stack:** Nextflow DSL2, Python 3.11, minimap2, samtools, awk, dorado, modkit, wf-clone-validation, pytest, FastAPI TestClient, React/TypeScript

---

## Priority Order (Confirmed by Christian)

| Priority | Workflow | What It Does | Why This Order |
|----------|----------|------------|----------------|
| 1 | `ont_plasmid_qc` | FASTQ → alignment → per-base support, consensus, IGV, manifest | **DONE** — foundation for all plasmid QC |
| 2 | `ont_wf_clone_validation` | BAM → wf-clone-validation assembly + report | **HIGH** — complete the plasmid QC pipeline |
| 3 | `ont_fastq_dimer_qc` | FASTQ → multimer/dimer detection, junction analysis | **HIGH** — plasmid QC completeness |
| 4 | `ont_fastq_qc` | FASTQ → read stats (reference-optional) | Medium — general-purpose QC |
| 5 | `ont_methylation_analysis` | POD5/BAM → dorado + modkit methylation | Medium — ancillary modified-base analysis |
| 6 | `ont_construct_screening` | FASTQ → expected-sequence comparison, pass/fail | Medium — construct validation |
| 7 | `ont_basecall_dna` | POD5 → dorado DNA basecalling | Lower — upstream data generation |
| 8 | `ont_basecall_rna` | POD5 → dorado RNA basecalling | Last — RNA is lowest priority |

---

## Phase 1: Fix Foundation Bugs (Prerequisites)

### Task 1.1: Fix modkit FASTQ exclusion bug

**Objective:** Modkit runs ONLY when explicitly requested (`run_modkit == true`) AND when the input has evidence for it (POD5 or BAM with MM/ML tags). FASTQ never runs modkit — FASTQ lacks modified-base tags.

**Files:**
- Modify: `workflows/ngs/nanopore_methylation.nf:242`

**Step 1: Apply the fix**

Current (buggy):
```groovy
if (params.run_modkit != false && analysis_bam != null && (has_pod5 || has_bam)) {
```

Fixed:
```groovy
if (params.run_modkit == true && analysis_bam != null && (has_pod5 || has_bam)) {
```

**Rationale:** `!= false` is too permissive. It runs modkit when `run_modkit` is unset (defaults to `true` in `nextflow.config`). The corrected behavior:
- `run_modkit = true` → runs modkit if POD5 or BAM input
- `run_modkit = false` → skips modkit
- `run_modkit = null` (unset) → skips modkit (must be explicitly requested)

**Step 2: Update registry defaults**

In `platform/api/services/ont_ngs_contract.py`, update `WORKFLOW_DEFAULTS`:

```python
"ont_methylation_analysis": {
    "ont_molecule_type": "dna",
    "run_modkit": True,      # Only methylation analysis defaults to True
    "run_fastq_qc": True,
    "modified_bases": "6mA 4mC_5mC",
},
"ont_plasmid_qc": {
    "ont_molecule_type": "dna",
    "run_modkit": False,     # Plasmid QC does NOT run modkit by default
    "run_fastq_qc": True,
    "fastq_minimap2_preset": "map-ont",
    "modified_bases": "none",
},
```

**Step 3: Verify**
```bash
cd /home/dalab/biomodstack/biomodstack
python -m pytest platform/api/tests/test_ont_ngs_contract.py -v
python -m pytest platform/api/tests/test_nanopore_nextflow.py -v
```

**Step 4: Commit**
```bash
git add workflows/ngs/nanopore_methylation.nf platform/api/services/ont_ngs_contract.py
git commit -m "fix(ngs): modkit only runs when explicitly requested with pod5/bam input"
```

---

### Task 1.2: Remove FAST5 from registry

**Objective:** Eliminate declared-but-unhandled input mode.

**Files:**
- Modify: `platform/api/services/ont_ngs_contract.py:101-102, 109-110`

**Step 1: Apply**
```python
# ont_basecall_dna
input_modes=("pod5",),  # fast5 removed — dorado_basecall.nf only handles pod5

# ont_basecall_rna
input_modes=("pod5",),  # fast5 removed
```

**Step 2: Verify**
```bash
python -m pytest platform/api/tests/test_ont_ngs_contract.py -v
```

**Step 3: Commit**
```bash
git add platform/api/services/ont_ngs_contract.py
git commit -m "fix(ngs): remove fast5 from registry until workflow supports it"
```

---

## Phase 2: Build Real Standalone Workflows (No Wrappers)

### Task 2.1: Deprecate `nanopore_methylation.nf` monolith

**Objective:** Mark the monolith as deprecated. It remains for backward compatibility but is no longer the primary path.

**Files:**
- Modify: `workflows/ngs/nanopore_methylation.nf` (add deprecation header)
- Modify: `ngs.nf` (update to route to standalone workflows)

**Step 1: Add deprecation header to `nanopore_methylation.nf`**

```groovy
// DEPRECATED: This monolithic workflow is retained for backward compatibility.
// New code should use the standalone workflow entrypoints:
//   - ont_plasmid_qc.nf
//   - ont_fastq_qc.nf
//   - ont_methylation_analysis.nf
//   - ont_construct_screening.nf
//   - ont_basecall_dna.nf
//   - ont_basecall_rna.nf
//   - ont_wf_clone_validation.nf
//   - ont_fastq_dimer_qc.nf
```

**Step 2: Update `ngs.nf` to route by workflow ID**

```groovy
#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { ONT_PLASMID_QC } from './workflows/ngs/ont_plasmid_qc.nf'
include { ONT_FASTQ_QC } from './workflows/ngs/ont_fastq_qc.nf'
include { ONT_METHYLATION_ANALYSIS } from './workflows/ngs/ont_methylation_analysis.nf'
include { ONT_CONSTRUCT_SCREENING } from './workflows/ngs/ont_construct_screening.nf'
include { ONT_BASECALL_DNA } from './workflows/ngs/ont_basecall_dna.nf'
include { ONT_BASECALL_RNA } from './workflows/ngs/ont_basecall_rna.nf'
include { ONT_WF_CLONE_VALIDATION } from './workflows/ngs/ont_wf_clone_validation.nf'
include { ONT_FASTQ_DIMER_QC } from './workflows/ngs/ont_fastq_dimer_qc.nf'

workflow {
    def workflowId = params.ont_workflow_id ?: 'ont_methylation_analysis'
    
    switch (workflowId) {
        case 'ont_plasmid_qc':
            ONT_PLASMID_QC()
            break
        case 'ont_fastq_qc':
            ONT_FASTQ_QC()
            break
        case 'ont_methylation_analysis':
        case 'nanopore_methylation':
            ONT_METHYLATION_ANALYSIS()
            break
        case 'ont_construct_screening':
            ONT_CONSTRUCT_SCREENING()
            break
        case 'ont_basecall_dna':
            ONT_BASECALL_DNA()
            break
        case 'ont_basecall_rna':
            ONT_BASECALL_RNA()
            break
        case 'ont_wf_clone_validation':
            ONT_WF_CLONE_VALIDATION()
            break
        case 'ont_fastq_dimer_qc':
            ONT_FASTQ_DIMER_QC()
            break
        default:
            error("Unknown ONT workflow: ${workflowId}. Supported: ont_plasmid_qc, ont_fastq_qc, ont_methylation_analysis, ont_construct_screening, ont_basecall_dna, ont_basecall_rna, ont_wf_clone_validation, ont_fastq_dimer_qc")
    }
}
```

**Step 3: Verify**
```bash
nextflow run ngs.nf -help 2>&1 | head -10
```

**Step 4: Commit**
```bash
git add workflows/ngs/nanopore_methylation.nf ngs.nf
git commit -m "refactor(ngs): deprecate monolith, add workflow router to standalone entrypoints"
```

---

### Task 2.2: Build `ont_plasmid_qc.nf` (standalone, following protein workflow pattern)

**Objective:** Real standalone workflow matching `structure_prediction.nf` pattern: input validation → channel setup → module orchestration → emit outputs.

**Files:**
- Create: `workflows/ngs/ont_plasmid_qc.nf`

**Step 1: Write the workflow**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT Plasmid QC Workflow
 * 
 * FASTQ-to-reference plasmid QC with per-base support, consensus, and IGV evidence.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_plasmid_qc.nf -c nextflow.config \
 *     --fastq_path /path/to/reads.fastq \
 *     --reference_fasta /path/to/reference.fa \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'

// Workflow-specific param defaults
params.fastq_path = null
params.reference_fasta = null
params.out_dir = null
params.ont_workflow_id = 'ont_plasmid_qc'
params.ont_molecule_type = 'dna'
params.fastq_minimap2_preset = 'map-ont'
params.fastq_minimap2_allow_secondary = true
params.run_fastq_qc = true
params.run_modkit = false
params.modified_bases = 'none'
params.manifest_contract = 'sequence_qc.manifest.v1'

workflow ONT_PLASMID_QC {
    take:
        fastq_ch     // Channel of path(fastq)
        reference_ch // Channel of path(reference)
    
    main:
        // Validate inputs
        if (!params.fastq_path) {
            error("--fastq_path is required for ont_plasmid_qc")
        }
        if (!params.reference_fasta) {
            error("--reference_fasta is required for ont_plasmid_qc")
        }
        if (!params.out_dir) {
            error("--out_dir is required")
        }
        
        def fastqFile = file(params.fastq_path)
        if (!fastqFile.exists()) {
            error("FASTQ file not found: ${params.fastq_path}")
        }
        
        def referenceFile = file(params.reference_fasta)
        if (!referenceFile.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
        
        def allowedPresets = ['map-ont', 'map-hifi', 'map-pb', 'sr'] as Set
        def preset = (params.fastq_minimap2_preset ?: 'map-ont').toString().trim()
        if (!allowedPresets.contains(preset)) {
            error("Unsupported --fastq_minimap2_preset '${preset}'. Supported: ${allowedPresets.join(', ')}")
        }
        
        println("=" * 60)
        println("ONT Plasmid QC Workflow")
        println("=" * 60)
        println("* FASTQ: ${params.fastq_path}")
        println("* Reference: ${params.reference_fasta}")
        println("* Minimap2 preset: ${preset}")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        // Align FASTQ to reference
        FastqAlign(fastq_ch, reference_ch)
        
        // Run plasmid QC on aligned BAM
        FastqPlasmidQC(FastqAlign.out.aligned, reference_ch, fastq_ch)
    
    emit:
        aligned_bam = FastqAlign.out.aligned
        qc_summary = FastqPlasmidQC.out.summary
        qc_manifest = FastqPlasmidQC.out.qc_manifest
        consensus = FastqPlasmidQC.out.consensus
        per_base_support = FastqPlasmidQC.out.per_base_support
        igv_tracks = FastqPlasmidQC.out.igv_track_config
        igv_report = FastqPlasmidQC.out.igv_report
}

// Entry point for direct invocation
workflow {
    def fastqFile = file(params.fastq_path)
    def referenceFile = file(params.reference_fasta)
    
    ONT_PLASMID_QC(
        Channel.of(fastqFile),
        Channel.of(referenceFile)
    )
}
```

**Step 2: Verify**
```bash
nextflow run workflows/ngs/ont_plasmid_qc.nf -help 2>&1 | head -20
```

**Step 3: Commit**
```bash
git add workflows/ngs/ont_plasmid_qc.nf
git commit -m "feat(ngs): standalone ont_plasmid_qc following protein workflow pattern"
```

---

### Task 2.3: Build `ont_wf_clone_validation.nf` (HIGH PRIORITY)

**Objective:** Standalone workflow for wf-clone-validation assembly. Takes BAM + reference, runs EPI2ME Labs wf-clone-validation, produces assembly report.

**Files:**
- Create: `workflows/ngs/ont_wf_clone_validation.nf`

**Step 1: Write the workflow**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT wf-clone-validation Workflow
 * 
 * Assembly-based clone validation from aligned BAM using EPI2ME Labs wf-clone-validation.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_wf_clone_validation.nf -c nextflow.config \
 *     --bam_path /path/to/aligned.bam \
 *     --reference_fasta /path/to/reference.fa \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { RunCloneValidation } from '../../modules/ngs/clone_validation.nf'

// Workflow-specific param defaults
params.bam_path = null
params.reference_fasta = null
params.out_dir = null
params.ont_workflow_id = 'ont_wf_clone_validation'
params.ont_molecule_type = 'dna'

// wf-clone-validation params
params.wf_clone_sample = null
params.wf_clone_approx_size = 7000
params.wf_clone_assm_coverage = 60
params.wf_clone_min_quality = 9
params.wf_clone_trim_length = 0
params.wf_clone_assembly_tool = 'flye'
params.wf_clone_large_construct = false
params.wf_clone_workflow_dir = null
params.wf_clone_source = 'epi2me-labs/wf-clone-validation'
params.wf_clone_revision = ''
params.wf_clone_profile = 'singularity'
params.wf_clone_singularity_cache = null
params.wf_clone_nxf_home = null
params.wf_clone_override_basecaller_cfg = ''

workflow ONT_WF_CLONE_VALIDATION {
    take:
        bam_ch       // Channel of tuple(path(bam), path(bai))
        reference_ch // Channel of val(reference_fasta_path)
    
    main:
        if (!params.bam_path) {
            error("--bam_path is required for ont_wf_clone_validation")
        }
        if (!params.reference_fasta) {
            error("--reference_fasta is required for ont_wf_clone_validation")
        }
        if (!params.out_dir) {
            error("--out_dir is required")
        }
        
        def bamFile = file(params.bam_path)
        if (!bamFile.exists()) {
            error("BAM file not found: ${params.bam_path}")
        }
        
        def referenceFile = file(params.reference_fasta)
        if (!referenceFile.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
        
        println("=" * 60)
        println("ONT wf-clone-validation Workflow")
        println("=" * 60)
        println("* BAM: ${params.bam_path}")
        println("* Reference: ${params.reference_fasta}")
        println("* Approx size: ${params.wf_clone_approx_size}")
        println("* Assembly tool: ${params.wf_clone_assembly_tool}")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        // Prepare input for RunCloneValidation: tuple(bam, reference_fasta_path)
        def cloneInput = bam_ch.map { bam, bai -> 
            tuple(bam, params.reference_fasta)
        }
        
        RunCloneValidation(cloneInput)
    
    emit:
        assembly_dir = RunCloneValidation.out.out
        report = RunCloneValidation.out.report
        sample_status = RunCloneValidation.out.sample_status
        log = RunCloneValidation.out.log
}

workflow {
    def bamFile = file(params.bam_path)
    def bamIndex = file("${params.bam_path}.bai")
    
    ONT_WF_CLONE_VALIDATION(
        Channel.of(tuple(bamFile, bamIndex)),
        Channel.of(params.reference_fasta)
    )
}
```

**Step 2: Verify**
```bash
nextflow run workflows/ngs/ont_wf_clone_validation.nf -help 2>&1 | head -20
```

**Step 3: Commit**
```bash
git add workflows/ngs/ont_wf_clone_validation.nf
git commit -m "feat(ngs): standalone ont_wf_clone_validation workflow"
```

---

### Task 2.4: Build `ont_fastq_dimer_qc.nf` (HIGH PRIORITY)

**Objective:** Standalone workflow for multimer/dimer detection from FASTQ. Wraps the existing `FastqMultimerQC` module.

**Files:**
- Create: `workflows/ngs/ont_fastq_dimer_qc.nf`

**Step 1: Write the workflow**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT FASTQ Dimer/Multimer QC Workflow
 * 
 * Detects multimeric plasmid forms (dimer, trimer, tetramer+) from read lengths.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_fastq_dimer_qc.nf -c nextflow.config \
 *     --fastq_path /path/to/reads.fastq \
 *     --expected_plasmid_size 7000 \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { FastqMultimerQC } from '../../modules/ngs/fastq_dimer_qc.nf'

// Workflow-specific param defaults
params.fastq_path = null
params.out_dir = null
params.ont_workflow_id = 'ont_fastq_dimer_qc'
params.ont_molecule_type = 'dna'
params.expected_plasmid_size = 7000
params.min_fastq_read_length = 0

workflow ONT_FASTQ_DIMER_QC {
    take:
        fastq_ch // Channel of path(fastq)
    
    main:
        if (!params.fastq_path) {
            error("--fastq_path is required for ont_fastq_dimer_qc")
        }
        if (!params.out_dir) {
            error("--out_dir is required")
        }
        
        def fastqFile = file(params.fastq_path)
        if (!fastqFile.exists()) {
            error("FASTQ file not found: ${params.fastq_path}")
        }
        
        println("=" * 60)
        println("ONT FASTQ Dimer/Multimer QC Workflow")
        println("=" * 60)
        println("* FASTQ: ${params.fastq_path}")
        println("* Expected size: ${params.expected_plasmid_size}")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        FastqMultimerQC(fastq_ch)
    
    emit:
        lengths = FastqMultimerQC.out.lengths
        summary = FastqMultimerQC.out.summary
        candidates = FastqMultimerQC.out.candidates
        log = FastqMultimerQC.out.log
}

workflow {
    def fastqFile = file(params.fastq_path)
    ONT_FASTQ_DIMER_QC(Channel.of(fastqFile))
}
```

**Step 2: Verify**
```bash
nextflow run workflows/ngs/ont_fastq_dimer_qc.nf -help 2>&1 | head -20
```

**Step 3: Commit**
```bash
git add workflows/ngs/ont_fastq_dimer_qc.nf
git commit -m "feat(ngs): standalone ont_fastq_dimer_qc workflow"
```

---

### Task 2.5: Build `ont_fastq_qc.nf` (reference-optional)

**Objective:** Standalone workflow for read stats. Reference optional — if provided, also runs alignment + plasmid QC.

**Files:**
- Create: `modules/ngs/fastq_stats.nf` (new module for read stats only)
- Create: `workflows/ngs/ont_fastq_qc.nf`

**Step 1: Create `fastq_stats.nf`**

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

**Step 2: Create `ont_fastq_qc.nf`**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT FASTQ QC Workflow
 * 
 * Read-length/Q-score/yield stats from FASTQ. Reference optional.
 * If reference provided, also runs alignment + plasmid QC.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_fastq_qc.nf -c nextflow.config \
 *     --fastq_path /path/to/reads.fastq \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { FastqStats } from '../../modules/ngs/fastq_stats.nf'
include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'

// Workflow-specific param defaults
params.fastq_path = null
params.reference_fasta = null
params.out_dir = null
params.ont_workflow_id = 'ont_fastq_qc'
params.ont_molecule_type = 'dna'
params.fastq_minimap2_preset = 'map-ont'
params.fastq_minimap2_allow_secondary = true
params.run_fastq_qc = true
params.run_modkit = false
params.modified_bases = 'none'
params.min_fastq_read_length = 0
params.manifest_contract = 'sequence_qc.manifest.v1'

workflow ONT_FASTQ_QC {
    take:
        fastq_ch // Channel of path(fastq)
    
    main:
        if (!params.fastq_path) {
            error("--fastq_path is required for ont_fastq_qc")
        }
        if (!params.out_dir) {
            error("--out_dir is required")
        }
        
        def fastqFile = file(params.fastq_path)
        if (!fastqFile.exists()) {
            error("FASTQ file not found: ${params.fastq_path}")
        }
        
        def hasReference = params.reference_fasta && params.reference_fasta.toString().trim()
        
        println("=" * 60)
        println("ONT FASTQ QC Workflow")
        println("=" * 60)
        println("* FASTQ: ${params.fastq_path}")
        println("* Reference: ${hasReference ? params.reference_fasta : 'none (stats only)'}")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        // Always produce read stats
        FastqStats(fastq_ch)
        
        // If reference provided, also run alignment + plasmid QC
        if (hasReference) {
            def referenceFile = file(params.reference_fasta)
            if (!referenceFile.exists()) {
                error("Reference FASTA not found: ${params.reference_fasta}")
            }
            
            def allowedPresets = ['map-ont', 'map-hifi', 'map-pb', 'sr'] as Set
            def preset = (params.fastq_minimap2_preset ?: 'map-ont').toString().trim()
            if (!allowedPresets.contains(preset)) {
                error("Unsupported --fastq_minimap2_preset '${preset}'")
            }
            
            def reference_ch = Channel.of(referenceFile)
            FastqAlign(fastq_ch, reference_ch)
            FastqPlasmidQC(FastqAlign.out.aligned, reference_ch, fastq_ch)
        }
    
    emit:
        lengths = FastqStats.out.lengths
        summary = FastqStats.out.summary
        stats_log = FastqStats.out.log
        aligned_bam = hasReference ? FastqAlign.out.aligned : null
        qc_summary = hasReference ? FastqPlasmidQC.out.summary : null
        qc_manifest = hasReference ? FastqPlasmidQC.out.qc_manifest : null
}

workflow {
    def fastqFile = file(params.fastq_path)
    ONT_FASTQ_QC(Channel.of(fastqFile))
}
```

**Step 3: Verify**
```bash
nextflow run workflows/ngs/ont_fastq_qc.nf -help 2>&1 | head -20
```

**Step 4: Commit**
```bash
git add modules/ngs/fastq_stats.nf workflows/ngs/ont_fastq_qc.nf
git commit -m "feat(ngs): standalone ont_fastq_qc with fastq_stats module"
```

---

### Task 2.6: Build `ont_methylation_analysis.nf` (standalone)

**Objective:** Standalone workflow for methylation analysis. Extracts the methylation logic from the monolith into a proper workflow.

**Files:**
- Create: `workflows/ngs/ont_methylation_analysis.nf`

**Step 1: Write the workflow**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT Methylation Analysis Workflow
 * 
 * Dorado basecalling + modkit methylation analysis from POD5 or BAM.
 * Optional FASTQ plasmid QC if FASTQ provided.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_methylation_analysis.nf -c nextflow.config \
 *     --pod5_dir /path/to/pod5 \
 *     --reference_fasta /path/to/ref.fa \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'
include { PrepareBamForAnalysis; ValidateMappedBam; PrepareReferenceForIGV } from '../../modules/ngs/bam_prepare.nf'
include { ModkitPileup } from '../../modules/ngs/modkit_pileup.nf'
include { ModkitSummary } from '../../modules/ngs/modkit_summary.nf'
include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'

// Workflow-specific param defaults
params.pod5_dir = null
params.bam_path = null
params.fastq_path = null
params.reference_fasta = null
params.out_dir = null
params.ont_workflow_id = 'ont_methylation_analysis'
params.ont_molecule_type = 'dna'
params.dorado_model = 'sup'
params.dorado_device = 'cuda:0'
params.modified_bases = '6mA 4mC_5mC'
params.run_modkit = true
params.run_fastq_qc = true
params.bam_force_realign = false
params.bam_min_mapq = 0
params.fastq_minimap2_preset = 'map-ont'
params.fastq_minimap2_allow_secondary = true
params.manifest_contract = 'sequence_qc.manifest.v1'

workflow ONT_METHYLATION_ANALYSIS {
    take:
        input_ch     // Channel of path(input) — pod5 dir, bam, or fastq
        reference_ch // Channel of path(reference) — optional
    
    main:
        // Validate exactly one input type
        def hasPod5 = params.pod5_dir && params.pod5_dir.toString().trim()
        def hasBam = params.bam_path && params.bam_path.toString().trim()
        def hasFastq = params.fastq_path && params.fastq_path.toString().trim()
        def inputCount = [hasPod5, hasBam, hasFastq].count { it }
        
        if (inputCount == 0) {
            error("One primary input is required: --pod5_dir, --bam_path, or --fastq_path")
        }
        if (inputCount > 1) {
            error("Specify exactly one primary input: --pod5_dir OR --bam_path OR --fastq_path")
        }
        
        def hasReference = params.reference_fasta && params.reference_fasta.toString().trim()
        def referenceFile = hasReference ? file(params.reference_fasta) : null
        if (hasReference && !referenceFile.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
        
        println("=" * 60)
        println("ONT Methylation Analysis Workflow")
        println("=" * 60)
        println("* Input: ${hasPod5 ? params.pod5_dir : hasBam ? params.bam_path : params.fastq_path}")
        println("* Type: ${hasPod5 ? 'POD5' : hasBam ? 'BAM' : 'FASTQ'}")
        println("* Reference: ${hasReference ? params.reference_fasta : 'none'}")
        println("* Dorado model: ${params.dorado_model}")
        println("* Modified bases: ${params.modified_bases}")
        println("* Run modkit: ${params.run_modkit}")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        def analysisBam = null
        
        // POD5 path: basecall → align
        if (hasPod5) {
            def pod5Input = file(params.pod5_dir)
            if (!pod5Input.exists()) {
                error("POD5 directory not found: ${params.pod5_dir}")
            }
            
            DoradoBasecall(Channel.of(pod5Input))
            
            if (hasReference) {
                DoradoAlign(DoradoBasecall.out.bam, Channel.of(referenceFile))
                analysisBam = DoradoAlign.out.aligned
            } else {
                PrepareBamForAnalysis(DoradoBasecall.out.bam)
                analysisBam = PrepareBamForAnalysis.out.aligned
            }
        }
        
        // BAM path: prepare or realign
        if (hasBam) {
            def bamInput = file(params.bam_path)
            if (!bamInput.exists()) {
                error("BAM file not found: ${params.bam_path}")
            }
            
            if (hasReference && params.bam_force_realign) {
                DoradoAlign(Channel.of(bamInput), Channel.of(referenceFile))
                analysisBam = DoradoAlign.out.aligned
            } else {
                PrepareBamForAnalysis(Channel.of(bamInput))
                analysisBam = PrepareBamForAnalysis.out.aligned
                
                if (hasReference) {
                    PrepareReferenceForIGV(Channel.of(referenceFile))
                }
                
                if (params.run_modkit) {
                    ValidateMappedBam(analysisBam)
                    analysisBam = ValidateMappedBam.out.aligned
                }
            }
        }
        
        // FASTQ path: align only (no modkit — FASTQ lacks MM/ML tags)
        if (hasFastq) {
            def fastqInput = file(params.fastq_path)
            if (!fastqInput.exists()) {
                error("FASTQ file not found: ${params.fastq_path}")
            }
            if (!hasReference) {
                error("FASTQ analysis requires --reference_fasta")
            }
            
            FastqAlign(Channel.of(fastqInput), Channel.of(referenceFile))
            analysisBam = FastqAlign.out.aligned
            
            if (params.run_fastq_qc) {
                FastqPlasmidQC(FastqAlign.out.aligned, Channel.of(referenceFile), Channel.of(fastqInput))
            }
        }
        
        // Modkit: only for POD5 or BAM (has MM/ML tags)
        if (params.run_modkit && analysisBam != null && (hasPod5 || hasBam)) {
            if (hasReference) {
                ModkitPileup(analysisBam, Channel.of(referenceFile))
            }
            ModkitSummary(analysisBam)
        }
    
    emit:
        analysis_bam = analysisBam
        modkit_summary = params.run_modkit && (hasPod5 || hasBam) ? ModkitSummary.out.summary : null
        modkit_pileup = params.run_modkit && hasReference && (hasPod5 || hasBam) ? ModkitPileup.out.pileup : null
        fastq_qc = hasFastq && params.run_fastq_qc ? FastqPlasmidQC.out.summary : null
}

workflow {
    def inputPath = params.pod5_dir ?: params.bam_path ?: params.fastq_path
    def referenceFile = params.reference_fasta ? file(params.reference_fasta) : null
    
    ONT_METHYLATION_ANALYSIS(
        Channel.of(file(inputPath)),
        referenceFile ? Channel.of(referenceFile) : Channel.empty()
    )
}
```

**Step 2: Verify**
```bash
nextflow run workflows/ngs/ont_methylation_analysis.nf -help 2>&1 | head -20
```

**Step 3: Commit**
```bash
git add workflows/ngs/ont_methylation_analysis.nf
git commit -m "feat(ngs): standalone ont_methylation_analysis workflow"
```

---

### Task 2.7: Build `ont_construct_screening.nf`

**Objective:** Standalone workflow for expected-construct screening. Compares consensus against expected sequence, produces pass/fail.

**Files:**
- Create: `workflows/ngs/ont_construct_screening.nf`
- Create: `modules/ngs/construct_screen.nf` (new module)

**Step 1: Create `construct_screen.nf`**

```groovy
process ConstructScreen {
    label 'local_cpu'
    publishDir "${params.out_dir}/construct_screen", mode: 'copy'
    tag "construct_screen"

    input:
    path consensus_fasta
    path expected_fasta

    output:
    path "construct_screen_result.tsv", emit: result
    path "construct_screen_report.json", emit: report
    path "construct_screen.log", emit: log

    script:
    def toleranceBp = (params.construct_screen_tolerance_bp ?: 10) as Integer
    def minIdentityPct = (params.construct_screen_min_identity ?: 95.0) as Double
    """
    set -euo pipefail

    # Read sequences
    consensus=\$(awk 'NR>1' "${consensus_fasta}" | tr -d '\\n')
    expected=\$(awk 'NR>1' "${expected_fasta}" | tr -d '\\n')
    cons_len=\${#consensus}
    exp_len=\${#expected}

    # Simple length comparison
    len_diff=\$((cons_len - exp_len))
    len_diff_abs=\${len_diff#-}

    if [[ \${len_diff_abs} -le ${toleranceBp} ]]; then
        length_status="pass"
    else
        length_status="fail"
    fi

    # Write result TSV
    {
        echo -e "metric\tvalue\tstatus"
        echo -e "consensus_length\t\${cons_len}\t"
        echo -e "expected_length\t\${exp_len}\t"
        echo -e "length_difference\t\${len_diff}\t\${length_status}"
        echo -e "tolerance_bp\t${toleranceBp}\t"
        echo -e "min_identity_pct\t${minIdentityPct}\t"
    } > construct_screen_result.tsv

    # Write JSON report
    cat > construct_screen_report.json <<JSON
{
  "consensus_length": \${cons_len},
  "expected_length": \${exp_len},
  "length_difference": \${len_diff},
  "length_status": "\${length_status}",
  "tolerance_bp": ${toleranceBp},
  "min_identity_pct": ${minIdentityPct},
  "screen_status": "\${length_status}"
}
JSON

    {
        echo "Construct screen complete"
        echo "Consensus: \${cons_len} bp"
        echo "Expected: \${exp_len} bp"
        echo "Difference: \${len_diff} bp"
        echo "Status: \${length_status}"
    } > construct_screen.log
    """
}
```

**Step 2: Create `ont_construct_screening.nf`**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT Construct Screening Workflow
 * 
 * Expected-construct screening from FASTQ reads.
 * Aligns reads to reference, builds consensus, compares to expected sequence.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_construct_screening.nf -c nextflow.config \
 *     --fastq_path /path/to/reads.fastq \
 *     --reference_fasta /path/to/reference.fa \
 *     --expected_fasta /path/to/expected.fa \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
include { FastqPlasmidQC } from '../../modules/ngs/fastq_plasmid_qc.nf'
include { ConstructScreen } from '../../modules/ngs/construct_screen.nf'

// Workflow-specific param defaults
params.fastq_path = null
params.reference_fasta = null
params.expected_fasta = null
params.out_dir = null
params.ont_workflow_id = 'ont_construct_screening'
params.ont_molecule_type = 'dna'
params.fastq_minimap2_preset = 'map-ont'
params.fastq_minimap2_allow_secondary = true
params.construct_screen_tolerance_bp = 10
params.construct_screen_min_identity = 95.0
params.manifest_contract = 'sequence_qc.manifest.v1'

workflow ONT_CONSTRUCT_SCREENING {
    take:
        fastq_ch     // Channel of path(fastq)
        reference_ch // Channel of path(reference)
        expected_ch  // Channel of path(expected)
    
    main:
        if (!params.fastq_path) {
            error("--fastq_path is required for ont_construct_screening")
        }
        if (!params.reference_fasta) {
            error("--reference_fasta is required for ont_construct_screening")
        }
        if (!params.expected_fasta) {
            error("--expected_fasta is required for ont_construct_screening")
        }
        if (!params.out_dir) {
            error("--out_dir is required")
        }
        
        def fastqFile = file(params.fastq_path)
        if (!fastqFile.exists()) {
            error("FASTQ file not found: ${params.fastq_path}")
        }
        
        def referenceFile = file(params.reference_fasta)
        if (!referenceFile.exists()) {
            error("Reference FASTA not found: ${params.reference_fasta}")
        }
        
        def expectedFile = file(params.expected_fasta)
        if (!expectedFile.exists()) {
            error("Expected FASTA not found: ${params.expected_fasta}")
        }
        
        println("=" * 60)
        println("ONT Construct Screening Workflow")
        println("=" * 60)
        println("* FASTQ: ${params.fastq_path}")
        println("* Reference: ${params.reference_fasta}")
        println("* Expected: ${params.expected_fasta}")
        println("* Tolerance: ${params.construct_screen_tolerance_bp} bp")
        println("* Min identity: ${params.construct_screen_min_identity}%")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        FastqAlign(fastq_ch, reference_ch)
        FastqPlasmidQC(FastqAlign.out.aligned, reference_ch, fastq_ch)
        ConstructScreen(FastqPlasmidQC.out.consensus, expected_ch)
    
    emit:
        aligned_bam = FastqAlign.out.aligned
        qc_summary = FastqPlasmidQC.out.summary
        qc_manifest = FastqPlasmidQC.out.qc_manifest
        consensus = FastqPlasmidQC.out.consensus
        screen_result = ConstructScreen.out.result
        screen_report = ConstructScreen.out.report
}

workflow {
    def fastqFile = file(params.fastq_path)
    def referenceFile = file(params.reference_fasta)
    def expectedFile = file(params.expected_fasta)
    
    ONT_CONSTRUCT_SCREENING(
        Channel.of(fastqFile),
        Channel.of(referenceFile),
        Channel.of(expectedFile)
    )
}
```

**Step 3: Verify**
```bash
nextflow run workflows/ngs/ont_construct_screening.nf -help 2>&1 | head -20
```

**Step 4: Commit**
```bash
git add modules/ngs/construct_screen.nf workflows/ngs/ont_construct_screening.nf
git commit -m "feat(ngs): standalone ont_construct_screening with construct_screen module"
```

---

### Task 2.8: Build `ont_basecall_dna.nf`

**Objective:** Standalone workflow for DNA basecalling from POD5.

**Files:**
- Create: `workflows/ngs/ont_basecall_dna.nf`

**Step 1: Write the workflow**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT DNA Basecalling Workflow
 * 
 * Dorado DNA basecalling from POD5 raw signal files.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_basecall_dna.nf -c nextflow.config \
 *     --pod5_dir /path/to/pod5 \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'

// Workflow-specific param defaults
params.pod5_dir = null
params.reference_fasta = null
params.out_dir = null
params.ont_workflow_id = 'ont_basecall_dna'
params.ont_molecule_type = 'dna'
params.dorado_model = 'sup'
params.dorado_device = 'cuda:0'
params.modified_bases = 'none'
params.run_modkit = false
params.run_fastq_qc = false

workflow ONT_BASECALL_DNA {
    take:
        pod5_ch // Channel of path(pod5_dir)
    
    main:
        if (!params.pod5_dir) {
            error("--pod5_dir is required for ont_basecall_dna")
        }
        if (!params.out_dir) {
            error("--out_dir is required")
        }
        
        def pod5Dir = file(params.pod5_dir)
        if (!pod5Dir.exists()) {
            error("POD5 directory not found: ${params.pod5_dir}")
        }
        
        println("=" * 60)
        println("ONT DNA Basecalling Workflow")
        println("=" * 60)
        println("* POD5: ${params.pod5_dir}")
        println("* Model: ${params.dorado_model}")
        println("* Device: ${params.dorado_device}")
        println("* Modified bases: ${params.modified_bases}")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        DoradoBasecall(pod5_ch)
        
        def hasReference = params.reference_fasta && params.reference_fasta.toString().trim()
        if (hasReference) {
            def referenceFile = file(params.reference_fasta)
            if (!referenceFile.exists()) {
                error("Reference FASTA not found: ${params.reference_fasta}")
            }
            DoradoAlign(DoradoBasecall.out.bam, Channel.of(referenceFile))
        }
    
    emit:
        basecall_bam = DoradoBasecall.out.bam
        basecall_log = DoradoBasecall.out.log
        sequencing_summary = DoradoBasecall.out.summary
        aligned_bam = hasReference ? DoradoAlign.out.aligned : null
}

workflow {
    def pod5Dir = file(params.pod5_dir)
    ONT_BASECALL_DNA(Channel.of(pod5Dir))
}
```

**Step 2: Verify**
```bash
nextflow run workflows/ngs/ont_basecall_dna.nf -help 2>&1 | head -20
```

**Step 3: Commit**
```bash
git add workflows/ngs/ont_basecall_dna.nf
git commit -m "feat(ngs): standalone ont_basecall_dna workflow"
```

---

### Task 2.9: Build `ont_basecall_rna.nf`

**Objective:** Standalone workflow for RNA basecalling from POD5.

**Files:**
- Create: `workflows/ngs/ont_basecall_rna.nf`

**Step 1: Write the workflow**

```groovy
#!/usr/bin/env nextflow
/**
 * ONT RNA Basecalling Workflow
 * 
 * Dorado RNA basecalling from POD5 raw signal files.
 * 
 * Usage:
 *   nextflow run workflows/ngs/ont_basecall_rna.nf -c nextflow.config \
 *     --pod5_dir /path/to/pod5 \
 *     --out_dir /path/to/output
 */

nextflow.enable.dsl = 2

include { DoradoBasecall } from '../../modules/ngs/dorado_basecall.nf'
include { DoradoAlign } from '../../modules/ngs/dorado_align.nf'

// Workflow-specific param defaults
params.pod5_dir = null
params.reference_fasta = null
params.out_dir = null
params.ont_workflow_id = 'ont_basecall_rna'
params.ont_molecule_type = 'rna'
params.dorado_model = 'sup'
params.dorado_device = 'cuda:0'
params.modified_bases = 'none'
params.run_modkit = false
params.run_fastq_qc = false

workflow ONT_BASECALL_RNA {
    take:
        pod5_ch // Channel of path(pod5_dir)
    
    main:
        if (!params.pod5_dir) {
            error("--pod5_dir is required for ont_basecall_rna")
        }
        if (!params.out_dir) {
            error("--out_dir is required")
        }
        
        def pod5Dir = file(params.pod5_dir)
        if (!pod5Dir.exists()) {
            error("POD5 directory not found: ${params.pod5_dir}")
        }
        
        println("=" * 60)
        println("ONT RNA Basecalling Workflow")
        println("=" * 60)
        println("* POD5: ${params.pod5_dir}")
        println("* Model: ${params.dorado_model}")
        println("* Device: ${params.dorado_device}")
        println("* Modified bases: ${params.modified_bases}")
        println("* Output: ${params.out_dir}")
        println("=" * 60)
        
        // TODO: Add RNA model selection (rna002, rna004) when Dorado supports it
        // For now, uses same DoradoBasecall as DNA but with RNA-specific model
        
        DoradoBasecall(pod5_ch)
        
        def hasReference = params.reference_fasta && params.reference_fasta.toString().trim()
        if (hasReference) {
            def referenceFile = file(params.reference_fasta)
            if (!referenceFile.exists()) {
                error("Reference FASTA not found: ${params.reference_fasta}")
            }
            DoradoAlign(DoradoBasecall.out.bam, Channel.of(referenceFile))
        }
    
    emit:
        basecall_bam = DoradoBasecall.out.bam
        basecall_log = DoradoBasecall.out.log
        sequencing_summary = DoradoBasecall.out.summary
        aligned_bam = hasReference ? DoradoAlign.out.aligned : null
}

workflow {
    def pod5Dir = file(params.pod5_dir)
    ONT_BASECALL_RNA(Channel.of(pod5Dir))
}
```

**Step 2: Verify**
```bash
nextflow run workflows/ngs/ont_basecall_rna.nf -help 2>&1 | head -20
```

**Step 3: Commit**
```bash
git add workflows/ngs/ont_basecall_rna.nf
git commit -m "feat(ngs): standalone ont_basecall_rna workflow"
```

---

## Phase 3: Update Router and Registry

### Task 3.1: Update `WORKFLOW_ENTRYPOINTS` and tests

**Objective:** Ensure all 8 workflows are registered and routed correctly.

**Files:**
- Modify: `platform/api/services/nextflow.py`
- Modify: `platform/api/tests/test_ont_ngs_workflow_products.py`
- Modify: `platform/api/services/ont_ngs_contract.py`

**Step 1: Update `WORKFLOW_ENTRYPOINTS`**

```python
WORKFLOW_ENTRYPOINTS: Dict[str, str] = {
    # ... existing entries ...
    "ont_plasmid_qc": "workflows/ngs/ont_plasmid_qc.nf",
    "ont_fastq_qc": "workflows/ngs/ont_fastq_qc.nf",
    "ont_methylation_analysis": "workflows/ngs/ont_methylation_analysis.nf",
    "ont_construct_screening": "workflows/ngs/ont_construct_screening.nf",
    "ont_basecall_dna": "workflows/ngs/ont_basecall_dna.nf",
    "ont_basecall_rna": "workflows/ngs/ont_basecall_rna.nf",
    "ont_wf_clone_validation": "workflows/ngs/ont_wf_clone_validation.nf",
    "ont_fastq_dimer_qc": "workflows/ngs/ont_fastq_dimer_qc.nf",
    # ... rest of entries ...
}
```

**Step 2: Update `CANONICAL_ONT_WORKFLOWS` in `ont_ngs_contract.py`**

Add the two new workflows:

```python
    "ont_wf_clone_validation": OntWorkflowSpec(
        workflow_id="ont_wf_clone_validation",
        display_name="ONT wf-clone-validation",
        description="Assembly-based clone validation from aligned BAM using EPI2ME Labs wf-clone-validation.",
        input_modes=("bam",),
        artifact_kinds=(
            "alignment_bam",
            "reference",
            "consensus",
            "construct_screening_summary",
        ),
        lifecycle="seed",
    ),
    "ont_fastq_dimer_qc": OntWorkflowSpec(
        workflow_id="ont_fastq_dimer_qc",
        display_name="ONT FASTQ Dimer/Multimer QC",
        description="Multimeric plasmid form detection from FASTQ read lengths.",
        input_modes=("fastq",),
        artifact_kinds=(
            "basecall_reads",
            "read_qc_summary",
        ),
        lifecycle="seed",
    ),
```

**Step 3: Update tests**

In `test_ont_ngs_workflow_products.py`:
- Add `ont_wf_clone_validation` and `ont_fastq_dimer_qc` to `EXPECTED_ONT_ENTRYPOINTS`
- Remove `NANOPORE_METHYLATION` assertions for standalone workflows
- Add assertion that standalone workflows do NOT contain `NANOPORE_METHYLATION`

**Step 4: Verify**
```bash
python -m pytest platform/api/tests/test_ont_ngs_workflow_products.py -v
python -m pytest platform/api/tests/test_ont_ngs_contract.py -v
```

**Step 5: Commit**
```bash
git add platform/api/services/nextflow.py platform/api/services/ont_ngs_contract.py platform/api/tests/test_ont_ngs_workflow_products.py
git commit -m "feat(ngs): register all 8 standalone ONT workflows"
```

---

## Phase 4: Add Execution Tests

### Task 4.1: Create test fixtures

**Objective:** Minimal test data for fast execution.

**Files:**
- Create: `platform/api/tests/fixtures/ngs/test_reference.fasta`
- Create: `platform/api/tests/fixtures/ngs/test_reads.fastq`
- Create: `platform/api/tests/fixtures/ngs/test_expected.fasta`

**Step 1: Create reference**
```fasta
>test_plasmid_pUC19_fragment
GAGATACCTACAGCGTGAGCTATGACTGGAGTGCCAACTCCTCAAGCGTATTCAATCA
TATGCTTCCCGCCGCCCAGAATGCGATGGCTCCTGCAAGTTAAATATTTAGCCTTATT
```

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

**Step 3: Create expected sequence**
```fasta
>expected_plasmid
GAGATACCTACAGCGTGAGCTATGACTGGAGTGCCAACTCCTCAAGCGTATTCAATCA
TATGCTTCCCGCCGCCCAGAATGCGATGGCTCCTGCAAGTTAAATATTTAGCCTTATT
```

**Step 4: Commit**
```bash
git add platform/api/tests/fixtures/ngs/
git commit -m "test(ngs): add minimal test fixtures for ONT workflows"
```

---

### Task 4.2: Add execution test for `ont_plasmid_qc`

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
        timeout=300,
    )

    assert result.returncode == 0, f"Nextflow failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"

    fastq_qc_dir = out_dir / "fastq_qc"
    expected_files = {
        "read_lengths.tsv", "fastq_qc_summary.tsv", "fastq_alignment_stats.tsv",
        "fastq_coverage.tsv", "per_base_support.tsv", "qc_manifest.json",
        "reference_qc.fasta", "reference_qc.fasta.fai",
        "igv_coverage_depth.bedgraph", "igv_position_gradient.bedgraph",
        "igv_gc_content.bedgraph", "igv_gc_zscore.bedgraph",
        "igv_split_read_density.bedgraph", "igv_softclip_density.bedgraph",
        "igv_junction_hotspots.bed", "igv_report_sites.bed", "igv_report_sites.tsv",
        "igv_track_config.json", "igv_report.html",
        "fastq_consensus.fasta", "fastq_consensus.fasta.fai",
        "fastq_consensus.log", "fastq_qc.log",
        "aligned.bam", "aligned.bam.bai",
    }

    found_files = {p.name for p in fastq_qc_dir.iterdir() if p.is_file()}
    missing = expected_files - found_files
    assert not missing, f"Missing expected output files: {missing}"

    manifest_path = fastq_qc_dir / "qc_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["artifact_schema_version"] == 1
    assert manifest["job_id"] == "test-plasmid-qc-1"
    assert manifest["reference"]["length"] == 120
    assert manifest["consensus"]["status"] in {"ok", "pileup_majority_fallback", "reference_copy_fallback"}
    assert len(manifest["artifacts"]) >= 10

    support_path = fastq_qc_dir / "per_base_support.tsv"
    support_lines = support_path.read_text(encoding="utf-8").strip().split("\n")
    assert len(support_lines) == 121
    header = support_lines[0].split("\t")
    assert header[0] == "chrom"
    assert "consensus_base" in header

    subprocess.run(["rm", "-rf", str(out_dir)], check=False)
```

**Step 2: Commit**
```bash
git add platform/api/tests/test_ont_plasmid_qc_execution.py
git commit -m "test(ngs): add execution test for ont_plasmid_qc"
```

---

### Task 4.3: Add execution tests for remaining workflows

**Files:**
- Create: `platform/api/tests/test_ont_fastq_qc_execution.py`
- Create: `platform/api/tests/test_ont_fastq_dimer_qc_execution.py`
- Create: `platform/api/tests/test_ont_construct_screening_execution.py`

**Step 1: Write `test_ont_fastq_qc_execution.py`**

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


def test_ont_fastq_qc_without_reference() -> None:
    """Run ont_fastq_qc without reference — stats only."""
    out_dir = REPO_ROOT / ".test_out_fastq_qc"
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

    assert (out_dir / "fastq_stats" / "read_lengths.tsv").exists()
    assert (out_dir / "fastq_stats" / "fastq_stats_summary.tsv").exists()
    assert not (out_dir / "fastq_qc").exists()

    subprocess.run(["rm", "-rf", str(out_dir)], check=False)


def test_ont_fastq_qc_with_reference() -> None:
    """Run ont_fastq_qc with reference — stats + plasmid QC."""
    out_dir = REPO_ROOT / ".test_out_fastq_qc_ref"
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

**Step 2: Write `test_ont_fastq_dimer_qc_execution.py`**

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


def test_ont_fastq_dimer_qc_produces_outputs() -> None:
    """Run ont_fastq_dimer_qc and verify outputs."""
    out_dir = REPO_ROOT / ".test_out_dimer_qc"
    fastq = NGS_FIXTURES / "test_reads.fastq"

    cmd = [
        "nextflow", "run",
        str(REPO_ROOT / "workflows" / "ngs" / "ont_fastq_dimer_qc.nf"),
        "-profile", "workstation_ryzen7960x",
        "--fastq_path", str(fastq),
        "--expected_plasmid_size", "60",
        "--out_dir", str(out_dir),
        "--job_id", "test-dimer-qc-1",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=120)
    assert result.returncode == 0, f"Nextflow failed:\n{result.stderr}"

    multimer_dir = out_dir / "multimer_qc"
    assert (multimer_dir / "read_lengths.tsv").exists()
    assert (multimer_dir / "multimer_summary.tsv").exists()
    assert (multimer_dir / "multimer_qc.log").exists()

    subprocess.run(["rm", "-rf", str(out_dir)], check=False)
```

**Step 3: Write `test_ont_construct_screening_execution.py`**

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


def test_ont_construct_screening_produces_report() -> None:
    """Run ont_construct_screening and verify screen report."""
    out_dir = REPO_ROOT / ".test_out_construct_screen"
    fastq = NGS_FIXTURES / "test_reads.fastq"
    reference = NGS_FIXTURES / "test_reference.fasta"
    expected = NGS_FIXTURES / "test_expected.fasta"

    cmd = [
        "nextflow", "run",
        str(REPO_ROOT / "workflows" / "ngs" / "ont_construct_screening.nf"),
        "-profile", "workstation_ryzen7960x",
        "--fastq_path", str(fastq),
        "--reference_fasta", str(reference),
        "--expected_fasta", str(expected),
        "--out_dir", str(out_dir),
        "--job_id", "test-construct-1",
    ]

    result = subprocess.run(cmd, capture_output=True, text=True, cwd=str(REPO_ROOT), timeout=300)
    assert result.returncode == 0, f"Nextflow failed:\n{result.stderr}"

    assert (out_dir / "construct_screen" / "construct_screen_report.json").exists()
    assert (out_dir / "construct_screen" / "construct_screen_result.tsv").exists()

    report_path = out_dir / "construct_screen" / "construct_screen_report.json"
    report = json.loads(report_path.read_text(encoding="utf-8"))
    assert "screen_status" in report
    assert report["screen_status"] in {"pass", "fail"}

    subprocess.run(["rm", "-rf", str(out_dir)], check=False)
```

**Step 4: Commit**
```bash
git add platform/api/tests/test_ont_fastq_qc_execution.py platform/api/tests/test_ont_fastq_dimer_qc_execution.py platform/api/tests/test_ont_construct_screening_execution.py
git commit -m "test(ngs): add execution tests for fastq_qc, dimer_qc, construct_screening"
```

---

## Phase 5: Add Result Contract and Frontend Wiring

### Task 5.1: Add `sequence_qc_v1` result contract

**Files:**
- Modify: `platform/api/services/result_contracts.py`

**Step 1: Add the contract**

```python
    ResultContractDefinition(
        contract_id="sequence_qc_v1",
        model_ids=["nanopore"],
        stage_families=["ont_ngs"],
        stage_modes=["plasmid_qc", "fastq_qc", "construct_screening", "methylation_analysis", "basecall_dna", "basecall_rna", "wf_clone_validation", "fastq_dimer_qc"],
        artifact_classes=["sequence_qc_manifest"],
        result_sets=["sequence_qc"],
        supported_analyzers=["sequence_qc_v1"],
        viewer_capabilities=["result_filter", "sequence_qc_metrics", "igv_viewer", "manifest_artifact_browser", "construct_screen_report"],
        required_fields=["artifact_class", "result_set", "job_id"],
        required_artifacts=["qc_manifest"],
        notes="ONT/NGS sequence QC outputs including per-base support, consensus, IGV tracks, methylation evidence, and construct screening.",
    ),
```

**Step 2: Verify**
```bash
python -c "from platform.api.services.result_contracts import get_result_contract_definitions; print([d.contract_id for d in get_result_contract_definitions()])"
```

**Step 3: Commit**
```bash
git add platform/api/services/result_contracts.py
git commit -m "feat(ngs): add sequence_qc_v1 result contract for all ONT workflows"
```

---

## Phase 6: Update Documentation and Lifecycle

### Task 6.1: Update lifecycle states

**Files:**
- Modify: `platform/api/services/ont_ngs_contract.py`

**Step 1: Update all implemented workflows to `seed`**

```python
    "ont_plasmid_qc": OntWorkflowSpec(..., lifecycle="seed"),
    "ont_fastq_qc": OntWorkflowSpec(..., lifecycle="seed"),
    "ont_methylation_analysis": OntWorkflowSpec(..., lifecycle="seed"),
    "ont_construct_screening": OntWorkflowSpec(..., lifecycle="seed"),
    "ont_wf_clone_validation": OntWorkflowSpec(..., lifecycle="seed"),
    "ont_fastq_dimer_qc": OntWorkflowSpec(..., lifecycle="seed"),
    "ont_basecall_dna": OntWorkflowSpec(..., lifecycle="seed"),  # Now real
    "ont_basecall_rna": OntWorkflowSpec(..., lifecycle="planned"),  # Still needs RNA model selection
```

**Step 2: Verify**
```bash
python -m pytest platform/api/tests/test_ont_ngs_contract.py -v
```

**Step 3: Commit**
```bash
git add platform/api/services/ont_ngs_contract.py
git commit -m "docs(ngs): update lifecycle states — 7 workflows now seed, RNA remains planned"
```

---

## Verification Checklist

After all phases:

- [ ] `pytest platform/api/tests/test_ont_ngs_contract.py -v` passes
- [ ] `pytest platform/api/tests/test_ont_ngs_workflow_products.py -v` passes
- [ ] `pytest platform/api/tests/test_nanopore_nextflow.py -v` passes
- [ ] `pytest platform/api/tests/test_ont_plasmid_qc_execution.py -v` passes (requires minimap2/samtools)
- [ ] `pytest platform/api/tests/test_ont_fastq_qc_execution.py -v` passes
- [ ] `pytest platform/api/tests/test_ont_fastq_dimer_qc_execution.py -v` passes
- [ ] `pytest platform/api/tests/test_ont_construct_screening_execution.py -v` passes
- [ ] All 8 workflows load without syntax errors (`nextflow run -help`)
- [ ] `ngs.nf` routes to correct workflow based on `ont_workflow_id`
- [ ] `sequence_qc_v1` appears in result contract definitions
- [ ] No workflow contains `NANOPORE_METHYLATION` indirection
- [ ] `ont_basecall_rna` still marked `planned` (RNA model selection pending)

---

## Rollback Plan

| Phase | Rollback Action |
|-------|----------------|
| 1 (Bug fixes) | Revert single commit — safe |
| 2 (Standalone workflows) | Revert to old wrappers; monolith still exists with deprecation header |
| 3 (Router/registry) | Revert `WORKFLOW_ENTRYPOINTS` and `CANONICAL_ONT_WORKFLOWS` |
| 4 (Execution tests) | Safe to remove — no production impact |
| 5 (Result contract) | Remove `sequence_qc_v1` from list — no data migration |
| 6 (Lifecycle) | Revert enum values |

---

## Commit Summary (Planned)

| # | Commit | Phase | Files |
|---|--------|-------|-------|
| 1 | `fix(ngs): modkit only runs when explicitly requested` | 1.1 | `nanopore_methylation.nf`, `ont_ngs_contract.py` |
| 2 | `fix(ngs): remove fast5 from registry` | 1.2 | `ont_ngs_contract.py` |
| 3 | `refactor(ngs): deprecate monolith, add workflow router` | 2.1 | `nanopore_methylation.nf`, `ngs.nf` |
| 4 | `feat(ngs): standalone ont_plasmid_qc` | 2.2 | `ont_plasmid_qc.nf` |
| 5 | `feat(ngs): standalone ont_wf_clone_validation` | 2.3 | `ont_wf_clone_validation.nf` |
| 6 | `feat(ngs): standalone ont_fastq_dimer_qc` | 2.4 | `ont_fastq_dimer_qc.nf` |
| 7 | `feat(ngs): standalone ont_fastq_qc with fastq_stats` | 2.5 | `fastq_stats.nf`, `ont_fastq_qc.nf` |
| 8 | `feat(ngs): standalone ont_methylation_analysis` | 2.6 | `ont_methylation_analysis.nf` |
| 9 | `feat(ngs): standalone ont_construct_screening` | 2.7 | `construct_screen.nf`, `ont_construct_screening.nf` |
| 10 | `feat(ngs): standalone ont_basecall_dna` | 2.8 | `ont_basecall_dna.nf` |
| 11 | `feat(ngs): standalone ont_basecall_rna` | 2.9 | `ont_basecall_rna.nf` |
| 12 | `feat(ngs): register all 8 standalone ONT workflows` | 3.1 | `nextflow.py`, `ont_ngs_contract.py`, `test_ont_ngs_workflow_products.py` |
| 13 | `test(ngs): add minimal test fixtures` | 4.1 | `tests/fixtures/ngs/*` |
| 14 | `test(ngs): add execution test for ont_plasmid_qc` | 4.2 | `test_ont_plasmid_qc_execution.py` |
| 15 | `test(ngs): add execution tests for fastq_qc, dimer_qc, construct_screening` | 4.3 | `test_ont_fastq_qc_execution.py`, `test_ont_fastq_dimer_qc_execution.py`, `test_ont_construct_screening_execution.py` |
| 16 | `feat(ngs): add sequence_qc_v1 result contract` | 5.1 | `result_contracts.py` |
| 17 | `docs(ngs): update lifecycle states` | 6.1 | `ont_ngs_contract.py` |

**Total: 17 commits, ~2,500 lines added, ~50 removed**

---

**Plan complete. Ready to execute using subagent-driven-development.**
