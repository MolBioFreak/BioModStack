# BindCraft2 chunking

New BMS campaigns default to **Off (no chunking)**. The Generation settings expose
three choices through the same model-owned browser and agent request:

- Off: `subbatch_size: null`.
- Native auto: `subbatch_size: "auto"`.
- Custom size: `subbatch_size` is a positive integer.

These are native inference subbatches, not target cropping or a reduction in the
number of optimization steps, models, or recycles. Off can use more GPU memory.
BMS does not silently switch modes after an out-of-memory error.

The model inventory reports `recommended_defaults.subbatch_size: null` for new
BMS requests, separately from the upstream preset's `native_default: "auto"`.
New-request normalization makes that default explicit before preview and Job
creation. Explicit null, auto, and integer settings survive compilation and
requested/effective receipt readback. Saved historical receipts and native
resume/postprocessing do not acquire the new default; their original native
settings remain unchanged.

At the installed upstream pin, auto may select four-row subbatches above 384
residues. The worker policy permits unchunked auto only when its conservative
memory estimate fits within half the free card memory; the estimate also includes
a factor-of-two safety margin. That estimate is not measured VRAM usage and does
not establish how much chunking contributes to runtime. Compare the same input
and scientific settings on the same worker to measure that contribution.

The registered `test_bindcraft2_chunking.py` regressions cover the three
representations, default normalization, preview/materialization, exact readback,
and historical resume. Setting `BMS_TEST_BC2_IMAGE` to the installed pinned image
also qualifies native CPU settings resolution and the actual subbatch consumers.
CPU qualification is not an unchunked GPU timing or memory measurement.
