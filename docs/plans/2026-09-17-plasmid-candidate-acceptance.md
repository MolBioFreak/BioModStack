# Plasmid corrective series: candidate acceptance boundary

Status: prepared source changes, not an integrated or qualified BMS release.
Upstream inspection baseline: `test` at
`9ad10eea97cf784da47b07d6d9b2896da35e455e`.

## Portable checks

From an environment with the repository's locked development dependencies:

```sh
python scripts/run_plasmid_candidate_tests.py --output-dir /path/outside/repo/plasmid-checks
```

The runner records exact source hashes, local commit, command, JUnit results and
unexecuted gates. A passing unit command is NOT release acceptance. Passing
`--require-release-qualification` intentionally returns a failure: this portable
runner cannot substitute for the qualification work below. Do not fabricate an
acceptance receipt to make that flag pass.

The verifier decision tests run actual Python validation, provenance binding and
serialization against synthetic records, but mock samtools IO. The `.nf` tests
inspect source wiring; they do not parse or execute Nextflow. The circular tests
include a separate linear edit-distance oracle and exhaustive rotation checks.

## Scope and unresolved work, by original plan

| Plan | Candidate change | Required before closure |
|---|---|---|
| PL-01 | Missing/invalid/unresolved structural summaries cannot certify a clean screen; canonical call fields and source-table contradiction are checked. | Run canonical producer on real cohorts, validate its denominator/read identity and exact output statuses; wire all relevant runtime artifact receipts. |
| PL-02 | QC reads are normalized across input routes; requested assemblies are verified and compared with retained read-guided consensus. | Execute all route/toggle combinations using actual Nextflow; reconcile stage/output registry, remote dependency closure, result API and browser. |
| PL-03 | Zero coverage is a no-call; incomplete observations produce REVIEW; strong omitted insertions are detected; non-plurality consensus is not automatically proven wrong. | Qualify quality/strand/MAPQ/overlap semantics and complete profile coverage; integrate new profile into typed UI/API before enabling it. |
| PL-04 | Runtime guard checks every primary read's RG-to-model metadata before applying the locked polishing override. | Move identical eligibility into authoritative preview/admission; qualify pinned model compatibility, imported FASTQ provenance, metadata retention and operator/agent parity. |
| PL-05 | Exact-match fast path and resource-bounded, full-length circular alignment replace unbounded long-input rotation matrices. | Qualify repeat-rich/large/complex real cases; profile whole-BAM parsing and workflow resources; operator-facing unresolved results. |
| PL-06 | Portable report labels mapping scope narrowly; unavailable variant analysis is not a zero-variant claim. | Integrate equivalent main React/API presentation, read-scope provenance and competitive/linked-mixture analysis; calibrate detection limits. |
| PL-07 | Explicit hold contract and regression guard preserve reference-required verification. | Reference-free workflow is NOT implemented; see its separate gate document. |
| PL-08 | Portable runner, decision regressions and explicit acceptance ledger. | Locked-runtime, truth-set, full-stack and authorized release validation remain NOT RUN. |

## Behavioral changes requiring review

- `run_fastq_qc=false` is respected. An assembly may still be generated, but missing
  structural evidence cannot produce complete verification.
- Imported FASTQ converted to BAM without model-bearing read groups will be
  rejected by the clone model guard. Do not remove the guard to restore an
  unsupported polishing assumption. Supply bound provenance through the normal
  ingestion/preview integration, or use the reference-guided QC route.
- Zero eligible dimer-screen reads now cause REVIEW, not substitution of the
  whole-BAM denominator. Validate whether a different, separately measured
  whole-read structural screen is needed for the intended sample population.
- Circular search is bounded at 64 edits, 4096 candidate orientations/offsets,
  50 million scored cells and 32 million traceback bytes. These are implementation
  resource limits, not accuracy thresholds. Exhaustion means unresolved/REVIEW;
  no partial local match is promoted to a global result. Exact matches bypass
  edit search. Do not claim that these bounds cover every intended construct.
- The new `plasmid_complete_v2` profile remains experimental and is NOT made the
  default or exposed as a completed operator capability. The original profile
  object and all qualification flags remain unchanged.
- Verifier producer version is 0.3.0; the existing v2 manifest shape and check keys
  remain compatible at schema level. Optional summary/metric evidence is additive.
  Full Python/TypeScript semantic-reader compatibility still needs testing. No
  historical artifact, profile identity, runtime receipt or verdict is rewritten.
- The source-bound runtime implementation record must be regenerated by the
  existing authorized builder after integration. This series does not edit it
  by hand, activate a runtime, or claim the existing receipt covers changed bytes.

## Release qualification

Freeze claim-specific intended scope and holdout datasets before calibrating.
Use known-correct and known-altered constructs, repeats, origin-spanning changes,
structural alternatives, controlled mixtures, coverage loss and provenance errors.
Run equivalent read populations through supported input representations; test
separately with independent extraction/library/sequencing replicates.

Do not use consensus-aligned-only vendor FASTQ to estimate original-sample
unmapped fractions or purity. Retain the BFX6NB ambiguity as an unresolved
historical result; do not relabel it as resolved truth merely to achieve a pass.

