# Final de-novo browser receiving roster

Standalone browser-harness registration; not a Vitest callback/mounted surrogate.
Run from the isolated checkout root with Hermes Python (`websockets` installed):

```sh
python platform/frontend/browser-tests/denovo_final_qualification.py \
  --root /home/dalab/.hermes/profiles/fresh/cache/scratch/denovo-final-browser/NEW-EXCLUSIVE-RUN \
  --evidence /home/dalab/Documents/BMS/Releases/de-novo-full-20261021/resume-controlled-20261022/complete-20261023/final-browser-route-repair/NEW-EXCLUSIVE-RUN \
  --build /absolute/path/to/production-build \
  --build-binding /absolute/path/to/build-binding.json
```

Both run directories must be fresh. Supply the ordinary locked production build
(`corepack pnpm run build`, with `BMS_FRONTEND_BUILD_OUT_DIR` outside source) and
its recorded successful build binding; there is no default historical bundle.
The JSON binding contains `source_revision` (full commit), `source_hashes`,
`bundle_hashes`, `build` (absolute directory), `command` (the argv above), and
`build_exit_code` (0). Source hashes cover every tracked file under
`platform/frontend` except `browser-tests`, `schemas`, `packages`, and root
`package.json`, `pnpm-lock.yaml`, `pnpm-workspace.yaml`; keys are repository-relative.
Bundle hashes cover every build file, keyed relative to the build directory.
All digests are SHA-256. The harness checks exact source bytes against both the
binding and the recorded commit, plus every bundle byte including index.html,
then retains the supplied binding and its digest. Retain build logs/environment
and disclose read-only locked dependency reuse separately; a JSON record alone
is not proof that a build command ran. Private node `.tmp`/Vite caches must not
write into the dependency donor. Native API uses the canonical locked API Python and
this checkout's BMS_HOME/imports; all writable homes/SQLite/caches are private.
Chrome and API run under one user/network namespace with loopback only. No
application lifespan, execution worker or provider is started. Request headers,
including Idempotency-Key, traverse the actual browser HTTP transport.

Roster: legacy capture Save twice; fresh optional operations OFF/restore;
sequence/packing and two independent Boltz modes plus native MD editor drafts;
source-native retained RFD4 document selection and exact MODEL 2; native Project
Save/full reload and full API+Chrome process restart; retained cancelled RFD3
receiving; native document download hash; actual Results continuation anchor.
The existing native receiving fixture SQLite rows/artifacts are imported read-only
from parent scratch. They are explicitly inert source atoms, **NOT inference**.
Only the RFD3 source artifact's scratch storage binding is relocated byte-identically
under BMS_DATA, preserving its content hash and request/document identity.

Execution exits nonzero on a product failure. Historical `cc1525a` failed at the
`/submit` versus `/bms/submit` continuation anchor. A fresh production build of
`c7afeb5df2ad933498b23fe7f31cb1640759c82f` reaches `/bms/submit` after clicking
the actual redesign anchor, but the existing 21-assertion roster still records
20 passes and one failure: native materialization succeeds, then CIF-to-PDB
materialization returns HTTP 422 (`Selected CIF has no single PDB-representable
model`), so the receiving editor does not mount. Exact document/candidate/return
URI and source hash survive; the Results continuation supplies no MODEL selection.
The separate source-picker MODEL 2 save/reload/full-process-cold checks pass.
Evidence: `complete-20261023/final-browser-route-repair/run1/` under the existing
release evidence root. This is NOT successful continuation acceptance.
Partial/failed attempts remain evidence, not passing tests.
JSON DOM/network captures do not establish visual quality.
MD compiled-path text is an unexecuted editor draft, not a supplied runnable TPR;
no native MD preview/digest approval, predictor execution/provider serialization,
Project result attachment/shelf navigation or original requirement closure is
claimed by this bounded roster. Those cases remain explicitly unperformed.

`denovo_final_cdp.py` is the text-first diagnostic action/capture helper;
`denovo_final_journey.py` owns actual production DOM actions;
`denovo_final_native_server.py` composes native routers/auth/storage;
`denovo_final_retained.py` imports exact retained inert fixture state;
`denovo_final_supervisor.py` owns API/Chrome cleanup;
`denovo_final_qualification.py` registers/runs the bounded roster and preserves
real subprocess statuses, logs, immutable bundle hashes and SQLite readbacks.
