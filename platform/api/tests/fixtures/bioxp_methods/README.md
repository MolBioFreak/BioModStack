# Receiving fixtures

These are copied bytes, not generated contract mocks. Hashes are SHA-256.

* `combined-thermal-result.json`: robot combined source **0fa5843**, exported by the integration owner's installed-image guarded source-overlay run of `test_native_thermal_timer_profile_real_store` (real ASGI/dispatcher/executor/SQLite; physical controller sends replaced). Source: `bioxp-workflow-implementation-r4/evidence/combined-first-pass-correct-fixtures/thermal.json/thermal-result.json`. Hash `53f5c55e00ff98ddcaf31324364fcef924cfab6057b890863238c4705ad99f06`.
* `thermal-result.json`: first-pass native lane producer, commits **5c2190f / ef98228**, same real owner test. Source: `bioxp-workflow-implementation-r4/native-exports/thermal-result.json`. Hash `1eabf91981265a2c677e3956e627c71ce6d0292cb0e7a71f5135107e24969440`.
* `ui-method-requests.json`: actual mounted frontend outgoing request capture, explicitly labeled transport-fixture-only by its producer. Source: `bioxp-workflow-implementation-r4/ui-method-requests.json`. Hash `4e5727ee6dff91d537bf71fb111f934154f326e5dcce2b26c2d6482e4d899aa3`.

The receiving suite feeds unmodified thermal bytes into `BioXpRobotClient` through an inert HTTP transport, then through the real mounted methods router and report. Native-authored thermal jobs do not contain BMS method snapshots, so the test asserts reconstruction is unavailable rather than relabeling native IDs to look compiler-produced.

The source-free lifecycle separately exercises real discovery/class source/compiler/UserTemplate revisions with an inert admission transport. Partial failure/owned-child cases use actual compiler provenance but constructed native fault states, explicitly not real native execution proof. UI request bodies are preserved; only allocated library identities are rebound to scratch records. The UI's incomplete/unbound method fixtures are correctly representation errors under the actual compiler, not fabricated executable examples. Complete bound scientific examples and native partial/error-hold exports must be qualified on final integrated owners.
