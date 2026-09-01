import { describe, expect, it, vi } from 'vitest';
import './vitest/setup';

import {
    fetchRestrictionAnalysis,
    fetchRestrictionCatalog,
    parseRestrictionAnalysis,
    parseRestrictionCatalogPage,
    parseRestrictionDigestSimulation,
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

    it.each([
        ['unknown field', () => ({ ...clone(ANALYSIS), extra: true })],
        ['schema drift', () => ({ ...clone(ANALYSIS), schema: 'v2' })],
        ['malformed hash', () => ({ ...clone(ANALYSIS), result_sha256: 'nope' })],
        ['non-finite number', () => { const value = clone(ANALYSIS); value.analysis.sequence_length = Number.NaN; return value; }],
        ['inconsistent counts', () => { const value = clone(ANALYSIS); value.analysis.counts.double_strand_break_count = 2; return value; }],
        ['duplicate occurrence identity', () => { const value = clone(ANALYSIS); value.analysis.occurrences.push(clone(value.analysis.occurrences[0])); return value; }],
        ['authority mismatch', () => { const value = clone(ANALYSIS); value.analysis.catalog_sha256 = 'b'.repeat(64); return value; }],
    ])('rejects %s', (_label, mutate) => expect(() => parseRestrictionAnalysis(mutate())).toThrow());

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
