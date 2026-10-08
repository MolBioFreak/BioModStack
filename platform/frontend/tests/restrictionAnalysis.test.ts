import { describe, expect, it, vi } from 'vitest';
import './vitest/setup';

import {
    fetchRestrictionAnalysis,
    fetchRestrictionCatalog,
    fetchRestrictionProducts,
    parseRestrictionAnalysis,
    parseRestrictionCatalogPage,
    parseRestrictionDigestSimulation,
    parseRestrictionProducts,
    simulateRestrictionDigest,
} from '../src/lib/restrictionAnalysis';
import { createLatestAsyncResourceController } from '../src/lib/latestAsyncResource';

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
const PRODUCT_RELEASE = {
    release_id: 'bms-restriction-products-permission-pending-v1', release_version: '1.0.0',
    content_sha256: 'b'.repeat(64), raw_sha256: 'c'.repeat(64), schema_raw_sha256: 'd'.repeat(64),
    created_at: null, created_at_policy: 'omitted_until_permissioned_evidence_release',
    source_policy: 'no_runtime_scraping_written_redistribution_permission_required',
    redistribution_permission_state: 'unavailable', permission_receipt: null,
    product_evidence_available: false, record_count: 0, active_claim_count: 0,
    core_catalog_digest_binding: 'independent_no_binding',
};
const PRODUCTS = { schema: 'bms.molbio.restriction-products-page.v1', product_release: PRODUCT_RELEASE, items: [], next_cursor: null };
const RECORD = {
    enzyme_id: 'EcoRI', id_policy: 'canonical_name_v1_casefold_unique', canonical_name: 'EcoRI', aliases: [],
    recognition: { site_iupac: 'GAATTC', site_alternatives_iupac: ['GAATTC'], source_notation: 'G^AATTC', reverse_complement_iupac: 'GAATTC', reverse_complement_alternatives_iupac: ['GAATTC'], length_bp: 6, palindromic: true },
    cleavage: { status: 'known_double_strand', events: [{ top_offset: 1, bottom_offset: 5, overhang_kind: 'five_prime', overhang_length_nt: 4 }], nick: null, source_fields: { fst5: 1, fst3: -5, scd5: null, scd3: null } },
    enzyme_kind: 'double_strand_endonuclease', analysis_capability: 'digest_simulation', exclusion_reason: null,
    supplier_provenance: { reported_commercial: true, historical_supplier_codes: ['N'], availability_claim: 'not_evaluated' },
    relationships: { isoschizomer_group_id: 'iso:EcoRI', equischizomer_group_id: null, equischizomer_ids: [], neoschizomer_ids: [] },
    source: { kind: 'biopython_restriction_dictionary', record_id: 1, canonical_name: 'EcoRI', uri: null, package: 'biopython', package_version: '1.87', embedded_rebase_release: '404', dictionary_sha256: H, page_sha256: null, retrieved_on: null, record_modified_on: null, source_notation: 'G^AATTC' },
    record_sha256: H,
};
const SOURCE = { kind: 'inline_dna', name: 'fixture', sequence_id: null, revision_id: null, revision_number: null, content_sha256: H, content_length: 10, topology: 'linear' };
const CONTRIBUTOR = { enzyme_id: 'EcoRI', occurrence_id: 'occ:1', event_ordinal: 0, orientation: 'forward' };
const DSB = { ...CONTRIBUTOR, status: 'complete', top_boundary: 3, bottom_boundary: 7, top_boundary_unwrapped: 3, bottom_boundary_unwrapped: 7, top_winding: 0, bottom_winding: 0, overhang_kind: 'five_prime', overhang_length_nt: 4, overhang_sequence_5to3: 'AATT', overhang_source_strand: 'top', protruding_strand: 'top', contributor_group_id: 'cut:1', activity_assessment: 'not_evaluated', methylation_context: 'unknown' };
const OCCURRENCE = { occurrence_id: 'occ:1', occurrence_ordinal: 0, enzyme_id: 'EcoRI', canonical_name: 'EcoRI', orientation: 'forward', certainty: 'definite', recognition_pattern: 'GAATTC', site_start: 2, site_end_unwrapped: 8, site_segments: [[2, 8]], wraps_origin: false, matched_reference_sequence: 'GAATTC', double_strand_events: [DSB], nicks: [], limitations: [], activity_assessment: 'not_evaluated', methylation_context: 'unknown' };
const ANALYSIS = {
    schema: 'bms.molbio.restriction-analysis-response.v1', source: SOURCE, catalog: RECEIPT,
    request_sha256: H, result_sha256: H,
    analysis: { algorithm_id: 'bms-restriction-analysis', algorithm_version: '2.1.0', source_sha256: H, topology: 'linear', sequence_length: 10, catalog_sha256: H, scope_sha256: H, region_policy_sha256: H, resource_policy_receipt: POLICY, resource_policy_sha256: H, counts: { recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0 }, enzyme_summaries: [{ enzyme_id: 'EcoRI', canonical_name: 'EcoRI', analysis_capability: 'digest_simulation', cleavage_status: 'known_double_strand', recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0, limitations: [] }], occurrences: [OCCURRENCE], grouped_cleavages: [{ contributor_group_id: 'cut:1', status: 'complete', top_boundary: 3, bottom_boundary: 7, overhang_kind: 'five_prime', overhang_length_nt: 4, overhang_sequence_5to3: 'AATT', overhang_source_strand: 'top', protruding_strand: 'top', contributing_enzyme_ids: ['EcoRI'], contributors: [CONTRIBUTOR] }], warnings: [], limitations: [], result_sha256: H },
};
const END = (side: 'left' | 'right', kind = 'five_prime_overhang') => ({ kind, enzyme_created: true, side, protruding_strand: 'top', overhang_sequence_5to3: 'AATT', length_nt: 4, top_boundary: 3, bottom_boundary: 7, top_boundary_unwrapped: 3, bottom_boundary_unwrapped: 7, top_winding: 0, bottom_winding: 0, contributing_enzyme_ids: ['EcoRI'], contributors: [CONTRIBUTOR], contributor_group_id: 'cut:1' });
const DIGEST = { schema: 'bms.molbio.restriction-digest-simulation.v1', cleavage_state: 'fragmented', activity_assessment: 'not_evaluated', source: SOURCE, catalog: RECEIPT, selected_enzyme_ids: ['EcoRI'], selected_enzymes: [RECORD], analysis_algorithm_id: 'bms-restriction-analysis', analysis_algorithm_version: '2.1.0', analysis_result_sha256: H, digest_algorithm_id: 'bms-restriction-duplex-digest', digest_algorithm_version: '1.0.0', resource_policy: { schema: 'bms.molbio.restriction-digest-resource-policy.v1', policy_version: '1.0.0', selected_enzyme_maximum: 200, physical_cut_maximum: 4096, fragment_maximum: 4097, total_fragment_bases_maximum: 2_000_000, simulation_response_maximum_bytes: 10_000_000, saved_output_maximum: 4097, worker_concurrency: 2, queue_policy: 'reject_when_all_workers_busy', cancellation_policy: 'worker_continues_and_capacity_is_retained_until_completion' }, resource_policy_sha256: H, request_sha256: H, occurrences: [OCCURRENCE], cleavages: [{ cleavage_index: 0, contributor_group_id: 'cut:1', top_boundary: 3, bottom_boundary: 7, top_boundary_unwrapped: 3, bottom_boundary_unwrapped: 7, top_winding: 0, bottom_winding: 0, overhang_kind: 'five_prime', overhang_length_nt: 4, contributing_enzyme_ids: ['EcoRI'], contributors: [CONTRIBUTOR] }], fragments: [{ fragment_index: 0, topology: 'linear', top_strand_sequence: 'AAA', reference_span_bp: 3, source_segments: [[0, 3]], top_start_boundary: 0, top_end_boundary: 3, bottom_start_boundary: 0, bottom_end_boundary: 7, top_start_boundary_normalized: 0, top_end_boundary_normalized: 3, bottom_start_boundary_normalized: 0, bottom_end_boundary_normalized: 7, top_start_winding: 0, top_end_winding: 0, bottom_start_winding: 0, bottom_end_winding: 0, wraps_origin: false, left_end: { ...END('left', 'natural'), enzyme_created: false, protruding_strand: null, overhang_sequence_5to3: null, length_nt: 0, contributing_enzyme_ids: [], contributors: [], contributor_group_id: null }, right_end: END('right'), lineage_cleavage_group_ids: ['cut:1'], contributing_enzyme_ids: ['EcoRI'] }], warnings: [], limitations: [], simulation_sha256: H };