Measure false acceptance, false rejection, review rate, missing calls, variant
and structural accuracy, and mixture sensitivity with sample counts/uncertainty.
A count threshold is not a demonstrated detection limit. Synthetic tests are not
biological replication. Keep `automatic_pass_eligible=false` until the actual
claim is qualified. Do not silently redefine `public_accuracy_validated`.

Require current `origin/test` reconciliation, full locked-environment tests,
actual source/image/model/effective-setting identity, UI/agent parity,
ordinary result reopen/download, cancellation/stale callback/retry and restart
acceptance. Do not gate readable partial results on optional viewers/telemetry.
Production promotion and live runs remain separate authorizations.


## 2026-09-18 live-review follow-up

Corrected evaluated structural fixtures without relaxing missing-evidence checks. The fixture uses the selected interpreter and `BMS_TEST_SAMTOOLS` (or PATH) for real-tool tests. Registered the comparison process and model-provenance output, including helper dependencies; both assembly workflows now have matching conditional native metadata graphs. The candidate runner reports dependency preflight failure separately from executed-test outcomes.

An isolated GitHub-hosted checkout ran the focused checks below. API/dev dependencies were installed using `uv sync --frozen --group dev --no-install-project` into an external environment; this was not the live Development environment or a full-API test suite. Baseline fixture assertions were unchanged; an external pytest hook redirected only their historical Python/samtools executable paths. Native metadata tests construct actual annotation objects and artifact/dependency roles; they do not run Nextflow, provision a remote worker, or sequence a biological sample.

```json
{
  "baseline": {
    "errors": 0,
    "failures": 7,
    "skipped": 0,
    "tests": 57
  },
  "baseline_tool_path_override_only": true,
  "candidate_api_fixtures": {
    "errors": 0,
    "failures": 0,
    "skipped": 0,
    "tests": 59
  },
  "native_metadata": {
    "errors": 0,
    "failures": 0,
    "skipped": 0,
    "tests": 39
  },
  "portable": {
    "errors": 0,
    "failures": 0,
    "skipped": 0,
    "tests": 80
  },
  "qualification_gate_exit_code": 2,
  "versions": {
    "pytest": "pytest 9.1.1",
    "python": "3.12.3 (main, Jul 15 2026, 23:46:41) [GCC 13.3.0]",
    "samtools": "samtools 1.19.2"
  }
}
```

Execution log: https://github.com/MolBioFreak/BioModStack/actions/runs/35369717831

The source-bound runtime record is regenerated in the immediately following commit by the existing builder from this record-free source precursor. Its source-only/unverified/open states are not changed to acceptance. Re-run the builder after subsequent integration changes. Full-repository/Development tests, actual Nextflow/remote execution, interface parity, and scientific qualification remain open. No existing result or profile identity is rewritten.


## 2026-09-20 samtools discovery follow-up

The API construct-verification fixture now resolves samtools lazily. With no `BMS_TEST_SAMTOOLS` and no samtools on `PATH`, only BAM-backed tests skip with an explicit dependency reason; pure-Python checks still execute. An explicitly configured but unresolvable executable fails clearly without fallback. `AGENTS.md` documents the executable override, `-rs`, and the distinction between skipped native checks and native verification. Scientific assertions, verifier code, and qualification flags are unchanged.

An isolated GitHub-hosted full checkout, using API/dev Python dependencies from `uv sync --frozen --group dev --no-install-project`, reproduced the original missing-tool failure and checked both PATH discovery and explicit selection outside PATH. The targeted runs use `--noconftest` to avoid the host-specific API harness; they do not claim a live Development or full API test run. The invalid-override failure below is an intentional negative control, and native skips mean tests not run.

```json
{
  "baseline_missing": {
    "errors": 0,
    "failures": 45,
    "passed": 14,
    "skipped": 0,
    "tests": 59
  },
  "candidate_explicit": {
    "errors": 0,
    "failures": 0,
    "passed": 59,
    "skipped": 0,
    "tests": 59
  },
  "candidate_missing": {
    "errors": 0,
    "failures": 0,
    "passed": 14,
    "skipped": 45,
    "tests": 59
  },
  "candidate_path": {
    "errors": 0,
    "failures": 0,
    "passed": 59,
    "skipped": 0,
    "tests": 59
  },
  "invalid_override": {
    "errors": 0,
    "failures": 1,
    "passed": 0,
    "skipped": 0,
    "tests": 1
  },
  "native_metadata": {
    "errors": 0,
    "failures": 0,
    "passed": 39,
    "skipped": 0,
    "tests": 39
  },
  "portable": {
    "errors": 0,
    "failures": 0,
    "skipped": 0,
    "tests": 93
  }
}
```

Execution log: https://github.com/MolBioFreak/BioModStack/actions/runs/35534014440

The qualification flag still exits 2. These four test/documentation paths are outside the runtime source denominator; every recorded source-authority digest and the existing canonical runtime record were checked unchanged. That record continues to bind its original source precursor, not this test-only commit. Nextflow execution, remote provisioning, UI acceptance, and biological qualification remain outside this check.
