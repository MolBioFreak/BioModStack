# WP5 worker observation increment

This increment does **not** close full WP5. No ResultsViewer/JobSubmission edits.
No generation, folding, scoring, GPU invocation, rental, image/default changes or new gates.

## Implemented

- Native MetricsPool's existing `initialize_metrics_worker` reference is decorated with an importable spawn initializer. It invokes the original initializer with unchanged args, installs the passive return observer, journals per-process events, and collects at the existing launcher/publication boundary. No executor, future, payload, worker-count or native result replacement.
- Actual Biotite `structure/io/trajfile.py:write(file_name)` return capture records XTC writer path and native example/trajectory key. XTC time is explicitly **native step labels, not physical time**.
- CIF and XTC worker observations retain payload dataset/task. Duplicate example IDs without enough identity are exposed as `ambiguous_relations`, never paired by file order.
- Added source-reviewed RFD4 SSE distribution, sequence adherence and exact-zero/one probability adherence descriptors. SSE element counts include loop regions and are nanmeans across structures; probability conditions are residue/token-identity pairs, not residue counts. Unknown metrics remain undeclared/unknown.

## Verified evidence

- `test_rfd4_worker_observation.py` + `test_rfd4_results_relations.py`: **9 passed**, separately against original retained generation and retained search publication. Includes a real multiprocessing spawn, preserved initializer/result, observation failure non-gating, ambiguity, native-byte custody, and changed-byte rejection through the existing source owner.
- Actual installed native CPU-only writer probe: pinned RFD4 SIF `058a27dff02778cd7304cf8955c73115f6baaa4dfc0202540c73a10a403fa957`; actual `MetricsPool`, `initialize_metrics_worker`, `_write_input_cif`, and Atomworks writer. Parsed retained coordinates and rewrote an input CIF only; no metric manager/model/science execution. One worker input event, no observation errors. Source SHA256 before/after `f91dbfc71f29c5060f627d88fd822835247618f9ec03eb8bd19f6d2888cbc77c` unchanged. The reserialized input is not claimed byte-identical; Atomworks warned about plain-AtomArray CCD fallback. Evidence `.artifacts/results/native-worker-input/probe.json`.
- Retained `rfd4-remaining-paths/bms-search` contains three genuine checkpoints. Publisher replay in scratch → registered SQLite → fresh session reader → actual mounted NativeBinderGenerationResults succeeded. UI explicitly reports missing molecule association; chosen state route preserves exact artifact ID/path/hash. No checkpoint was unpickled, resumed or attributed to a final molecule.
- Search input path points outside its retained publication and remains `not_retained`; the test does not fabricate an input association.
- Mounted `deNovoV15Relations.test.tsx`: **2 passed** with search wire. TypeScript build passed.
- Local ignored wire: `.artifacts/results/retained-search.json`; mounted export `.artifacts/results/retained-mounted-export.json`. Original retained generation wire remains `.artifacts/results/retained.json`.

## Original state custody

Original checkpoint hash custody is preserved through existing `NativeFileSource.expected_sha256` / `prepare_native_sources`. The registered hash travels through mounted continuation and existing acquisition copies/checks those bytes. No independent artifact-ID acquisition branch was added: no additional required custody gap was established. Artifact ID remains provenance, not an independently resolved backend selector.

## Remaining implementation — not execution-only

The full source-grounded metric descriptor inventory for **all RFD4 providers, Complexa, La-Proteina and DISCO remains incomplete**. This increment extends only the explicitly reviewed RFD4 families above. Do not relabel that as an execution-only gap or declare WP5 closed.

## Remaining execution-only evidence

- Installed XTC return seam is source-confirmed and spawn transport-tested, but no retained RFD4 trajectory or refold files were found under `native-qualification-20261024` (read-only search). No scientific trajectories/refolds were manufactured. XTC/refold producer → cold reader → mounted scientific examples therefore remain unqualified.
- Genuine retained checkpoint bytes now qualify inventory, cold receiving, mounted selection and acquisition custody. Their historical publications lack saved checkpoint membership observations, so the new checkpoint-return hook's exact batch/step/rank association is still transport-qualified only. Final-candidate ancestry is not supplied by that native writer.
- The new worker hook is exercised against installed native CPU input writing, not a new full native generation run. No scientific continuation execution is claimed.

## Reproduce retained search checks

From worktree root:

```sh
BMS_RETAINED_RFD4=/home/dalab/Documents/BMS/Releases/de-novo-full-20261021/resume-controlled-20261022/complete-20261023/native-qualification-20261024/rfd4-remaining-paths/bms-search BMS_RETAINED_RESULTS_WIRE="$PWD/.artifacts/results/retained-search.json" /home/dalab/biomodstack/checkpoint-api-env/bin/python -m pytest platform/api/tests/test_rfd4_results_relations.py platform/api/tests/test_rfd4_worker_observation.py -q -s
```

From `platform/frontend`:

```sh
BMS_RETAINED_RESULTS_WIRE="$PWD/../../.artifacts/results/retained-search.json" node_modules/.bin/vitest run --config vitest.md.config.ts tests/vitest/deNovoV15Relations.test.tsx
node_modules/.bin/tsc -b --pretty false
```
