# NGS Nextflow Decomposition Implementation Plan

> **For Hermes:** Use `subagent-driven-development` only after Christian approves a phase. This plan is intentionally scoped to NGS/Nanopore Nextflow layout and regression harnesses. Do **not** edit `main.nf` during the planning or early relocation phases except to prove it remains untouched and NGS-blind.

**Goal:** Make BioModStack NGS workflows utterly independent from `main.nf`, then decompose the current Nanopore workflow and oversized Dorado module into an explicit NGS entrypoint, workflow directory, and tool/process modules.

**Architecture:** `ngs.nf` is the only top-level NGS entrypoint. NGS workflow orchestration lives under `workflows/ngs/`. Individual NGS tools/processes live under `modules/ngs/`. API launcher routing continues to invoke `nextflow run ngs.nf` for Nanopore fresh and resume paths. `main.nf` remains protein/design/structure orchestration and must stay completely NGS-blind.

**Tech Stack:** Nextflow DSL2, Python/Pytest launcher tests, FastAPI launcher service, Apptainer-backed Dorado/modkit/minimap2/samtools runtime, BioModStack workflow adapter.

---

## 1. Current Repo Evidence

Reviewed checkout: `/home/dalab/biomodstack/biomodstack`

Current NGS entrypoint:

- `ngs.nf`
  - Includes `NANOPORE_METHYLATION` from `./workflows/nanopore_methylation.nf`.
  - Dispatches when `params.nanopore_enabled` or compatibility `params.rfd_mode == 'nanopore_methylation'`.
  - Does **not** include or call `main.nf`.

Current NGS workflow:

- `workflows/nanopore_methylation.nf`
  - Includes NGS processes from `../modules/dorado.nf`.
  - Owns inline input validation for POD5/BAM/FASTQ exclusivity.
  - Wires Dorado basecall/alignment, BAM preparation, FASTQ alignment/QC, modkit pileup/summary, and clone validation.
  - Contains `reportNanoporeStage(...)` helper for stage reporter updates.

Current oversized NGS module:

- `modules/dorado.nf`, currently contains these processes:
  - `DoradoBasecall`
  - `DoradoAlign`
  - `PrepareBamForAnalysis`
  - `ValidateMappedBam`
  - `PrepareReferenceForIGV`
  - `ModkitPileup`
  - `ModkitSummary`
  - `FastqAlign`
  - `FastqPlasmidQC`
  - `FastqMultimerQC`
  - `FastqDimerAnalysis`
  - `BuildDimerCanonicalOutputs`
  - `RunCloneValidation`

Existing top-level `workflows/` directory contains many non-NGS workflow files already:

- Antibody/design/structure/experimental workflows such as `antibody_denovo.nf`, `structure_prediction.nf`, `boltzgen_design.nf`, `protein_local_redesign.nf`, etc.
- Current NGS workflow is a peer at `workflows/nanopore_methylation.nf`.

The target plan must therefore **not** pretend `workflows/` is empty or purely NGS-owned. NGS should move into a subdirectory instead of flattening more NGS files into the already crowded shared root.

---

## 2. Non-Negotiable Scope Rules

1. **`main.nf` must remain untouched by NGS decomposition unless the task is a read-only verification step.**
2. **`main.nf` must contain zero NGS terms:** no `nanopore`, `dorado`, `modkit`, `methylation`, `clone_validation`, `fastq`, `bam_path`, `reference_fasta`, or `ngs.nf`.
3. **No NGS fallback branch in `main.nf`.** If a Nanopore launch is misrouted to `main.nf`, the launcher test should fail rather than adding compatibility logic there.
4. **`ngs.nf` is the only NGS top-level entrypoint.** New NGS workflows dispatch from `ngs.nf` or future NGS-only entrypoints, never from `main.nf`.
5. **Preserve current API launch behavior.** Fresh and resumed Nanopore launches must continue to invoke `nextflow run ngs.nf`.
6. **Preserve existing non-NGS workflows.** Do not reorganize antibody/design/structure workflow files as part of this NGS plan.
7. **No fake/demo outputs.** Smoke tests may use minimal valid inputs, but must not create placeholder artifacts as proof of biological execution.
8. **Do not sweep unrelated dirty tree files into this work.** Existing unrelated changes must remain untouched.

