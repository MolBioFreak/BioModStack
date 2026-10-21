# Embedded predictor fidelity boundaries

- RF3 is the embedded Foundry PDB predictor, not a re-enabled standalone
  sequence route. Foundry `b02eed6a6bdf8f44d14a80cc36e3da13c9f2291c`
  emits `overall_plddt` as a fraction and `overall_pae` in Angstrom. Filtering
  and canonical metadata convert only pLDDT to percent, including zero.
- Native top-ranked RF3 files alias an existing sample when per-sample leaves
  exist. Candidate export retains every actual sample, and archives top aliases
  and ranking CSVs separately. With native sample dumping disabled, the only
  top model remains a candidate. Identical samples are not content-deduplicated.
- Pinned RF3 `chain_ptm` actually contains chain mean pLDDT. Canonical metadata
  names this `rf3_chain_mean_plddt_fraction`; raw native summaries remain intact.
  Full native `token_res_ids` are zero-based token positions, not author residue
  IDs. Chain-pair summaries are upper-triangular, not full directed matrices.
  No true chain pTM or residue correspondence is fabricated.
- Caliby `ddb368cbe239ddc2bd731a963f10ffe81452bb1f` self-consistency returns
  `avg_ca_plddt`, `sc_ca_rmsd`, and `tmalign_score` keyed by designed output stem.
  These map to canonical pLDDT, CA RMSD, and TM-align respectively. TM-align is
  not pTM. Multiple samples from a shared backbone must join by output stem,
  not by the repeated input example ID. Native pLDDT units are retained.
- The Caliby recipe now includes upstream's pinned optional ColabDesign/JAX
  extra with a resolved transitive lock constrained by the original runtime.
  Resolution is not import/build/GPU qualification. Existing installed images
  remain unchanged until separately built and explicitly adopted.
- Active `RunAF2` uses an in-memory wrapper over installed dl_binder_design
  `predict.py`: template target indices start at `feat_holder.binderlen`, not
  one residue later. Initial-guess coordinates, features, recycles and native
  model selection are otherwise untouched; installed source is never edited.
- The scoped BC2 startup adapter includes finite zero CA B-factor readings in
  native score-folder confidence averaging. It retains the native scale
  convention. This is not a change to AF2 prediction metrics, native campaign
  acceptance or coordinate-only prediction confidence availability.
- Standalone AF2 and legacy AF2_BACKPROP remain retired/disabled. Native BC2
  validation-pool exhaustion and Fold-CP 1D CP>1 numerical caveats are not
  converted into new refusal gates or silently changed scientific methods.

CPU-only checks: `tests/test_other_predictor_fidelity.py`, installed-image
`tests/native_rf3_fidelity.py`, `tests/native_fold_cp_chemistry.py`, and Gloo
`tests/test_fold_cp_dp_local_native.py`. The latter needs `PATCHED_CP_ROOT`
pointing to a task-owned copy of pinned native source with the tracked patch
applied, and eight CPU processes. No test loads model weights or runs inference.
