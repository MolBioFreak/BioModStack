# PPIFlow Maturation Runtime RCA and Recovery Spec

> **For Hermes:** Use systematic-debugging first; implement with TDD and commit in small slices. Do not relaunch long PPIFlow production runs until the Java/runtime preflight and child-state watchdog gates pass.

**Goal:** Prevent BMS FA-MPNN→PPIFlow maturation batches from wedging when Nextflow/Java runtime health breaks or a child process becomes stale, and make failures self-evident in API/UI state.

**Architecture:** Pin the Nextflow launcher to one known-good JDK, add a launch preflight that proves Java can spawn subprocesses before scheduling GPU children, reconcile child `.nextflow/history` and process liveness into queue state, and add bounded child-stage watchdogs so one stale PPIFlow child cannot hold GPU0 and block the whole batch.

**Tech Stack:** BioModStack API, Python/FastAPI, SQLAlchemy async DB models, Nextflow DSL2, local executor, Apptainer, PyRosetta, PPIFlow, FA-MPNN.

---

## Observed incident

Parent job:

```text
50ce0192-1200-4a8b-bdbe-4956a03d20d8
human_tdt_p04053_rfa_nanobody_2p45_25step_g15_50bb_fixed_fampnn_t035x2_ppiflow_5090_rosetta_rerun
```

Final user action/status after manual kill:

```text
parent status: failed
parent error: Cancelled by user
parent stage at kill: waitformaturationchildren
design_count before PPIFlow: 40 FA-MPNN sequence designs
```

Maturation children after kill:

```text
total: 10
completed: 0
failed: 1
cancelled: 9
success_rate: 0.0
```

Before the kill, API/queue showed:

```text
PPIFlow 1/10: running at scorepartialflowimprovement for ~1 hour
PPIFlow 2/10: failed
PPIFlow 3/10..10/10: queued, blocker = busy threshold reached on GPU 0
```

## Artifact-backed findings

### Finding 1 — PPIFlow did start, but no maturation child completed

Evidence:

```text
PPIFlow 1/10 nextflow.log submitted:
- IdentifyAnchorResidues
- RunPartialFlow
- ScorePartialFlowImprovement
- ANARCII
```

But the output dir contained only:

```text
.nextflow/history
nextflow.log
```

No child output dirs were reported by the parent child-status endpoint:

```text
child_output_dirs: []
child_output_dirs_all: []
```

### Finding 2 — one PPIFlow child failed with Java `jspawnhelper` mismatch

Failed child:

```text
d7213b29-7276-48b1-8c64-1cc9b6533fc0
PPIFlow 2/10
```

`.nextflow/history`:

```text
ERR after 28m 34s
```

Failure tail:

```text
Incorrect Java version: 17.0.18+8-Ubuntu-122.04.1
jspawnhelper version 17.0.19+10-1-22.04.2-Ubuntu
This command is not for general use and should only be run as the result of a call to
ProcessBuilder.start() or Runtime.exec() in a java application
```

This is a Java runtime integrity failure: the JVM used by Nextflow and the on-disk `jspawnhelper` helper were from different OpenJDK patch versions. That breaks Nextflow's ability to spawn/monitor local executor tasks.

### Finding 3 — known good JDK exists and is internally consistent

Known-good path on this workstation:

```text
/home/dalab/.local/jdks/temurin-17
```

Observed version:

```text
openjdk version "17.0.18" 2026-01-20
Temurin-17.0.18+8
```

Observed helper version:

```text
/home/dalab/.local/jdks/temurin-17/lib/jspawnhelper -> 17.0.18+8
```

So the immediate runtime fix is not to rely on system OpenJDK; force Nextflow launches to use this JDK unless explicitly overridden.

### Finding 4 — scheduler state amplified the runtime failure

The batch was pinned to GPU0/5090. While PPIFlow 1/10 was considered running, the rest of the children were queued with:

```text
busy threshold reached on GPU 0
```

This is expected scheduler behavior for a locked single-GPU run, but it means any stale child becomes a global batch choke point.

### Finding 5 — run settings were aggressive enough that a child-stage wall-clock guard is needed

Each child processed 4 PDBs, with:

```text
ppiflow_samples_per_target: 8
maturation_redesign_enabled: true
maturation_redesign_top_n: 3
maturation_redesign_steps: 600
ppiflow_objective_mode: balanced
ppiflow_start_t: 0.75
Rosetta/PyRosetta scoring enabled
```

Per child, this can produce many local subprocesses and Rosetta/PyRosetta scoring tasks. BMS should track and bound stage-level progress; it should not leave a child in an unqualified “running” state for an hour without last-task evidence.

## Root cause

Primary RCA:

> Nextflow child workflows were launched under a Java runtime path vulnerable to OpenJDK patch-version drift. At least one child failed because the active JVM was `17.0.18` while `jspawnhelper` on disk was `17.0.19`, causing Java `ProcessBuilder`/Nextflow task launch failures.

Contributing RCA:

> The GPU scheduler correctly serialized GPU0-pinned PPIFlow children, but the system lacked a stale-child watchdog and stage liveness reporting. One running/stale child plus one failed child blocked the remaining 8 queued children and made the parent wait at `waitformaturationchildren` without a clear operator-facing RCA.

Not root cause:

- Not FA-MPNN failure: FA-MPNN produced 40 sequence designs.
- Not API/host-agent outage at the final check: both were healthy.
- Not AF3Score/DockQ omission: those were intentionally omitted and unrelated.
- Not a Rosetta metric semantic issue: PyRosetta initialized in at least one scoring workdir; runtime orchestration failed before a usable maturation batch completed.

---

## Solution spec

### Phase 1: Pin Nextflow Java runtime and preflight it

**Objective:** Ensure every BMS-launched Nextflow process uses an internally consistent JDK and can spawn child processes before any GPU reservation is consumed.

**Files:**

- Modify: `platform/api/services/nextflow.py`
- Modify: `scripts/run_biomodstack_workflow_adapter.sh`
- Modify: `scripts/run_biomodstack_api.sh` or core runtime launcher if API directly launches workflows
- Test: `platform/api/tests/test_nextflow_java_runtime.py` or adjacent Nextflow service tests

**Implementation requirements:**

1. Add a helper in `platform/api/services/nextflow.py`:

```python
def _resolve_nextflow_java_env(base_env: dict[str, str]) -> tuple[dict[str, str], list[str]]:
    """Return env with JAVA_HOME/PATH pinned for Nextflow and diagnostic notes."""
```

Resolution order:

1. `BMS_NEXTFLOW_JAVA_HOME` if set.
2. `/home/dalab/.local/jdks/temurin-17` if present and executable.
3. Existing `JAVA_HOME` if it passes preflight.
4. Fall back to PATH only, but emit a warning and fail launch if strict mode is enabled.

2. Set before `asyncio.create_subprocess_exec`:

```python
env["JAVA_HOME"] = resolved_java_home
env["PATH"] = f"{resolved_java_home}/bin:{env.get('PATH', '')}"
env.setdefault("NXF_ANSI_LOG", "false")
```

3. Add a preflight function:

```python
def _preflight_nextflow_java(env: dict[str, str]) -> tuple[bool, str]:
    """Validate java version/helper consistency and subprocess spawning."""
```

Minimum checks:

- `$JAVA_HOME/bin/java -version` exits 0.
- `$JAVA_HOME/lib/jspawnhelper` exists when using a JDK/JRE layout that has it.
- `strings $JAVA_HOME/lib/jspawnhelper` version matches `java -version` major/minor/patch where parseable.
- A Java subprocess-spawn smoke test passes. Prefer compiling/running a tiny `ProcessBuilder` class with `$JAVA_HOME/bin/javac`; if `javac` is unavailable, run the version/helper consistency check and warn.

4. Fail fast before queue reservation or immediately after assignment but before launching Nextflow if preflight fails:

```text
Nextflow Java preflight failed: JAVA_HOME=... java=... jspawnhelper=...
```

5. Surface this text in `job.error_message`, not just logs.

**Tests:**

- Test explicit `BMS_NEXTFLOW_JAVA_HOME` wins.
- Test Temurin default is selected when present.
- Test `PATH` is prepended with `$JAVA_HOME/bin`.
- Test mismatch parser reports a fatal diagnostic.
- Test preflight failure sets job failed and releases assigned GPU.

**Commit:**