---

## 3. Target Directory Layout

Target NGS layout:

```text
ngs.nf
workflows/
  nanopore_methylation.nf        # temporary compatibility location until Phase 1 relocation
  ngs/
    nanopore_methylation.nf
    nanopore_fastq_qc.nf
    nanopore_clone_validation.nf
    nanopore_assembly.nf
    nanopore_variant_calling.nf
modules/
  dorado.nf                      # temporary compatibility module until fully split
  ngs/
    dorado_basecall.nf
    dorado_align.nf
    bam_prepare.nf
    fastq_align.nf
    fastq_plasmid_qc.nf
    fastq_dimer_qc.nf
    modkit_pileup.nf
    modkit_summary.nf
    clone_validation.nf
    manifest_publish.nf
```

Important layout rule:

- `workflows/ngs/` is the NGS workflow namespace.
- `modules/ngs/` is the NGS process namespace.
- The existing shared `workflows/` root remains for already-existing non-NGS files until a separate non-NGS refactor is approved.

---

## 4. Final Intended `ngs.nf`

The final router should be small and NGS-only:

```nextflow
#!/usr/bin/env nextflow
nextflow.enable.dsl = 2

include { NANOPORE_METHYLATION } from './workflows/ngs/nanopore_methylation.nf'
include { NANOPORE_FASTQ_QC } from './workflows/ngs/nanopore_fastq_qc.nf'
include { NANOPORE_CLONE_VALIDATION } from './workflows/ngs/nanopore_clone_validation.nf'
include { NANOPORE_ASSEMBLY } from './workflows/ngs/nanopore_assembly.nf'
include { NANOPORE_VARIANT_CALLING } from './workflows/ngs/nanopore_variant_calling.nf'

workflow {
    def workflowName = (
        params.ngs_workflow
        ?: params.workflow
        ?: params.rfd_mode
        ?: 'nanopore_methylation'
    ).toString()

    if (workflowName == 'nanopore_methylation') {
        NANOPORE_METHYLATION()
        return null
    }

    if (workflowName == 'nanopore_fastq_qc') {
        NANOPORE_FASTQ_QC()
        return null
    }

    if (workflowName == 'nanopore_clone_validation') {
        NANOPORE_CLONE_VALIDATION()
        return null
    }

    if (workflowName == 'nanopore_assembly') {
        NANOPORE_ASSEMBLY()
        return null
    }

    if (workflowName == 'nanopore_variant_calling') {
        NANOPORE_VARIANT_CALLING()
        return null
    }

    error("Unsupported NGS workflow: ${workflowName}")
}
```

Compatibility note:

- `params.rfd_mode` may remain here temporarily only because current BMS model/profile naming already uses it.
- It must not leak into `main.nf` or become the long-term canonical NGS workflow selector.
- Long-term canonical selector should be `params.ngs_workflow`.

---

## 5. Final Intended Workflow Responsibilities

### `workflows/ngs/nanopore_methylation.nf`

Owns the end-to-end Nanopore methylation capability:

```text
exactly one primary input: POD5, BAM, or FASTQ
        ↓
normalize to analysis BAM
        ↓
optional Dorado basecall/align for POD5
        ↓
optional BAM prepare/realign
        ↓
optional FASTQ align and plasmid QC
        ↓
optional modkit pileup/summary for POD5/BAM-derived evidence
        ↓
optional clone validation / assembly
        ↓
publish typed NGS artifact manifest
```

It should include NGS modules only:

```nextflow
include { DORADO_BASECALL } from '../../modules/ngs/dorado_basecall.nf'
include { DORADO_ALIGN } from '../../modules/ngs/dorado_align.nf'
include { PREPARE_BAM_FOR_ANALYSIS; VALIDATE_MAPPED_BAM; PREPARE_REFERENCE_FOR_IGV } from '../../modules/ngs/bam_prepare.nf'
include { FASTQ_ALIGN } from '../../modules/ngs/fastq_align.nf'
include { FASTQ_PLASMID_QC } from '../../modules/ngs/fastq_plasmid_qc.nf'
include { MODKIT_PILEUP } from '../../modules/ngs/modkit_pileup.nf'
include { MODKIT_SUMMARY } from '../../modules/ngs/modkit_summary.nf'
include { RUN_CLONE_VALIDATION } from '../../modules/ngs/clone_validation.nf'
include { PUBLISH_NGS_MANIFEST } from '../../modules/ngs/manifest_publish.nf'
```

