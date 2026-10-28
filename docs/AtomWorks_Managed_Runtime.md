# Managed Development Studio and isolated preparation runtime

The existing `biomodstack_services.render_user_units()` / `install_user_units()`
owner now consumes an **optional** `atomworks-studio-development.json` in
`biomodstack_runtime_profile.get_biomodstack_config_dir()`. No descriptor leaves
legacy units unchanged. Production rendering never consumes it. The existing
Development target/API weak `Wants` relationship starts the loopback CPU Studio;
there is no `Requires` dependency, scientific admission condition or new scheduler.
The Studio unit is `PartOf` the Development target and uses control-group teardown.

The API alone reads the same private EnvironmentFile as Studio (not the frontend
or workflow adapter). A configured `BMS_MOLECULAR_PREPARATION_PYTHON` dispatches
only graph/geometry POST execution to that isolated interpreter. JSON stdin/stdout
carries the unchanged typed request/result, while native logs go to stderr. The
worker imports only the existing stateless service leaves, avoiding the eager
Nextflow/API services initializer. `-I -B` ignores caller Python package injection
and prevents source bytecode writes. When unconfigured, routes retain the original
in-process execution. Complement and schema discovery remain dependency-free and unchanged.
Original native ValueError/RuntimeError details and unavailable classes preserve
the existing route statuses (graph 422/503, geometry 422/502/503), including RDKit
ValueError subclasses. Unstartable/crashed transport returns preparation-only 503.
The native geometry subprocess's existing timeout and scientific policies stay
unchanged; no broader job or preview refusal was added.

## Stage only — no managed configuration/service changes

Use the previously hash-qualified isolated installation and rebuilt wheel; there
is no resolver, scientific repin, API pip install, model entry or weight acquisition.
The stage command verifies the wheel RECORD, original wheel payloads, repaired
Python/assets/notices, lock identity and installed locked versions. It copies the
venv and complete standalone Python distribution (reflinks where supported, no
scratch symlinks), relocates interpreter entrypoints, carries the Amber executable
and corresponding source/licenses, and smoke-imports the real CPU Studio. It emits
a complete payload hash inventory, a private 0600 EnvironmentFile and a descriptor.
The secret is generated locally and is never printed or included in the manifest.
The destination is a new 0700 directory; staging refuses an existing destination.
No live configuration is selected until the separate reviewed activation below.

```sh
S=/home/dalab/.hermes/profiles/fresh/cache/scratch
R=/home/dalab/.local/share/biomodstack/runtime/atomworks-studio/closeout-20261028
python3 -B scripts/atomworks_studio_runtime.py \
  --prepared "$S/atomworks-patched-host/supported-venv" \
  --wheel "$S/atomworks-patched-host/supported-venv-package/atomworks_studio-0.0.5-1bms03-py3-none-any.whl" \
  --geometry-bundle "$S/s05-geometry-bundle-qualified" \
  --destination "$R" --port 18092
# Already staged: use read-only verification, not another stage invocation.
python3 -B scripts/atomworks_studio_runtime.py --verify-only \
  --prepared "$R/venv" \
  --wheel "$R/atomworks_studio-0.0.5-1bms03-py3-none-any.whl"
```

`manifest.json` covers files at staging, not mutable future sessions or credentials.
The selected managed `md-preparation-v1.sif` remains external, read-only, and under
existing `paths.get_container_path()` / container-runtime ownership. The staged
native leaf verifies its original image/bundle receipts on use. It inherits the
API's existing `BMS_CONTAINER_DIR`; do not replace/rebuild that image. This host's
qualified image is `/mnt/BioModStack/apptainer/md-preparation-v1.sif`.

## Parent-reviewed activation/cutover — NOT run by the staging task

Publish/integrate the reviewed source normally. Preserve the existing automatic
sync pause/timer state. Immediately before deploying, verify `origin/test` is the
parent's exact approved release; the synchronizer deploys the current tip, not a
revision argument. Confirm 18092 is still unused. From the reviewed source root:

```sh
R=/home/dalab/.local/share/biomodstack/runtime/atomworks-studio/closeout-20261028
ss -ltn 'sport = :18092'
CONFIG=$(python3 -B -c 'from biomodstack_runtime_profile import get_biomodstack_config_dir; print(get_biomodstack_config_dir())')
# Preserve a prior descriptor if one has appeared; do not overwrite another owner.
test ! -e "$CONFIG/atomworks-studio-development.json"
install -d -m 700 "$CONFIG"
install -m 600 "$R/development.json" "$CONFIG/atomworks-studio-development.json"
# Use the installed supported one-shot Development transaction, not source reset.
python3 ~/.local/libexec/biomodstack/biomodstack_dev_sync.py --deploy-now
```

That transaction's existing manager installs/render units and API Wants starts
Studio. A deferred-active-work/paused receipt is NOT deployment. Do not add
`--allow-active-work` without the parent's exact active-work authorization. Do not
start/restart services from this task worktree or expose the Studio port in ingress.
If the reviewed source has already been deployed before descriptor activation,
the existing manager can regenerate and start API with its normal lifecycle:

```sh
cd /home/dalab/biomodstack/dev-test-canonical
python3 -B scripts/manage_desktop_services.py restart-api --runtime dev
```

This alternative is a real local API restart and needs the same parent cutover
review. No Studio unit enable is required: it is pulled in by API/Development
Wants. A later change to the Studio wrapper itself requires an explicit managed
`systemctl --user restart biomodstack-atomworks-studio.service`; normal API-only
restarts do not evict editor sessions gratuitously. `PartOf` covers target teardown.

Read back the exact activated descriptor, unit `EnvironmentFiles`, running process
interpreter/loopback port and only boolean secret presence. Do not print the
EnvironmentFile or `/proc/*/environ`. Verify the saved sync receipt, actual API
`/api/health` build identity and served Vite build identity separately. Through the
existing authenticated BMS session verify Studio import/edit/Undo/export and the
optional construction calls; do not bypass auth with dumped credentials. Readiness
of a socket is not browser/custody/native-model acceptance. Rollback can restore or
remove only this descriptor, regenerate the old managed API units, and explicitly
stop the Studio unit when no longer used; preserve the staged payload/private
sessions and original descriptor. Production remains untouched.

## Qualification and limits

`tests/test_molecular_preparation_runtime.py` runs in the API interpreter without
RDKit and invokes real locked RDKit/Amber in the staged CPU interpreter. Its private
ASGI fixture exercises actual route validation/typed responses, ssDNA/ssRNA graphs,
all three duplex geometry forms, exact equality to independent native calls,
original semantic errors/unavailable details, and optional Development-only unit
rendering. This is not a live full-app/store or browser call.
`tests/test_atomworks_studio_http.py` separately starts fixture-owned loopback
Studio and exercises the actual BMS identity/CSRF proxy, real edit/Undo/export,
MODEL behavior, private assets and patched package. The browser case is opt-in;
its absence must be reported as skipped, not counted as acceptance.

No scientific schemas/algorithms/pins changed. The locked versions include RDKit
2026.9.1, Biotite 1.6.0, AtomWorks 3.0.4, Pydantic 2.14.1 and Torch 2.7.1+cpu.
Missing CCD/PDB mirror warnings remain truthful: broad offline chemistry is not
qualified. The Amber geometry scope and GPL corresponding source remain described
in `S05_AmberTools_Geometry.md`. No inference, production deployment, rentals,
app-store writes or Jobs are part of this runtime qualification.
