const H = 'a'.repeat(64);
const POLICY = {
    schema: 'bms.molbio.restriction-analysis-resource-policy.v1', policy_version: '1.1.0',
    scan_work_formula_id: 'candidate-starts-times-motif-width', scan_work_formula_version: '1.0.0',
    sequence_length_maximum: 1_000_000, explicit_enzyme_maximum: 200, region_maximum: 64,
    actual_scan_pattern_maximum: 400, scan_work_maximum: 1_000_000, occurrence_maximum: 10_000,
    event_maximum: 20_000, response_maximum_bytes: 1_000_000, response_base_budget_bytes: 1,
    response_occurrence_budget_bytes: 2, response_event_budget_bytes: 3, worker_concurrency: 2,
    queue_policy: 'reject_when_all_workers_busy', timeout_seconds: 30,
    cancellation_policy: 'worker_continues_and_capacity_is_retained_until_completion',
    cache_entry_maximum: 10, cache_total_weight_maximum_bytes: 1000,
    cache_result_weight_maximum_bytes: 100, cache_weight_formula_id: 'canonical-json-entry-and-complete-cache-graph',
    cache_weight_formula_version: '2.0.0',
};
const BOUNDS = {
    default_limit: 200, maximum_limit: 200, query_max_length: 128,
    analysis_inline_sequence_max_length: 1_000_000, analysis_explicit_enzyme_maximum: 200,
    analysis_region_maximum: 64, analysis_scan_pattern_maximum: 400,
    analysis_scan_work_maximum: 1_000_000, analysis_occurrence_maximum: 10_000,
    analysis_event_maximum: 20_000, analysis_response_maximum_bytes: 1_000_000,
    analysis_cache_maximum_entries: 10, analysis_cache_maximum_total_weight_bytes: 1000,
    analysis_cache_maximum_result_weight_bytes: 100,
};
const COUNTS = { total: 1, geometry_ready: 1, commercial_geometry_ready: 1, unknown_geometry: 0, nicking: 0, two_event_double_strand: 0 };
const RECEIPT = {
    catalog_id: 'catalog-v1', catalog_sha256: H, source_release: 'REBASE 404', counts: COUNTS,
    source_year: 2024, source_age_years: 2, source_age_notice: 'historical source',
    supplier_code_notice: 'provenance only', bounds: BOUNDS, resource_policy: POLICY,
    resource_policy_sha256: H, analysis_enabled: true, digest_enabled: true,
};
const RECORD = {
    enzyme_id: 'EcoRI', id_policy: 'canonical_name_v1_casefold_unique', canonical_name: 'EcoRI', aliases: [],
    recognition: { site_iupac: 'GAATTC', site_alternatives_iupac: ['GAATTC'], source_notation: 'G^AATTC', reverse_complement_iupac: 'GAATTC', reverse_complement_alternatives_iupac: ['GAATTC'], length_bp: 6, palindromic: true },
    cleavage: { status: 'known_double_strand', events: [{ top_offset: 1, bottom_offset: 5, overhang_kind: 'five_prime', overhang_length_nt: 4 }], nick: null, source_fields: { fst5: 1, fst3: -5, scd5: null, scd3: null } },
    enzyme_kind: 'double_strand_endonuclease', analysis_capability: 'digest_simulation', golden_gate_compatible: false, exclusion_reason: null,
    supplier_provenance: { reported_commercial: true, historical_supplier_codes: ['N'], availability_claim: 'not_evaluated' },
    relationships: { isoschizomer_group_id: 'iso:EcoRI', equischizomer_group_id: null, equischizomer_ids: [], neoschizomer_ids: [] },
    source: { kind: 'biopython_restriction_dictionary', record_id: 1, canonical_name: 'EcoRI', uri: null, package: 'biopython', package_version: '1.87', embedded_rebase_release: '404', dictionary_sha256: H, page_sha256: null, retrieved_on: null, record_modified_on: null, source_notation: 'G^AATTC' },
    record_sha256: H,
};

const SUMMARY = {
    enzyme_id: RECORD.enzyme_id, canonical_name: RECORD.canonical_name, aliases: RECORD.aliases,
    site_iupac: RECORD.recognition.site_iupac, site_alternatives_iupac: RECORD.recognition.site_alternatives_iupac,
    palindromic: true, cleavage_status: 'known_double_strand', overhang_kinds: ['five_prime'], nick_strand: null,
    enzyme_kind: RECORD.enzyme_kind, analysis_capability: RECORD.analysis_capability,
    golden_gate_compatible: false, exclusion_reason: null, reported_commercial: true, historical_supplier_codes: ['N'],
};
export { RECEIPT, RECORD, SUMMARY };