### `workflows/ngs/nanopore_fastq_qc.nf`

A narrower FASTQ-only capability:

- Requires `--fastq_path` and `--reference_fasta`.
- Runs `FASTQ_ALIGN` and `FASTQ_PLASMID_QC`.
- Does not run Dorado or modkit.
- Produces `qc_manifest.json` / sequence-QC artifacts.

### `workflows/ngs/nanopore_clone_validation.nf`

A clone-validation capability:

- Accepts BAM or FASTQ + reference, depending on validated mode.
- Uses alignment/preparation modules as needed.
- Runs `RUN_CLONE_VALIDATION`.
- Publishes clone validation artifacts and a typed manifest.

### `workflows/ngs/nanopore_assembly.nf`

A future assembly-oriented capability:

- Owns wf-clone/Flye/Medaka-style assembly orchestration.
- Must not be hidden inside Dorado or methylation code.
- Must have preflight for nested workflow assets/caches before execution.

### `workflows/ngs/nanopore_variant_calling.nf`

A future variant-calling capability:

- Owns Clair3/Medaka/variant reports if added.
- Must be independent of methylation unless explicitly composed.

---

## 6. Final Intended Module Boundaries

Split `modules/dorado.nf` into small single-purpose files.

### `modules/ngs/dorado_basecall.nf`

Processes:

- `DORADO_BASECALL`

Responsibility:

- POD5 directory → basecalled BAM + basecall log + optional sequencing summary.

### `modules/ngs/dorado_align.nf`

Processes:

- `DORADO_ALIGN`

Responsibility:

- BAM + reference FASTA → aligned/indexed BAM + reference copy/index + log.

### `modules/ngs/bam_prepare.nf`

Processes:

- `PREPARE_BAM_FOR_ANALYSIS`
- `VALIDATE_MAPPED_BAM`
- `PREPARE_REFERENCE_FOR_IGV`

Responsibility:

- Normalize user BAMs, guarantee BAM/BAI availability, validate mapped BAMs before modkit/assembly, prepare FASTA/FAI for reports.

### `modules/ngs/fastq_align.nf`

Processes:

- `FASTQ_ALIGN`

Responsibility:

- FASTQ + reference → sorted/indexed aligned BAM using validated minimap2 preset.

### `modules/ngs/fastq_plasmid_qc.nf`

Processes:

- `FASTQ_PLASMID_QC`

Responsibility:

- Build read-length, coverage, per-base support, IGV tracks/report, consensus, and `qc_manifest.json` for FASTQ plasmid/construct QC.

### `modules/ngs/fastq_dimer_qc.nf`

Processes:

- `FASTQ_MULTIMER_QC`
- `FASTQ_DIMER_ANALYSIS`
- `BUILD_DIMER_CANONICAL_OUTPUTS`

Responsibility:

- Dimer/multimer-specific FASTQ analyses and canonical output normalization.

### `modules/ngs/modkit_pileup.nf`

Processes:

- `MODKIT_PILEUP`

Responsibility:

- BAM + reference → methylation BED/pileup artifacts.

### `modules/ngs/modkit_summary.nf`

Processes:

- `MODKIT_SUMMARY`

Responsibility:

- BAM → modkit summary TSV/log.

### `modules/ngs/clone_validation.nf`

Processes:

- `RUN_CLONE_VALIDATION`

Responsibility:

- Run wf-clone/nested clone validation behind a clear wrapper and artifact contract.

### `modules/ngs/manifest_publish.nf`

Processes:

- `PUBLISH_NGS_MANIFEST`

Responsibility:

- Gather emitted NGS artifacts into one typed NGS artifact manifest for API/frontend consumption.

---

## 7. Testing Strategy

### Existing tests to preserve

- `platform/api/tests/test_nanopore_nextflow.py`
  - Entry point exists.
  - NGS workflow exists.
  - Fresh Nanopore launch uses `ngs.nf`.
  - Resume Nanopore launch uses `ngs.nf`.
  - `main.nf` contains no NGS symbols.