```bash
git add platform/api/services/nextflow.py platform/api/tests/test_nextflow_java_runtime.py scripts/run_biomodstack_workflow_adapter.sh scripts/run_biomodstack_api.sh
git commit -m "fix(workflows): pin Nextflow Java runtime"
```

### Phase 2: Add maturation child liveness reconciliation

**Objective:** A child cannot remain “running” indefinitely if its Nextflow process is gone, its `.nextflow/history` is terminal, or its workdir has not advanced past a defined stage-specific timeout.

**Files:**

- Modify: `platform/api/services/gpu_orchestrator.py`
- Modify: `platform/api/routers/jobs.py`
- Possibly modify: `platform/api/services/stage_review.py`
- Test: `platform/api/tests/test_maturation_child_reconciliation.py`

**Implementation requirements:**

1. Enhance child status reconciliation to include:

- `.nextflow/history` status `OK` -> completed, release GPU.
- `.nextflow/history` status `ERR` -> failed, release GPU, preserve final log excerpt.
- missing/empty terminal history but `nextflow_run_id` process not live and no recent workdir mtime -> failed/stale, release GPU.

2. Add stage-liveness fields to child status:

```text
last_workdir_mtime
last_nextflow_log_mtime
last_submitted_process
stale_for_seconds
stale_reason
```

3. Stage timeout policy for PPIFlow maturation child:

Defaults:

```text
BMS_MATURATION_CHILD_STAGE_STALE_SECONDS=3600
BMS_MATURATION_CHILD_HARD_TIMEOUT_SECONDS=7200
```

For aggressive runs with PyRosetta, allow override in params:

```text
ppiflow_child_stage_timeout_seconds
ppiflow_child_hard_timeout_seconds
```

4. When a child is marked stale/failed, do not silently keep GPU reservations:

```python
job.assigned_gpu = None
job.queue_status = "failed"
job.error_message = "PPIFlow child stale: no workdir/log progress for ..."
```

5. Parent `WaitForMaturationChildren` should stop waiting once all children are terminal, and report counts:

```text
0 completed, 1 failed, 9 cancelled/stale; no child outputs to aggregate
```

**Tests:**

- Running child + `.nextflow/history` ERR -> failed, GPU released.
- Running child + no live PID + stale mtime -> failed, GPU released.
- Queued children are not blocked by failed child’s assigned GPU.
- Parent child-status endpoint reports stale reason and last evidence path.

**Commit:**

```bash
git add platform/api/services/gpu_orchestrator.py platform/api/routers/jobs.py platform/api/services/stage_review.py platform/api/tests/test_maturation_child_reconciliation.py
git commit -m "fix(queue): reconcile stale PPIFlow maturation children"
```

### Phase 3: Add Nextflow/PPIFlow child launch preflight before batch spawning

**Objective:** Do not spawn 10 children if the first child cannot pass host/runtime preflight.

**Files:**

- Modify: `workflows/antibody_denovo.nf`
- Modify: `scripts/spawn_maturation_children.py`
- Create: `scripts/preflight_maturation_runtime.py`
- Test: `tests/test_ppiflow_maturation_preflight_contract.py`

**Implementation requirements:**

1. Add `scripts/preflight_maturation_runtime.py` that checks:

- Java/Nextflow preflight via the same JDK selection.
- Apptainer exists and can run `antibody_tools.sif` smoke command.
- `ppiflow` container/config paths exist.
- PyRosetta import smoke test inside the relevant container, bounded timeout.
- Required input PDBs exist under the path visible to the worker.

2. `SpawnMaturationJobs` must run preflight once before creating all child jobs.

3. If preflight fails, parent should fail before creating children, with actionable diagnostic.

4. Write preflight JSON artifact:

```text
${params.out_dir}/preflight/maturation_runtime_preflight.json
```

**Tests:**

- Static workflow test asserts preflight is called before `spawn_maturation_children.py`.
- Unit test for missing Java returns `ok=false` with no secret leakage.
- Unit test for failed PyRosetta smoke marks preflight failed.

**Commit:**

```bash
git add workflows/antibody_denovo.nf scripts/preflight_maturation_runtime.py scripts/spawn_maturation_children.py tests/test_ppiflow_maturation_preflight_contract.py
git commit -m "feat(ppiflow): preflight maturation runtime before child spawn"
```

### Phase 4: Bound fan-out and add operator-visible progress