const clone = <T,>(value: T): T => JSON.parse(JSON.stringify(value));

describe('restriction API boundary', () => {
    it('accepts backend-shaped catalog/analysis/digest payloads without reordering', () => {
        const page = parseRestrictionCatalogPage({ schema: 'bms.molbio.restriction-catalog-page.v1', catalog: RECEIPT, items: [RECORD], next_cursor: null });
        expect(page.items[0]).toBe(RECORD);
        expect(parseRestrictionAnalysis(ANALYSIS).analysis.occurrences[0]).toBe(OCCURRENCE);
        expect(parseRestrictionDigestSimulation(DIGEST).fragments[0]).toBe(DIGEST.fragments[0]);
    });

    it('accepts exact empty product evidence and rejects contradictory or ungoverned claims', () => {
        expect(parseRestrictionProducts(PRODUCTS).product_release).toBe(PRODUCT_RELEASE);
        const contradictions = [
            { product_evidence_available: true }, { record_count: 1 }, { active_claim_count: 1 },
            { redistribution_permission_state: 'approved' }, { content_sha256: 'bad' },
        ];
        for (const change of contradictions) {
            const value = clone(PRODUCTS);
            Object.assign(value.product_release, change);
            expect(() => parseRestrictionProducts(value)).toThrow();
        }
        const record = clone(PRODUCTS);
        record.items = [{ product_id: 'fake' }];
        expect(() => parseRestrictionProducts(record)).toThrow();
    });

    it('keeps only the newest product evidence completion and sanitizes backend details', async () => {
        const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
        const older = deferred<Response>(); const newer = deferred<Response>(); let call = 0;
        const transport = vi.fn(() => [older, newer][call++].promise);
        const controller = createLatestAsyncResourceController(); const commits: string[] = [];
        const run = async (label: string) => { const token = controller.begin(); try { await fetchRestrictionProducts({ transport }); if (controller.isCurrent(token)) commits.push(label); } catch { if (controller.isCurrent(token)) commits.push(`${label}:error`); } };
        const oldRun = run('old'); const newRun = run('new');
        newer.resolve(new Response(JSON.stringify(PRODUCTS))); await newRun;
        older.resolve(new Response(JSON.stringify({ detail: { code: 'product_evidence_unavailable', message: '/srv/private/products.json' } }), { status: 503 })); await oldRun;
        expect(commits).toEqual(['new']);
        const failing = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code: 'product_evidence_unavailable', message: '/srv/private/products.json' } }), { status: 503 }));
        await expect(fetchRestrictionProducts({ transport: failing })).rejects.toThrow('Required restriction evidence is unavailable.');
        await expect(fetchRestrictionProducts({ transport: failing })).rejects.not.toThrow('/srv/private');
    });

    it.each([
        ['unknown field', () => ({ ...clone(ANALYSIS), extra: true })],
        ['schema drift', () => ({ ...clone(ANALYSIS), schema: 'v2' })],
        ['malformed hash', () => ({ ...clone(ANALYSIS), result_sha256: 'nope' })],
        ['non-finite number', () => { const value = clone(ANALYSIS); value.analysis.sequence_length = Number.NaN; return value; }],
        ['inconsistent counts', () => { const value = clone(ANALYSIS); value.analysis.counts.double_strand_break_count = 2; return value; }],
        ['duplicate occurrence identity', () => { const value = clone(ANALYSIS); value.analysis.occurrences.push(clone(value.analysis.occurrences[0])); return value; }],
        ['authority mismatch', () => { const value = clone(ANALYSIS); value.analysis.catalog_sha256 = 'b'.repeat(64); return value; }],
    ])('rejects %s', (_label, mutate) => expect(() => parseRestrictionAnalysis(mutate())).toThrow());

    it.each([
        ['result hash mismatch', () => { const value = clone(ANALYSIS); value.result_sha256 = 'b'.repeat(64); return value; }],
        ['occurrence summary mismatch', () => { const value = clone(ANALYSIS); value.analysis.occurrences[0].canonical_name = 'EcoRI-inconsistent'; return value; }],
        ['duplicate occurrence ordinal', () => { const value = clone(ANALYSIS); const occurrence = clone(value.analysis.occurrences[0]); occurrence.occurrence_id = 'occ:2'; occurrence.double_strand_events = []; value.analysis.occurrences.push(occurrence); value.analysis.counts.recognition_site_count_definite = 2; value.analysis.enzyme_summaries[0].recognition_site_count_definite = 2; return value; }],
        ['duplicate event ordinal', () => { const value = clone(ANALYSIS); const event = clone(value.analysis.occurrences[0].double_strand_events[0]); event.contributor_group_id = 'cut:duplicate'; value.analysis.occurrences[0].double_strand_events.push(event); value.analysis.counts.double_strand_break_count = 2; value.analysis.enzyme_summaries[0].double_strand_break_count = 2; const group = clone(value.analysis.grouped_cleavages[0]); group.contributor_group_id = 'cut:duplicate'; value.analysis.grouped_cleavages.push(group); return value; }],
        ['duplicate contributor identity', () => { const value = clone(ANALYSIS); value.analysis.grouped_cleavages[0].contributors.push(clone(value.analysis.grouped_cleavages[0].contributors[0])); return value; }],
        ['unresolved contributor event', () => { const value = clone(ANALYSIS); value.analysis.grouped_cleavages[0].contributors[0].event_ordinal = 99; return value; }],
        ['per-enzyme counts swapped while global totals match', () => {
            const value = clone(ANALYSIS);
            const other = clone(value.analysis.occurrences[0]);
            other.occurrence_id = 'occ:2';
            other.occurrence_ordinal = 1;
            other.enzyme_id = 'Other';
            other.canonical_name = 'Other';
            other.certainty = 'possible';
            other.double_strand_events = [];
            other.nicks = [{ enzyme_id: 'Other', occurrence_id: 'occ:2', event_ordinal: 0, orientation: 'forward', strand: 'top', status: 'complete', boundary: 9, boundary_unwrapped: 9, winding: 0, contributor_group_id: 'nick:2', activity_assessment: 'not_evaluated' }];
            value.analysis.occurrences.push(other);
            value.analysis.counts.recognition_site_count_possible = 1;
            value.analysis.counts.nick_count = 1;
            value.analysis.enzyme_summaries[0].recognition_site_count_definite = 0;
            value.analysis.enzyme_summaries[0].recognition_site_count_possible = 1;
            value.analysis.enzyme_summaries[0].double_strand_break_count = 0;
            value.analysis.enzyme_summaries[0].nick_count = 1;
            value.analysis.enzyme_summaries.push({ ...clone(value.analysis.enzyme_summaries[0]), enzyme_id: 'Other', canonical_name: 'Other', recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0 });
            return value;
        }],
        ['event orientation differs from owning occurrence', () => { const value = clone(ANALYSIS); value.analysis.occurrences[0].double_strand_events[0].orientation = 'reverse'; value.analysis.grouped_cleavages[0].contributors[0].orientation = 'reverse'; return value; }],
        ['same occurrence repeats event ordinal under opposite orientations', () => { const value = clone(ANALYSIS); const event = clone(value.analysis.occurrences[0].double_strand_events[0]); event.orientation = 'reverse'; event.contributor_group_id = 'cut:opposite'; value.analysis.occurrences[0].double_strand_events.push(event); value.analysis.counts.double_strand_break_count = 2; value.analysis.enzyme_summaries[0].double_strand_break_count = 2; const group = clone(value.analysis.grouped_cleavages[0]); group.contributor_group_id = 'cut:opposite'; group.contributors[0].orientation = 'reverse'; value.analysis.grouped_cleavages.push(group); return value; }],
    ])('rejects relational analysis contradiction: %s', (_label, mutate) => expect(() => parseRestrictionAnalysis(mutate())).toThrow());

    it.each([
        ['unselected occurrence enzyme', () => { const value = clone(DIGEST); value.occurrences[0].enzyme_id = 'Other'; value.occurrences[0].double_strand_events[0].enzyme_id = 'Other'; return value; }],
        ['unselected cleavage contributor enzyme', () => { const value = clone(DIGEST); value.cleavages[0].contributors[0].enzyme_id = 'Other'; return value; }],
        ['duplicate cleavage ordinal', () => { const value = clone(DIGEST); const duplicate = clone(value.cleavages[0]); duplicate.contributor_group_id = 'cut:2'; value.cleavages.push(duplicate); return value; }],
        ['unresolved cleavage contributor event', () => { const value = clone(DIGEST); value.cleavages[0].contributors[0].event_ordinal = 99; return value; }],
        ['unresolved fragment lineage', () => { const value = clone(DIGEST); value.fragments[0].lineage_cleavage_group_ids = ['cut:missing']; return value; }],
        ['duplicate fragment lineage', () => { const value = clone(DIGEST); value.fragments[0].lineage_cleavage_group_ids.push('cut:1'); return value; }],
        ['digest occurrence canonical name differs from selected record', () => { const value = clone(DIGEST); value.occurrences[0].canonical_name = 'EcoRI-inconsistent'; return value; }],
        ['fragment end contributor belongs to a different contributor group', () => { const value = clone(DIGEST); const cleavage = clone(value.cleavages[0]); cleavage.cleavage_index = 1; cleavage.contributor_group_id = 'cut:2'; cleavage.contributing_enzyme_ids = []; cleavage.contributors = []; value.cleavages.push(cleavage); value.fragments[0].right_end.contributor_group_id = 'cut:2'; return value; }],
        ['fragment end enzyme IDs omit a resolved contributor enzyme', () => { const value = clone(DIGEST); value.fragments[0].right_end.contributing_enzyme_ids = []; return value; }],
        ['fragment end enzyme IDs add a selected non-contributor enzyme', () => { const value = clone(DIGEST); const other = clone(value.selected_enzymes[0]); other.enzyme_id = 'Other'; other.canonical_name = 'Other'; value.selected_enzyme_ids.push('Other'); value.selected_enzymes.push(other); value.fragments[0].right_end.contributing_enzyme_ids.push('Other'); return value; }],
    ])('rejects relational digest contradiction: %s', (_label, mutate) => expect(() => parseRestrictionDigestSimulation(mutate())).toThrow());

    it.each([
        ['linear DSB boundary outside source axis', () => { const value = clone(ANALYSIS); const event = value.analysis.occurrences[0].double_strand_events[0]; event.top_boundary = 11; event.top_boundary_unwrapped = 11; value.analysis.grouped_cleavages[0].top_boundary = 11; return value; }],
        ['linear nick boundary outside source axis', () => { const value = clone(ANALYSIS); const occurrence = value.analysis.occurrences[0]; occurrence.double_strand_events = []; occurrence.nicks = [{ enzyme_id: 'EcoRI', occurrence_id: 'occ:1', event_ordinal: 0, orientation: 'forward', strand: 'top', status: 'complete', boundary: 11, boundary_unwrapped: 11, winding: 0, contributor_group_id: 'cut:1', activity_assessment: 'not_evaluated' }]; value.analysis.counts.double_strand_break_count = 0; value.analysis.counts.nick_count = 1; value.analysis.enzyme_summaries[0].double_strand_break_count = 0; value.analysis.enzyme_summaries[0].nick_count = 1; return value; }],
        ['circular DSB normalized boundary inconsistent with unwrapped geometry', () => { const value = clone(ANALYSIS); value.source.topology = 'circular'; value.analysis.topology = 'circular'; const event = value.analysis.occurrences[0].double_strand_events[0]; event.top_boundary = 10; return value; }],
        ['circular nick winding inconsistent with unwrapped geometry', () => { const value = clone(ANALYSIS); value.source.topology = 'circular'; value.analysis.topology = 'circular'; const occurrence = value.analysis.occurrences[0]; occurrence.double_strand_events = []; occurrence.nicks = [{ enzyme_id: 'EcoRI', occurrence_id: 'occ:1', event_ordinal: 0, orientation: 'forward', strand: 'top', status: 'complete', boundary: 1, boundary_unwrapped: 11, winding: 0, contributor_group_id: 'cut:1', activity_assessment: 'not_evaluated' }]; value.analysis.counts.double_strand_break_count = 0; value.analysis.counts.nick_count = 1; value.analysis.enzyme_summaries[0].double_strand_break_count = 0; value.analysis.enzyme_summaries[0].nick_count = 1; return value; }],
        ['occurrence policy maximum', () => { const value = clone(ANALYSIS); value.analysis.resource_policy_receipt.occurrence_maximum = 0; return value; }],
        ['event policy maximum', () => { const value = clone(ANALYSIS); value.analysis.resource_policy_receipt.event_maximum = 0; return value; }],
        ['source length policy maximum', () => { const value = clone(ANALYSIS); value.analysis.resource_policy_receipt.sequence_length_maximum = 9; return value; }],
        ['analysis response byte maximum', () => { const value = clone(ANALYSIS); value.analysis.resource_policy_receipt.response_maximum_bytes = 1; return value; }],
    ])('rejects analysis bound violation: %s', (_label, mutate) => expect(() => parseRestrictionAnalysis(mutate())).toThrow());

    it.each([
        ['selected enzyme maximum', () => { const value = clone(DIGEST); value.resource_policy.selected_enzyme_maximum = 0; return value; }],
        ['physical cut maximum', () => { const value = clone(DIGEST); value.resource_policy.physical_cut_maximum = 0; return value; }],
        ['fragment maximum', () => { const value = clone(DIGEST); value.resource_policy.fragment_maximum = 0; return value; }],
        ['saved output maximum', () => { const value = clone(DIGEST); value.resource_policy.saved_output_maximum = 0; return value; }],
        ['total fragment bases maximum', () => { const value = clone(DIGEST); value.resource_policy.total_fragment_bases_maximum = 2; return value; }],
        ['digest response byte maximum', () => { const value = clone(DIGEST); value.resource_policy.simulation_response_maximum_bytes = 1; return value; }],
        ['fragment source segment outside source', () => { const value = clone(DIGEST); value.fragments[0].source_segments = [[0, 11]]; return value; }],
        ['fragment topology mismatch', () => { const value = clone(DIGEST); value.fragments[0].topology = 'circular'; return value; }],
        ['fragment normalized boundary outside source', () => { const value = clone(DIGEST); value.fragments[0].top_end_boundary_normalized = 11; return value; }],
        ['fragment winding inconsistent with linear source', () => { const value = clone(DIGEST); value.fragments[0].top_end_winding = 1; return value; }],
    ])('rejects digest bound violation: %s', (_label, mutate) => expect(() => parseRestrictionDigestSimulation(mutate())).toThrow());

    it.each([
        [500, 'analysis_failed', 'Restriction analysis is unavailable.'],
        [413, 'request_too_large', 'Restriction request exceeds the supported limits.'],
        [418, 'unknown_backend_code', 'Restriction service request failed.'],
    ])('sanitizes backend error %s/%s', async (status, code, expected) => {
        const internalDetail = '/srv/internal/database.sqlite stack trace internal query plan';
        const transport = vi.fn().mockResolvedValue(new Response(JSON.stringify({ detail: { code, message: internalDetail } }), { status }));
        await expect(fetchRestrictionCatalog({ transport })).rejects.toThrow(expected);
        await expect(fetchRestrictionCatalog({ transport })).rejects.not.toThrow(internalDetail);
    });

    it('sends only stable enzyme IDs and source/catalog authority', async () => {
        const revisionSource = { ...SOURCE, kind: 'molecular_revision', name: 'fixture', sequence_id: 'seq-1', revision_id: 'rev-1', revision_number: 1 };
        const analysisResponse = { ...ANALYSIS, source: revisionSource };
        const digestResponse = { ...DIGEST, source: revisionSource };
        const transport = vi.fn()
            .mockResolvedValueOnce(new Response(JSON.stringify({ schema: 'bms.molbio.restriction-catalog-page.v1', catalog: RECEIPT, items: [RECORD], next_cursor: null })))
            .mockResolvedValueOnce(new Response(JSON.stringify(analysisResponse)))
            .mockResolvedValueOnce(new Response(JSON.stringify(digestResponse)));
        await fetchRestrictionCatalog({ transport });
        const source = { kind: 'molecular_revision' as const, sequence_id: 'seq-1', revision_id: 'rev-1', expected_content_sha256: H, topology: 'linear' as const };
        await fetchRestrictionAnalysis({ source, catalog: { catalog_id: 'catalog-v1', expected_catalog_sha256: H }, enzymeIds: ['EcoRI'], transport });
        await simulateRestrictionDigest({ source, catalog: { catalog_id: 'catalog-v1', expected_catalog_sha256: H }, enzymeIds: ['EcoRI'], transport });
        for (const call of transport.mock.calls.slice(1)) {
            const body = JSON.parse(String(call[1].body));
            const forbidden = new Set(['recognition_pattern', 'site', 'cut_index', 'top_offset', 'bottom_offset', 'fragments', 'overhang_sequence_5to3', 'result_sha256', 'evidence']);
            const visit = (value: unknown): void => {
                if (Array.isArray(value)) value.forEach(visit);
                else if (value && typeof value === 'object') Object.entries(value).forEach(([key, child]) => { expect(forbidden.has(key)).toBe(false); visit(child); });
            };
            visit(body);
            expect(body.scope?.enzyme_ids ?? body.enzyme_ids).toEqual(['EcoRI']);
        }
    });

    it('prevents an older completion from replacing newer authority even when transport ignores AbortSignal', async () => {
        const deferred = <T,>() => { let resolve!: (value: T) => void; const promise = new Promise<T>((done) => { resolve = done; }); return { promise, resolve }; };
        const older = deferred<Response>();
        const newer = deferred<Response>();
        const transports = [older, newer];
        let call = 0;
        const transport = vi.fn((_input: RequestInfo | URL, _init?: RequestInit) => transports[call++].promise);
        const controller = createLatestAsyncResourceController();
        const commits: string[] = [];
        const run = async (label: string) => {
            const token = controller.begin();
            commits.push(`${label}:loading`);
            try {
                await fetchRestrictionCatalog({ transport, signal: new AbortController().signal });
                if (controller.isCurrent(token)) commits.push(`${label}:success`);
            } catch {
                if (controller.isCurrent(token)) commits.push(`${label}:error`);
            } finally {
                if (controller.isCurrent(token)) commits.push(`${label}:settled`);
            }
        };
        const oldRun = run('old');
        const newRun = run('new');
        newer.resolve(new Response(JSON.stringify({ schema: 'bms.molbio.restriction-catalog-page.v1', catalog: RECEIPT, items: [RECORD], next_cursor: null })));
        await newRun;
        older.resolve(new Response('not-json'));
        await oldRun;
        expect(commits).toEqual(['old:loading', 'new:loading', 'new:success', 'new:settled']);
    });

    it('prevents completion after lifecycle disposal', async () => {
        let resolve!: (value: Response) => void;
        const transport = vi.fn(() => new Promise<Response>((done) => { resolve = done; }));
        const controller = createLatestAsyncResourceController();
        const token = controller.begin();
        const commits: string[] = [];
        const pending = fetchRestrictionCatalog({ transport }).then(() => { if (controller.isCurrent(token)) commits.push('success'); }).finally(() => { if (controller.isCurrent(token)) commits.push('settled'); });
        controller.dispose();
        resolve(new Response(JSON.stringify({ schema: 'bms.molbio.restriction-catalog-page.v1', catalog: RECEIPT, items: [RECORD], next_cursor: null })));
        await pending;
        expect(commits).toEqual([]);
    });
});

export { ANALYSIS, DIGEST, H, RECEIPT, RECORD };
