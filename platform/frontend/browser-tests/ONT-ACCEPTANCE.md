# Isolated ONT browser acceptance

`ont-suite.html` mounts the real NGSToolkit, theme and Experiment providers with one QueryClient. `ont_native_server.py` imports native routers and a real private SQLite database without the normal application lifespan. It copies a **previously produced real** wf-clone output tree; it does not run or mock science. Synthetic job metadata identifies that import. No instrument or scheduler is started. The harness only permits GET/HEAD. Authentication and NGS access use an explicit synthetic fixture credential, never production auth.

From `platform/frontend`, using the locked API Python and private frontend node_modules:

```sh
export ONT_ACCEPTANCE_ROOT=/absolute/task-evidence/browser
export ONT_ACCEPTANCE_CLONE_OUTPUT=/absolute/previous-native-output/assembly/wf_clone_out
export ONT_ACCEPTANCE_TOKEN=synthetic-ont-ui-only
unshare --user --map-root-user --net sh -c 'ip link set lo up; exec /absolute/locked/api/.venv/bin/python browser-tests/ont_acceptance.py'
```

The supervisor requires no routes, starts native HTTP, task Vite with production React and task headless Chrome, exercises Run Inspector → Reuse Params → advanced clone settings, and terminates its process groups. Chrome temporary storage uses an inherited evidence-directory descriptor to avoid Unix-socket path length limits. Ports 18761–18763 exist only in that network namespace. This is real HTTP and the actual components, not a production-bundle browser test; run the ordinary typecheck/build separately.

Outputs: `acceptance.json`, `browser.json` (DOM and CDP requests/responses), imported artifact hashes, and component logs. **Read `blocked` as well as `passed`.** A 409 from the native clone catalog is an unresolved receiving contract, not successful report/download/IGV acceptance. Missing optional shell routes (Experiment workspace, worker inventory, GPU status, sequence library) remain visible 404s; this harness does not replace them with fabricated payloads. Use the native owner fixtures for additional domain/authoring acceptance.

`ont_cdp.py` is reusable for text-first probes in a running task namespace. Its `--alignment-job` option installs only the synthetic job capability defined by the scratch seed. Never use this fixture credential or server in a live environment.


## Retained result receiving and download verification

The supervisor now GETs every present clone catalog URL and compares HTTP status,
length and SHA-256 to both the native public descriptor and the unchanged imported
source inventory. `download_count: 0` or any `blocked` entry is **not** successful
clone download acceptance. `verified-downloads.json` contains the byte checks.
The catalog must expose `filename` (basename or unambiguous relative filename)
for the rich text loaders to bind native output names to their public URLs.
No generic `/api/files` fallback is used.

Optionally set `ONT_ACCEPTANCE_RETAINED_JOBS` to a JSON array containing
`job_id` (UUID), `name`, `mode`, `params`, `source_output` (existing genuine native
published root), and `expected_text` (rendered text assertions). The harness copies
these trees unchanged, inventories every file, labels all seeded Job metadata as
synthetic, and opens the real toolkit against the native routers. Results append
to `retained-cases.json`; blocked catalogs and missing populated text stay blocked.
Do not add invented manifests to make an imported result pass.

The same JSON enables opt-in mounted retained-byte parser controls:

```sh
ONT_ACCEPTANCE_RETAINED_JOBS=/absolute/retained-jobs.json \
ONT_ACCEPTANCE_ROOT=/absolute/existing/evidence \
node_modules/.bin/vitest run --config vitest.md.config.ts \
  tests/vitest/ngsResultRoutingMounted.test.tsx tests/vitest/ngsArtifacts.test.ts
```

Those mounted controls use real retained modkit and FASTQ-QC TSV bytes with a
**synthetic transport catalog**, not native HTTP or fresh science. Their current
fixture assertions target the retained one-read modkit control and 30-read QC
control. They are distinct from the browser/native router receiving checks.

The harness does not yet seed signal-workbench/raw-signal authority or exercise
pooled native mutations, saved-session reopen/export, or IGV populated rendering.
Existing mounted signal/IGV/review tests do not close those browser acceptance IDs.
