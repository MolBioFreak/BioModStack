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