**Objective:** Make PPIFlow progress understandable and prevent one GPU0 child from hiding batch-level state.

**Files:**

- Modify: `workflows/maturation_child_core.nf`
- Modify: `scripts/score_maturation.py`
- Modify: `platform/api/routers/jobs.py`
- Modify frontend queue/job status component if needed.
- Test: backend status tests plus static workflow tests.

**Implementation requirements:**

1. Emit a per-child progress JSON:

```text
maturation_child_progress.json
```

Fields:

```json
{
  "input_pdb_count": 4,
  "samples_per_target": 8,
  "expected_partial_flow_samples": 32,
  "partial_flow_completed": 0,
  "score_json_count": 0,
  "anarcii_completed": 0,
  "rosetta_scored_count": 0,
  "last_updated_at": "..."
}
```

2. API child status endpoint reads this file when present.

3. Parent status displays:

```text
PPIFlow 1/10: 24/32 scored, last update 7m ago
```

instead of only `scorepartialflowimprovement`.

4. Add configurable concurrency for CPU-heavy scoring inside a child if PyRosetta/ANARCII fan-out is too broad for local executor stability.

Suggested params:

```text
ppiflow_child_max_score_concurrency
ppiflow_child_max_anarcii_concurrency
ppiflow_child_max_rosetta_concurrency
```

Default conservative on 5090 host:

```text
score: 4
ANARCII: 4
Rosetta: 2
```

**Commit:**

```bash
git add workflows/maturation_child_core.nf scripts/score_maturation.py platform/api/routers/jobs.py platform/api/tests tests
git commit -m "feat(ppiflow): report maturation child progress"
```

### Phase 5: Relaunch strategy after fixes

**Objective:** Restart from existing 40 FA-MPNN designs without redoing RFA or FA-MPNN unless explicitly desired.

**Relaunch inputs:**

Use existing FA-MPNN sequence outputs from parent:

```text
/var/lib/biomodstack/bms_results/human_tdt_p04053_rfa_nanobody_2p45_25step_g15_50bb_fixed_fampnn_t035x2_ppiflow_5090_rosetta_rerun_20260610_014617/collected/fampnn* or stage_outputs.fampnn paths
```

**Recommended relaunch changes:**

Conservative first pass:

```text
ppiflow_samples_per_target: 4
maturation_redesign_top_n: 2
maturation_redesign_steps: 400
ppiflow_child_stage_timeout_seconds: 3600
ppiflow_child_hard_timeout_seconds: 7200
```

Then aggressive second pass if preflight/progress is clean:

```text
ppiflow_samples_per_target: 8
maturation_redesign_top_n: 3
maturation_redesign_steps: 600
```

**Acceptance gate before production relaunch:**

Run a 1-child canary on two FA-MPNN designs:

```text
pdb_count: 2
ppiflow_samples_per_target: 2
maturation_redesign_top_n: 1
```

Must produce:

- child `.nextflow/history` = `OK`
- nonempty child output dir
- score JSONs with BMS-local objective
- Rosetta fields present or explicit `rosetta_interface_analyzer_used=false` with error
- API child status terminal within expected time
- GPU released after completion

Only then launch full 40-design PPIFlow maturation.

---

## Acceptance criteria

1. Java runtime preflight fails fast on simulated `java`/`jspawnhelper` mismatch.
2. Nextflow launch environment always includes pinned `JAVA_HOME` and `$JAVA_HOME/bin` at front of `PATH`.
3. A failed child `.nextflow/history=ERR` is reconciled to failed and releases GPU automatically.
4. A stale child with no live process and no workdir/log movement is marked failed with an artifact-backed reason.
5. Queue no longer reports only `busy threshold reached on GPU 0` when the blocker is a stale/failed child.
6. PPIFlow child status includes last evidence path and last update time.
7. Canary PPIFlow maturation run completes before any full rerun.
8. No claim of paper-aligned PPIFlow final ranking; output remains BMS-local PPIFlow objective + Rosetta InterfaceAnalyzer evidence.

## Immediate operator recommendation

Do not restart the same full 10-child aggressive PPIFlow batch unchanged. First implement Phase 1 + Phase 2, then run the canary in Phase 5. If the canary passes, resume/relaunch PPIFlow from the existing 40 FA-MPNN designs rather than repeating FA-MPNN.
