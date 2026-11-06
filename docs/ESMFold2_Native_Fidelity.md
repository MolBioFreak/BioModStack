# ESMFold2 native input and execution semantics

Authority: installed Biohub `esm` commit `c94ed8d763bbd7088b296949e5b401e8ea12073a`, Biohub `transformers` commit `3a8956fb4d4ea16b0ec8e71deef2c2909b6a5cbf`. These are the runtime-definition pins, not a claim that an arbitrary deployed image was attested.

## Input dialect

`run_esmfold2_inference.py` accepts component arrays or a document with `components` (native synonym `sequences`), `covalent_bonds`, and `distogram_conditioning`.

- Polymer modifications are `{position, ccd}`, with **zero-based** sequence positions. No Protenix modification dialect or SMILES modifications are implied.
- Bonds use `chain_id1,res_idx1,atom_idx1,chain_id2,res_idx2,atom_idx2`. Both residue and per-residue atom-list indices are **zero-based**. Atom-name endpoints are not this native schema.
- Distograms are `{chain_id, distogram: number[][]}` records. These are not template-coordinate files or generic constraints.
- Inline protein MSA is `msa: {sequences: string[]}`; native `msa: null` means omitted/query-only preparation. An explicitly empty sequence array remains an empty native MSA object, not an authorization to search a provider. Native preparation limitations remain native limitations.
- Explicit ID lists retain order. BMS `count` expands the list to the requested copy count using unique IDs, reserving other authored IDs first. Existing explicit IDs are never removed or renamed.
- DNA/RNA letters are not interconverted by BMS. Native interpretation of unusual letters remains the installed model's responsibility. Standard DNA/RNA and DNA-only preparation are tested; protein-free inference accuracy is not established.

PDB input is **sequence/chemical-identity source only**. Coordinates are never templates. Source chain order, supported modified-residue CCD identities and non-water ligand CCD identities are retained. The bridge uses Biopython's residue-parent table rather than silently deleting known modified amino acids. The shared polymer-only helper remains available to sequence-authoring callers; the ESM PDB inference path explicitly includes ligand context. This does not reconstruct arbitrary PDB CONECT/LINK chemistry. Explicit native bonds belong in the component document.

Selected blind-pose candidates are deliberate canonical sequence products. Historical modifications are not grafted back onto those designed sequences. The separately declared target is independent context, so its supported modifications are retained. The candidate never supplies target coordinates or target sequence authority.

## Fast, Full and defaults

The inspected local Fast and Full configurations are both release implementations. Fast disables the dedicated MSA encoder, while Full enables it. Both execute shared MSA-derived profile/deletion-input logic. Therefore, “Fast is not MSA-conditioned” is inaccurate. BMS's existing hosted-MSA policy selects Full; that is a policy distinction, not a claim that supplied MSA has no possible effect on Fast. No Full/Fast accuracy equivalence is claimed.

Existing BMS defaults are intentionally caller-specific: runner/API ordinary 3 loops / 50 steps / 1 sample; marked compiler fallback 1 / 5 / 1; antibody validation 1 / 25 / 1. These are BMS choices, not the installed checkpoint's advertised 3 / 14 / 32. Explicit controls and seed zero win. No quality reduction or default seed is introduced.

## Batch and confidence

`run_esmfold2_batch.py` consumes per-input argv/receipts or staged validation PDB/MSA/settings. It matches staged alignments by exact query sequence and does not overwrite authored inline MSA. Each request retains all diffusion samples and its independent receipt.

Explicitly seeded calls reuse an eval model for the same model/device/offline identity. The installed processor separately seeds preparation and diffusion sampling on every fold. Unseeded calls retain fresh subprocesses because removing initialization could change RNG behavior. CPU inert-boundary tests prove initialization count and argument forwarding, **not numerical inference equivalence or a measured performance improvement**.

Native decoded confidence arrays and scalar dialect are retained regardless of whether a settings receipt exists. Validation normalization carries confidence NPZ peers into publication. PAE remains on the native token axis; no token-to-CIF residue mapping is invented, and iPTM is never labeled ipSAE.

## Limits and qualification

Pocket input is dropped by the installed cleaning implementation and is not advertised as effective support. There is no installed generic template-file field, FILE ligand syntax, atom-name bond endpoint contract or implemented SMILES modification support.

`tests/esmfold2_native_fidelity_probe.py` compares actual CPU preparation tensors against independently deserialized native requests in the pinned image, with a read-only CCD asset and no checkpoints. Its writer section explicitly replaces the model boundary with test fixtures; those structures/confidences are not predictions. `tests/test_esmfold2_fidelity.py` covers compiler, selected-source authority, receipt scoping and seeded/unseeded batch dispatch. UI/API persistence, remote execution, inference accuracy and live deployment require separate owner qualification.
