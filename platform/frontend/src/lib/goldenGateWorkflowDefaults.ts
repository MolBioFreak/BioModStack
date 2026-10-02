/** Initial values from native model defaults. Applied only by explicit authoring actions. */
export const thermodynamicsDefaults = {
  "algorithm": "nn_santalucia_hicks_2004",
  "salt_correction": "owczarzy_2008",
  "primer_concentration_nM": 250.0,
  "template_concentration_nM": 0.0,
  "na_mM": 50.0,
  "k_mM": 0.0,
  "tris_mM": 0.0,
  "mg_mM": 1.5,
  "dntps_mM": 0.6,
  "dmso_percent": 0.0,
  "formamide_percent": 0.0,
  "self_complementary": false
} as const;
export const domesticationSettingsDefaults = {
  "schema_version": "golden-gate-domestication/v1",
  "enabled": false,
  "editable_regions": [],
  "protected_regions": [],
  "cds": [],
  "unwanted_sites": [],
  "algorithm": "minimal_substitutions",
  "candidate_budget": 100000,
  "max_edits": null
} as const;
export const reactionSettingsDefaults = {
  "schema_version": "golden-gate-reaction/v1",
  "mass_basis_g_per_mol_bp": 660,
  "reference_part_id": null,
  "total_volume_uL": null,
  "reaction_count": 1,
  "mastermix_overage_percent": 0,
  "water_in_mastermix": true,
  "rounding_step_uL": null,
  "minimum_transfer_uL": null,
  "cycling": null
} as const;
export const primerSelectionSettingsDefaults = {
  "primer_min_length": 18,
  "primer_max_length": 28,
  "product_min_length": 120,
  "product_max_length": 1500,
  "flank_search_span": 80,
  "gc_min_percent": 35.0,
  "gc_max_percent": 65.0,
  "tm_target_c": 62.0,
  "tm_max_delta_c": 3.0,
  "gc_clamp_min": 1,
  "max_poly_x": 4,
  "max_pairs": 8
} as const;
export const searchControlsDefaults = {
  "seed": 0,
  "evaluation_budget": 10000,
  "restarts": 8,
  "exact_limit": 10000,
  "alternatives": 5,
  "unique_classes": true,
  "exclude_palindromes": true,
  "ranking_mode": "empirical"
} as const;
export const fidelitySettingsDefaults = {
  "dataset_id": null,
  "condition_use": "reference",
  "include_pair_observations": false
} as const;
export const reactionPartDefaults = {
  "length_bp": null,
  "amount": null,
  "ratio_to_reference": null,
  "stock": null,
  "dilution": {
    "factor": 1,
    "preparation_volume_uL": null
  },
  "in_mastermix": false,
  "phosphorylation": "unknown",
  "purification": null
} as const;
export const reactionReagentDefaults = {
  "formulation": null,
  "volume_uL": null,
  "stock_multiple": null,
  "final_multiple": null,
  "in_mastermix": true
} as const;
export const cDSConstraintDefaults = {
  "region": {
    "wraps_origin": false
  },
  "strand": 1,
  "frame": 0,
  "genetic_code": 1,
  "initiation": "preserve",
  "allowed_start_codons": [],
  "stop_policy": "preserve"
} as const;