### Add tests as the layout changes

When moving `workflows/nanopore_methylation.nf` to `workflows/ngs/nanopore_methylation.nf`, update/add tests:

```python
def test_ngs_workflow_lives_under_ngs_workflows_namespace() -> None:
    assert (REPO_ROOT / "workflows" / "ngs" / "nanopore_methylation.nf").exists()
    assert not (REPO_ROOT / "workflows" / "nanopore_methylation.nf").exists()
```

When splitting modules, add tests for includes and removed legacy coupling:

```python
def test_nanopore_workflow_uses_ngs_module_namespace() -> None:
    workflow = (REPO_ROOT / "workflows" / "ngs" / "nanopore_methylation.nf").read_text(encoding="utf-8")
    assert "../../modules/ngs/dorado_basecall.nf" in workflow
    assert "../../modules/ngs/fastq_align.nf" in workflow
    assert "../../modules/ngs/modkit_pileup.nf" in workflow
    assert "../modules/dorado.nf" not in workflow
```

Keep the hard boundary test:

```python
def test_ngs_is_explicitly_isolated_from_main_entrypoint() -> None:
    main_nf = (REPO_ROOT / "main.nf").read_text(encoding="utf-8").lower()
    ngs_nf = (REPO_ROOT / "ngs.nf").read_text(encoding="utf-8").lower()

    forbidden_main_terms = (
        "nanopore",
        "dorado",
        "modkit",
        "methylation",
        "clone_validation",
        "fastq",
        "bam_path",
        "reference_fasta",
        "ngs.nf",
    )
    assert not any(term in main_nf for term in forbidden_main_terms)
    assert "main.nf" not in ngs_nf
```

Run after every phase:

```bash
source platform/api/.venv/bin/activate
pytest platform/api/tests/test_nanopore_nextflow.py -q
```

Expected:

```text
all tests passed
```

### Main non-regression verification

After every phase:

```bash
git diff -- main.nf
```

Expected:

```text
# no output
```

Also scan for forbidden NGS terms:

```bash
python3 - <<'PY'
from pathlib import Path
main = Path('main.nf').read_text(errors='ignore').lower()
terms = ['nanopore','dorado','modkit','methylation','clone_validation','fastq','bam_path','reference_fasta','ngs.nf']
hits = {term: main.count(term) for term in terms if term in main}
assert not hits, hits
print('main.nf NGS isolation OK')
PY
```

Expected:

```text
main.nf NGS isolation OK
```

---

## 8. Phased Implementation Plan

### Phase 0 — Lock the boundary before moving files

**Objective:** Make the no-`main.nf` boundary executable before any layout churn.

**Files:**

- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Read-only verify: `main.nf`, `ngs.nf`, `platform/api/services/nextflow.py`

**Steps:**

1. Keep or add the test that scans `main.nf` for forbidden NGS terms.
2. Keep or add tests that fresh/resume Nanopore command construction uses `ngs.nf`.
3. Run:

   ```bash
   source platform/api/.venv/bin/activate
   pytest platform/api/tests/test_nanopore_nextflow.py -q
   ```

4. Run:

   ```bash
   git diff -- main.nf
   ```

5. Expected:

   - Nanopore tests pass.
   - `git diff -- main.nf` has no output.

**Commit boundary:**

```bash
git add platform/api/tests/test_nanopore_nextflow.py docs/plans/2026-05-15-ngs-nextflow-decomposition-plan.md
git commit -m "test: lock NGS nextflow boundary away from main"
```

Only commit if unrelated dirty files are not staged.

---

### Phase 1 — Move the NGS workflow into `workflows/ngs/`

**Objective:** Respect the existing crowded `workflows/` root by giving NGS its own workflow namespace.

**Files:**

- Create: `workflows/ngs/nanopore_methylation.nf`
- Modify: `ngs.nf`
- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Remove after tests pass: `workflows/nanopore_methylation.nf`
- Do not modify: `main.nf`

**Steps:**

1. Create directory:

   ```bash
   mkdir -p workflows/ngs
   ```

2. Move the existing workflow:

   ```bash
   git mv workflows/nanopore_methylation.nf workflows/ngs/nanopore_methylation.nf
   ```

