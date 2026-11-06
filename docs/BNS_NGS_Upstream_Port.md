# BNS NGS/ONT upstream port inventory

## Pins and ownership

- BNS base: `4aeaee783cda69035522a823e80824a86e3512df`.
- Source test: `7e662af00f56bdd70a74978ba0ff5fd54a6704fd`.
- Source main: `84476f5622e7a8f257f77fd823bd41756544dd94`.
- Comparison merge base: `dc701125bf1ee0c9813f6974886cbd9c48c2df38`.
- Worktree: `/home/dalab/.hermes/profiles/fresh/cache/scratch/bns-ngs-installer-port`.
- Branch: `task/bns-ngs-installer-port`. Source only; no push, deployment, host changes or dependency manifest edits.
- MolBio cloning, alignment engine/viewer and dependency updates belong to the other lane.

`BNS_NGS_Upstream_Port_Inventory.json` enumerates 91 scoped files and 83 touching source commits (including merges), with full commit IDs, per-file final-hunk classification and reasons. Final pinned test/main trees are identical for all these files. Historical commits are mapped to their final file/hunk state, not represented as whole-commit cherry-picks. Shared owners were read as complete diffs; this is not a keyword patch copy.

## Already present (76 scoped files)

BNS already has the pinned upstream ONT backend, workflows, runtime recipes/locks and deployment instructions. These files are byte-identical, including merge-result descriptor confinement. No extra ONT workflow backport is needed. Coverage includes:

- Raw-signal POD5/SLOW5/BLOW5 registration, worker, restored POD5, conversion automation and retention choice.
- MinKNOW native management, output discovery, run receipts and host helper.
- Signal workbench, waveform/read squiggle, Squigualiser/Squigulator policies and build scripts.
- Dorado CPU/CUDA selection, trailing partial stride, all pinned modification models and target-scoped controls.
- Tandem modkit across the DNA/plasmid workflows and the nonblocking FASTQ skip.
- NGS alignment sessions, ONT contract/result schemas and pinned image build/deployment guidance.
- Shared Python installer source-only docopt exception, first-party package bootstrap, managed interpreter resolver and frontend bootstrap owners in this inventory (dependency changes remain owned by the other lane).

Exact paths and source commits are in the JSON inventory.

## Ported shared hunks

| Source | Hunk | BNS adaptation |
| --- | --- | --- |
| `eb5f23fd11f39d31f30f9cbb6f81a3135287ae73` | `asset_publication_environment` and generated API/adapter selector environment | Complete non-secret selector helper and three environment insertions. Keep rendering, explicit empty override, absent-owner and secret-exclusion tests; exclude the source test's Boltz execution fixture rather than restoring a trimmed model. |
| `f92a54a97ad412424515f06c4d60a551f5a03306` | extracted runtime durability | Replace per-inode fsync with one checked Linux syncfs before publication; preserve manifest/parent fsync, inode/mode verification, writeback failure cleanup and warm reuse. Carry full tests and documentation. |
| `1d69c8cb3214727eafd015ae24314e7efd2b8beb` | runtime image semantic publication keys | Broaden publication key support while retaining existing malformed-key coverage. No workflow/model is enabled by this change. |

## Superseded by BNS fixes

- `biomodstack_python_prerequisites.py` and its tests retain BNS `4aeaee7`: original `pyvenv.cfg` home links remain valid when support Python is relocated. Upstream replacement would undo the required fix.
- `test_ont_signal_backend_startup.py` retains BNS's stronger in-flight remote polling cancellation test instead of upstream's weaker immediate AsyncMock fixture.

## Edition-excluded/shared hunk boundaries

- `biomodstack_services.py`: no AtomWorks Studio descriptor/service, automatic mobile publisher activation or full-product Tailnet restart policy. `manage_desktop_services.py`: no Tailnet environment-selection imports/hooks.
- `services/nextflow.py`: no protein generation, chemistry, RFD4/Complexa/La-Proteina, oracle/checkpoint or optional gate-review-row additions; ONT compiler/runtime portions are already present. All changed hunks were read, including the shared terminal publisher hunk.
- Generic HF acquisition/local mirror, nested RFD4 oracle layouts, invocation receipt reuse, de novo task-private weight projection/accounting and rental udocker revision changes are not needed by the existing direct pinned ONT Apptainer provisioning owner. They are explicitly excluded, not claimed to be present. Existing execution is preserved.
- MolBio cloning and alignment scope is delegated; no dependency files were modified by this lane.

