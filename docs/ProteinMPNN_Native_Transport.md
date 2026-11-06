# ProteinMPNN native role and score transport

The single execution adapter is `scripts/proteinmpnn_native_binding.py`.
`proteinmpnn_binder_roles.py` retains only the historical CLI entrypoint and
forwards to that owner. Neither path renumbers or reorders the input pose.

## Residue locks

- Legacy inpaint `True` remains fixed. Identity is `(author chain, author
  residue number, insertion code)`, not the integer residue number alone.
- Legacy prep emits real Rosetta pose-index FIXED labels plus their full author
  identity in `REMARK BMS_MPNN_FIXED`. The latter disambiguates the pose dialect
  from historical generic monomer author-number labels.
- Historical `binder_chains` replay uses Rosetta pose-index labels, as the old
  binder adapter did. Explicit `fixed_positions` remains author-qualified.
- H/L preparation retains ATOM/B-factor bytes and emits explicit design/fixed
  chain roles. Local-redesign manifest selections become exact author-residue
  masks, including blank versus nonblank insertion codes.
- Legacy preparation does not inherit generic input admission restrictions;
  native Rosetta retains its parsing authority. This is not certification of
  every nonprotein, alternate-location or multi-model structure.

## Scoring authority and historical identity

The installed dl helper scores sampled residues while conditioning its model
forward on the source sequence. The installed **standard ProteinMPNN CLI**
(`ProteinMPNN/protein_mpnn_run.py`, line 334 in the audited source) instead uses
`S_sample`, `use_input_decoding_order=True`, and
`decoding_order=sample_dict["decoding_order"]`, followed by native `_scores` over
`mask * chain_M * chain_M_pos`. The adapter now uses that exact native scoring
call, not a new loss or score transformation. FastRelax therefore ranks by the
corrected sampled-sequence score. New output records identify this as
`proteinmpnn.sampled_sequence_autoregressive_nll.v1`. Historical output bytes
and their original source-conditioned scores are not rewritten or relabeled.
Do not compare the two score contracts as interchangeable observations.

The unused initial native scoring forward is deliberately retained. Its
`randn_1`, and potential backbone-augmentation draws inside the model, affect
later samples. CPU differential tests exercise a random-consuming inert model
with the actual native parser, featurizer, sampler orchestration and NLL helper:
sequence draws and final Torch RNG state agree before/after scoring correction.
No trained model, weights or GPU are used. Corrected scores may change selected
FastRelax trajectories; preserving incorrect rankings is not the intended fix.
The duplicate binder clone/renumber implementation is gone. Native geometry
serialization still runs after relaxation; no stale-backbone cache is introduced.

## Other MPNN implementations are not this score

BC2's native `score`/`seqid` average over all CA-resolved context, including fixed
target/framework residues; they are not binder-only scores or binding
probabilities. Its decode stage discards these fields and uses refold/filter
metrics. Do not replace this documented context-wide computation.

Ordinary Foundry LigandMPNN and ProtonPotts retain their native score/result
contracts. Protein-Hunter is retired/launch-blocked: retained wrapper list-chain,
UNK substitution, logits and Chai cysteine-default issues do not justify
reactivating it. Its retained terminal sequence reader now preserves insertion
codes, without changing launch policy or native unknown handling.

## Audited installed source identities (SHA-256)

- `util_protein_mpnn.py`: `8a95420aa1845c7e5a822ccbe869bbc6f72ea8d45988b2270ea9b44bceb8dbba`
- `ProteinMPNN/protein_mpnn_run.py`: `61f2c519a7f73fa12da9eb90da97b97ec2f8d5f31d42605639c7600cbd321cbe`
- `dl_interface_design_multi.py`: `4a549c9d8b6f2f321c753dfd52132de0ac69ffff95250cd13db2ebb8a43308a4`