3. Update `ngs.nf` include:

   ```nextflow
   include { NANOPORE_METHYLATION } from './workflows/ngs/nanopore_methylation.nf'
   ```

4. Update relative include inside moved workflow from:

   ```nextflow
   } from '../modules/dorado.nf'
   ```

   to:

   ```nextflow
   } from '../../modules/dorado.nf'
   ```

5. Update tests to expect:

   ```python
   assert (REPO_ROOT / "workflows" / "ngs" / "nanopore_methylation.nf").exists()
   assert not (REPO_ROOT / "workflows" / "nanopore_methylation.nf").exists()
   assert "./workflows/ngs/nanopore_methylation.nf" in (REPO_ROOT / "ngs.nf").read_text(encoding="utf-8")
   ```

6. Run:

   ```bash
   source platform/api/.venv/bin/activate
   pytest platform/api/tests/test_nanopore_nextflow.py -q
   ```

7. Run:

   ```bash
   git diff -- main.nf
   ```

8. Expected:

   - Tests pass.
   - `main.nf` diff is empty.
   - NGS workflow exists only under `workflows/ngs/`.

**Commit boundary:**

```bash
git add ngs.nf workflows/ngs/nanopore_methylation.nf platform/api/tests/test_nanopore_nextflow.py
git add -u workflows/nanopore_methylation.nf
git commit -m "refactor: move nanopore workflow under workflows/ngs"
```

---

### Phase 2 — Split low-risk alignment/preparation modules first

**Objective:** Start decomposing `modules/dorado.nf` with small processes that have clear boundaries.

**Files:**

- Create: `modules/ngs/bam_prepare.nf`
- Create: `modules/ngs/fastq_align.nf`
- Modify: `workflows/ngs/nanopore_methylation.nf`
- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Do not modify: `main.nf`

**Process moves:**

- `PrepareBamForAnalysis` → `PREPARE_BAM_FOR_ANALYSIS`
- `ValidateMappedBam` → `VALIDATE_MAPPED_BAM`
- `PrepareReferenceForIGV` → `PREPARE_REFERENCE_FOR_IGV`
- `FastqAlign` → `FASTQ_ALIGN`

**Compatibility rule:**

- Rename process symbols only if you update every include/call in the NGS workflow in the same commit.
- Do not keep duplicated processes with the same name imported from two places.

**Steps:**

1. Copy the exact process bodies from `modules/dorado.nf` into the new module files.
2. Prefer preserving process names for the first split if it reduces risk:

   ```nextflow
   include { PrepareBamForAnalysis; ValidateMappedBam; PrepareReferenceForIGV } from '../../modules/ngs/bam_prepare.nf'
   include { FastqAlign } from '../../modules/ngs/fastq_align.nf'
   ```

3. Remove those process includes from the legacy `../../modules/dorado.nf` include block in `workflows/ngs/nanopore_methylation.nf`.
4. Leave the legacy process definitions in `modules/dorado.nf` temporarily only if no duplicate include occurs; delete them once all references are moved and tests/smoke pass.
5. Add tests asserting the workflow includes the new module paths.
6. Run targeted tests.
7. Run `git diff -- main.nf` and forbidden term scan.

**Commit boundary:**

```bash
git add modules/ngs/bam_prepare.nf modules/ngs/fastq_align.nf workflows/ngs/nanopore_methylation.nf platform/api/tests/test_nanopore_nextflow.py
git commit -m "refactor: split NGS BAM prepare and FASTQ align modules"
```

---

### Phase 3 — Split FASTQ QC modules

**Objective:** Move FASTQ QC logic out of the Dorado module because it is not Dorado basecalling.

**Files:**

- Create: `modules/ngs/fastq_plasmid_qc.nf`
- Create: `modules/ngs/fastq_dimer_qc.nf`
- Modify: `workflows/ngs/nanopore_methylation.nf`
- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Do not modify: `main.nf`

**Process moves:**

- `FastqPlasmidQC`
- `FastqMultimerQC`
- `FastqDimerAnalysis`
- `BuildDimerCanonicalOutputs`

**Steps:**

1. Copy process bodies exactly first.
2. Update includes in `workflows/ngs/nanopore_methylation.nf`.
3. Add tests asserting:

   - `modules/ngs/fastq_plasmid_qc.nf` exists.
   - `modules/ngs/fastq_dimer_qc.nf` exists.
   - The NGS workflow no longer imports these processes from `modules/dorado.nf`.

