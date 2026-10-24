# Final de-novo browser receiving roster

Standalone browser-harness registration; not a Vitest callback/mounted surrogate.
Run from the isolated checkout root with Hermes Python (`websockets` installed):

```sh
python platform/frontend/browser-tests/denovo_final_qualification.py \
  --root /home/dalab/.hermes/profiles/fresh/cache/scratch/denovo-final-browser/NEW-EXCLUSIVE-RUN \
  --evidence /home/dalab/Documents/BMS/Releases/de-novo-full-20261021/resume-controlled-20261022/complete-20261023/final-browser/NEW-EXCLUSIVE-RUN
```

Both directories must be fresh. The harness reuses the parent's actual production
bundle only after checking its exact index SHA and the empty frontend/schema diff
between its recorded UI revision and the frozen integrated product. No install or
bundle rebuild is claimed. Native API uses the canonical locked API Python and
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

Execution exits nonzero on a product failure. A fresh run against cc1525a records
the real `/submit` versus `/bms/submit` continuation-anchor failure; it is NOT
expected-success acceptance. Partial/failed earlier attempts remain evidence,
not passing tests. JSON DOM/network captures do not establish visual quality.
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
