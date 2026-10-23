# Embedded predictor fidelity boundaries

RF3 and RFD3 are outside this release; their deployed behavior is retained.

- Caliby `ddb368cbe239ddc2bd731a963f10ffe81452bb1f` self-consistency returns
  `avg_ca_plddt`, `sc_ca_rmsd`, and `tmalign_score` keyed by designed output stem.
  These map to canonical pLDDT, CA RMSD, and TM-align respectively. TM-align is
  not pTM. Multiple samples from a shared backbone must join by output stem,
  not by the repeated input example ID. Native pLDDT units are retained.
- The Caliby recipe includes upstream's pinned optional ColabDesign/JAX
  extra with Haiku 0.0.13, compatible with retained JAX/JAXlib 0.4.34.
  The candidate image passed installed AF2 imports and five CPU configuration,
  dispatch and serializer contracts (`tests/native_caliby_af2.py`), with model
  construction/inference intercepted. Candidate build/CPU qualification is not
  GPU or initialized-model qualification; installed selection remains unchanged
  until the release owner explicitly adopts the candidate.
- Active `RunAF2` uses an in-memory wrapper over installed dl_binder_design
  `predict.py`: template target indices start at `feat_holder.binderlen`, not
  one residue later. Initial-guess coordinates, features, recycles and native
  model selection are otherwise untouched; installed source is never edited.
- The scoped BC2 startup adapter includes finite zero CA B-factor readings in
  native score-folder confidence averaging. It retains the native scale
  convention. This is not a change to AF2 prediction metrics, native campaign
  acceptance or coordinate-only prediction confidence availability.
- Standalone AF2 is not reactivated. The optional AF2_BACKPROP source branch
  remains conditional; default false is not proof of retirement or public
  reachability. Explicit multimer false transport is preserved without changing
  activation or inventing a CDR-redesign capability. Native BC2
  validation-pool exhaustion and Fold-CP 1D CP>1 numerical caveats are not
  converted into new refusal gates or silently changed scientific methods.

CPU-only checks: `tests/test_other_predictor_fidelity.py`, installed-image
`tests/native_fold_cp_chemistry.py`, and Gloo
`tests/test_fold_cp_dp_local_native.py`. The latter needs `PATCHED_CP_ROOT`
pointing to a task-owned copy of pinned native source with the tracked patch
applied, and eight CPU processes. No test loads model weights or runs inference.