4. Run:

   ```bash
   source platform/api/.venv/bin/activate
   pytest platform/api/tests/test_nanopore_nextflow.py -q
   ```

5. Verify `main.nf` unchanged.

**Commit boundary:**

```bash
git add modules/ngs/fastq_plasmid_qc.nf modules/ngs/fastq_dimer_qc.nf workflows/ngs/nanopore_methylation.nf platform/api/tests/test_nanopore_nextflow.py
git commit -m "refactor: split NGS FASTQ QC modules"
```

---

### Phase 4 — Split modkit modules

**Objective:** Move modified-base analysis into explicit modkit modules.

**Files:**

- Create: `modules/ngs/modkit_pileup.nf`
- Create: `modules/ngs/modkit_summary.nf`
- Modify: `workflows/ngs/nanopore_methylation.nf`
- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Do not modify: `main.nf`

**Process moves:**

- `ModkitPileup`
- `ModkitSummary`

**Steps:**

1. Copy existing process bodies exactly.
2. Update workflow includes.
3. Preserve FASTQ-only semantic: FASTQ-only runs do not become modkit evidence.
4. Add tests asserting modkit module paths are imported from `modules/ngs/`.
5. Run targeted tests.
6. Verify `main.nf` unchanged.

**Commit boundary:**

```bash
git add modules/ngs/modkit_pileup.nf modules/ngs/modkit_summary.nf workflows/ngs/nanopore_methylation.nf platform/api/tests/test_nanopore_nextflow.py
git commit -m "refactor: split NGS modkit modules"
```

---

### Phase 5 — Split Dorado basecall/alignment modules

**Objective:** Leave Dorado-specific work in Dorado-specific NGS modules.

**Files:**

- Create: `modules/ngs/dorado_basecall.nf`
- Create: `modules/ngs/dorado_align.nf`
- Modify: `workflows/ngs/nanopore_methylation.nf`
- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Do not modify: `main.nf`

**Process moves:**

- `DoradoBasecall`
- `DoradoAlign`

**Steps:**

1. Copy exact process bodies.
2. Update includes.
3. Run tests.
4. Run a minimal NGS Nextflow preview or smoke if runtime assets are available.
5. Verify `main.nf` unchanged.

**Commit boundary:**

```bash
git add modules/ngs/dorado_basecall.nf modules/ngs/dorado_align.nf workflows/ngs/nanopore_methylation.nf platform/api/tests/test_nanopore_nextflow.py
git commit -m "refactor: split NGS Dorado modules"
```

---

### Phase 6 — Split clone validation and manifest publishing

**Objective:** Keep nested wf-clone and artifact-manifest creation out of Dorado concerns.

**Files:**

- Create: `modules/ngs/clone_validation.nf`
- Create: `modules/ngs/manifest_publish.nf`
- Modify: `workflows/ngs/nanopore_methylation.nf`
- Modify or create: `scripts/build_ngs_artifacts_manifest.py` if a final manifest process is implemented now
- Modify: `platform/api/tests/test_nanopore_nextflow.py`
- Do not modify: `main.nf`

**Process moves/additions:**

- `RunCloneValidation`
- New `PublishNgsManifest` or equivalent, if approved for this phase.

**Steps:**

1. Move `RunCloneValidation` into `modules/ngs/clone_validation.nf`.
2. Add manifest publishing only if there is a clear typed manifest script ready; otherwise defer to a later artifact-contract phase.
3. Update includes.
4. Run tests.
5. Verify `main.nf` unchanged.

**Commit boundary:**

```bash
git add modules/ngs/clone_validation.nf modules/ngs/manifest_publish.nf workflows/ngs/nanopore_methylation.nf platform/api/tests/test_nanopore_nextflow.py scripts/build_ngs_artifacts_manifest.py
git commit -m "refactor: split NGS clone validation and manifest publishing"
```

Only add `scripts/build_ngs_artifacts_manifest.py` if actually created in this phase.

---

### Phase 7 — Remove legacy `modules/dorado.nf` NGS imports or turn it into a shim

**Objective:** Finish the decomposition without breaking old accidental imports abruptly.

