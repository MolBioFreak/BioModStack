# Final native contract pin

`schemas/bioxp_method_native.json` is the actual `bioxp.protocols.method_contract.method_contract()` export from committed native source `759ee635e851fc447269c59ac69b3e705ab79b34`, plus the ApplicationRequest/Recipe/capability envelopes used by the compiler. Regenerate with `tests/export_bioxp_method_native_schema.py` in the pinned native environment, under its offline guard.

Ordinary action parameter schemas now come from that producer. Conditional numeric fields are coerced from raw draft spellings using the selected native `if/then` schema, including attainment tolerance/timeout. Raw persisted drafts remain unchanged.

Park/RGB/SS and finite Cavro registration nulls are replaced only where that source establishes registration. Classifier remains unavailable; physical qualification remains false. The SS implementation remains the recovered source no-op, not physical separation or operator completion. pLLD behavior is unchanged; no automatic Z Stop, classifier, runtime gate, retry or cleanup was introduced.

`test_bioxp_method_final_contract.py` can receive `BIOXP_FINAL_DOCUMENTS=<documents.json>` to send every final model document through the actual Methods quick-run snapshot producer, with only the native transport inert. It verifies exact original compiler documents and exports `run-documents.json`, plus four explicit recovery/control inputs. Native tests execute these bytes; they do not inject metadata into observed jobs.
