# Remaining de-novo real-browser acceptance

Runs the production bundle with native scratch routers/SQLite and actual Chrome pointer clicks. No scientific HTTP responses are mocked. Deterministic structure bytes, stored Jobs and the ready worker metadata are **NOT INFERENCE**; the worker has no running transport. The checkpoint comes from `ComponentRuntime` + `open_component_gate`, not the retired RFD3 local-redesign product.

```sh
$BMS_QA_API_PYTHON \
 platform/frontend/browser-tests/denovo_remaining_qualification.py \
 --root $BMS_QA_SCRATCH_ROOT/UNIQUE-RUN \
 --build /PATH/TO/PRODUCTION-BUNDLE \
 --retained /PATH/TO/NATIVE-WRITER-PYTEST-ROOT
```

The bundle must be built from the selected source with Vite base `/bms/`. The runner records source/bundle hashes, exact subprocess argv and exit status, native HTTP bodies, actual browser downloads, SQLite stores, DOM/control state, screenshots, namespace and process cleanup. Reuse locked dependencies is not a clean install.

Journeys:
- Dashboard Resequence **Clone** → detail GET → ordinary editor → zero/false/Local settings → native preparation → **Save Template** → native persisted readback.
- Dashboard checkpoint **Continue** → retained native artifact GET → selected output → explicit **Local**, worker and **Keep current execution target**. This qualifies the picker, not a submitted/executed successor. The execution POST remains forbidden in the harness.
- Exact native artifact **MODEL 2 and MODEL 1** → real selection checkboxes → **Compare result sets / Candidates** → cold browser reload → downloaded native JSON with both identities and unchanged document hash. MODEL 1 has genuine zero QA; MODEL 2 has no QA, so it must remain unavailable rather than borrowing MODEL 1.

Retired RFD3 local-redesign fixture remains explicitly excluded by the existing importer. No production deployment, inference, worker execution, live :18002, new admission gate, default change or redesign is exercised.