**Files:**

- Modify or delete: `modules/dorado.nf`
- Modify: tests
- Do not modify: `main.nf`

Options:

1. **Preferred final state:** delete `modules/dorado.nf` if no references remain.
2. **Compatibility shim:** keep `modules/dorado.nf` temporarily as comments or includes pointing to `modules/ngs/*` only if Nextflow include semantics allow it cleanly and tests cover it.

Before deleting:

```bash
python3 - <<'PY'
from pathlib import Path
root = Path('.')
for p in root.rglob('*.nf'):
    if 'work/' in p.parts:
        continue
    text = p.read_text(errors='ignore')
    if 'modules/dorado.nf' in text or '../modules/dorado.nf' in text or '../../modules/dorado.nf' in text:
        print(p)
PY
```

Expected before deletion:

```text
# no references outside possible legacy shim
```

Run tests and verify `main.nf` unchanged.

**Commit boundary:**

```bash
git add -u modules/dorado.nf workflows/ngs/nanopore_methylation.nf platform/api/tests/test_nanopore_nextflow.py
git commit -m "refactor: retire legacy Dorado aggregate module"
```

---

## 9. Nextflow Smoke Validation

After Phase 1 and after the final module split, run a real minimal smoke when runtime assets allow it.

Do not use empty fake BAM files as proof.

Suggested smoke shape:

1. Create a tiny valid reference FASTA.
2. Create a tiny valid mapped SAM/BAM using samtools inside the Dorado container if host samtools is missing.
3. Run `ngs.nf` with BAM input, modkit disabled, assembly disabled.

Example command skeleton:

```bash
TMPDIR=$(mktemp -d)
export NEXTFLOW_HOME="$TMPDIR/nxf-home"
mkdir -p "$NEXTFLOW_HOME" "$TMPDIR/work" "$TMPDIR/out"

cat > "$TMPDIR/ref.fa" <<'EOF'
>ref
ACGTACGTACGTACGTACGTACGTACGTACGT
EOF

cat > "$TMPDIR/input.sam" <<'EOF'
@HD	VN:1.6	SO:coordinate
@SQ	SN:ref	LN:32
read1	0	ref	1	60	32M	*	0	0	ACGTACGTACGTACGTACGTACGTACGTACGT	FFFFFFFFFFFFFFFFFFFFFFFFFFFFFFFF
EOF

apptainer exec apptainer/dorado.sif samtools view -bS "$TMPDIR/input.sam" > "$TMPDIR/input.bam"

nextflow run ngs.nf \
  -profile nanopore_methylation,workstation_ryzen7960x \
  -w "$TMPDIR/work" \
  --bam_path "$TMPDIR/input.bam" \
  --reference_fasta "$TMPDIR/ref.fa" \
  --out_dir "$TMPDIR/out" \
  --run_modkit false \
  --run_assembly false
```

Expected:

- Nextflow completes successfully.
- Outputs are real BAM/BAI/reference-prep artifacts, not placeholders.
- `main.nf` remains untouched.

---

## 10. Definition of Done

This plan is complete when all of these are true:

- `ngs.nf` is the only top-level NGS entrypoint.
- NGS workflows live under `workflows/ngs/`.
- NGS process modules live under `modules/ngs/`.
- `workflows/` root remains otherwise unchanged for existing non-NGS workflows.
- `main.nf` contains no NGS/Nanopore/Dorado/modkit/FASTQ/BAM/methylation references.
- `git diff -- main.nf` is empty for every NGS decomposition phase.
- Fresh and resumed Nanopore launches still call `nextflow run ngs.nf`.
- Targeted tests pass:

  ```bash
  source platform/api/.venv/bin/activate
  pytest platform/api/tests/test_nanopore_nextflow.py -q
  ```

- At least one real minimal NGS Nextflow smoke run succeeds after relocation and after final module decomposition.
- No unrelated dirty-tree files are staged or modified by this work.

---

## 11. Explicit Non-Goals

- Do not refactor protein/design/structure workflows in this plan.
- Do not split `main.nf` in this plan.
- Do not redesign the full API model registry in this plan.
- Do not invent fake NGS artifacts for UI satisfaction.
- Do not convert MolBio into NGS; any read-evidence bridge remains a separate typed attachment contract.