## Gate/restriction review

No new workflow admission/refusal gate or stricter scientific success condition is carried. The syncfs change preserves the existing durability requirement; it does not add a scientific check. Semantic publication key support relaxes an existing allow-list. Selector propagation is optional and does not forward credentials.

Not carried: HF `prepare_release_sources` adds bounded-release count/role/size refusal rules; full-product generator mode refusal branches and Studio descriptor validation are outside this port. Existing Dorado/OEM restrictions and existing raw-signal integrity/path/descriptor checks are unchanged. Existing source-identity checks were not changed to accommodate tests.

## Qualification boundaries

Tests use the canonical API interpreter read-only, worktree source through PYTHONPATH, private test stores and the network namespace handshake. Actual host/runtime provisioning and native instrument/basecalling acceptance are parent-owned, not demonstrated by these filesystem and API fixtures.

Initial completed runs:

- Root runtime views + lifecycle: **107 passed**.
- Twelve API files (shared publication, Python/lifecycle, ONT runtime/import/conversion/retention/device/workflow controls): **315 passed, 2 failed, 1 deselected**, plus **7 unittest subtests passed**. Both failures reproduced at the exact BNS base: `test_install_user_units_writes_expected_files` and `test_mobile_update_publisher_unit_is_outside_both_runtime_targets`; these are pre-existing lifecycle expectations, not newly ported blocks.
- Clean committed-tree final ONT run (the seven additional ONT files plus publication rendering): **211 passed**, exit 0, 272.27 seconds, tested source `18f673ec782b0cfe4a223f6d6f754e9c121f8f82`. The earlier timed-out/dirty-source attempts are retained as non-acceptance evidence; all five source-identity failures disappeared on the clean full-roster rerun. No source-identity check was changed.

Evidence directories:

- `/home/dalab/.hermes/profiles/fresh/cache/scratch/ngs-port-evidence/`: root/API/baseline JUnit, earlier attempt logs, retained lock/policy snapshot and `reproduce.sh` with exact commands and full file rosters.
- `/home/dalab/.hermes/profiles/fresh/cache/scratch/test-runs/bns-ngs-ont-clean-final/`: clean final `junit.xml` and `log.txt`, including `pytest_exit=0`.

The root command was `env -u PYTHONHOME PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=.:scripts /home/dalab/biomodstack/dev-test-canonical/platform/api/.venv/bin/python -m pytest tests/test_runtime_image_views.py tests/test_runtime_image_lifecycle.py -o addopts='' -p no:cacheprovider --junitxml=<evidence>/root-runtime.xml -q`.

The clean final command was `timeout 900 bash /home/dalab/.hermes/profiles/fresh/skills/bms-mounted-test-runner-env/scripts/run-api-tests.sh <worktree> bns-ngs-ont-clean-final tests/test_ont_signal_backend_startup.py tests/test_ont_signal_workbench.py tests/test_ont_read_squiggle.py tests/test_ont_ngs_contract.py tests/test_ont_ngs_result_schema.py tests/test_ont_prepared_launch.py tests/test_ont_ngs_submission.py tests/test_asset_publication_handoff.py -m 'not runtime_integration' --tb=short -q`.

No assertion or production gate was loosened to turn failures green.

## Existing documentation caveat for host provisioning

The final paragraph of the historical `BNS_NGS_Runtime_Deployment.md` still says managed Dorado requires CUDA. That upstream text is stale, not a reason to disable CPU: the current contract supports CPU and CUDA (`BMS_ONT_DORADO_DEVICE=cpu` selects the installation default), and this lane's CPU-device tests passed. The historical guide's native acceptance claims describe its earlier host, not the new CachyOS host. Full host provisioning and a real new-host job remain parent-owned.
