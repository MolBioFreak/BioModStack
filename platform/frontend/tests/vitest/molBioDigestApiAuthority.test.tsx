import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import './setup';
import { DigestPanel } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import type { RestrictionAnalysisBatch, RestrictionCatalogReceipt, RestrictionDigestSimulation, RestrictionProductReleaseReceipt, RestrictionRecord } from '../../src/lib/restrictionAnalysis';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';

const sequence: SequenceData = { name: 'API fixture', sequence: 'TTGAATTCAA', circular: false, sequenceType: 'dna', features: [] };
const catalog = { catalog_id: 'catalog-v1', catalog_sha256: 'a'.repeat(64), source_release: 'REBASE 404', counts: { total: 1, geometry_ready: 1, commercial_geometry_ready: 1, unknown_geometry: 0, nicking: 0, two_event_double_strand: 0 } } as RestrictionCatalogReceipt;
const products = { release_id: 'bms-restriction-products-permission-pending-v1', release_version: '1.0.0', content_sha256: 'c'.repeat(64), raw_sha256: 'd'.repeat(64), schema_raw_sha256: 'e'.repeat(64), created_at: null, created_at_policy: 'omitted_until_permissioned_evidence_release', source_policy: 'no_runtime_scraping_written_redistribution_permission_required', redistribution_permission_state: 'unavailable', permission_receipt: null, product_evidence_available: false, record_count: 0, active_claim_count: 0, core_catalog_digest_binding: 'independent_no_binding' } as RestrictionProductReleaseReceipt;
const record = { enzyme_id: 'EcoRI', canonical_name: 'EcoRI', aliases: [], recognition: { site_iupac: 'GAATTC', site_alternatives_iupac: ['GAATTC'], palindromic: true }, cleavage: { status: 'known_double_strand', events: [{ top_offset: 1, bottom_offset: 5, overhang_kind: 'five_prime' }], nick: null }, enzyme_kind: 'double_strand_endonuclease', analysis_capability: 'digest_simulation', supplier_provenance: { reported_commercial: true, historical_supplier_codes: [] } } as unknown as RestrictionRecord;
const occurrence = { occurrence_id: 'occ:1', occurrence_ordinal: 0, enzyme_id: 'EcoRI', canonical_name: 'EcoRI', orientation: 'forward', certainty: 'definite', site_start: 2, site_end_unwrapped: 8, site_segments: [[2, 8]], wraps_origin: false, double_strand_events: [{ enzyme_id: 'EcoRI', occurrence_id: 'occ:1', event_ordinal: 0, orientation: 'forward', status: 'complete', top_boundary: 3, bottom_boundary: 7, top_boundary_unwrapped: 3, bottom_boundary_unwrapped: 7, contributor_group_id: 'cut:1' }], nicks: [], limitations: [] };
const analysis = { authority_key: 'b'.repeat(64), chunks: [{ enzyme_ids: ['EcoRI'], request_sha256: 'a'.repeat(64), result_sha256: 'b'.repeat(64), analysis_result_sha256: 'b'.repeat(64) }], analysis: { counts: { recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0 }, enzyme_summaries: [{ enzyme_id: 'EcoRI', canonical_name: 'EcoRI', analysis_capability: 'digest_simulation', cleavage_status: 'known_double_strand', recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0, limitations: [] }], occurrences: [occurrence] } } as unknown as RestrictionAnalysisBatch;
const recognitionOnly = { ...record, enzyme_id: 'MysteryI', canonical_name: 'MysteryI', analysis_capability: 'recognition_only', cleavage: { status: 'unknown', events: [], nick: null } } as unknown as RestrictionRecord;
const bamRecord = { ...record, enzyme_id: 'BamHI', canonical_name: 'BamHI', recognition: { ...record.recognition, site_iupac: 'GGATCC', site_alternatives_iupac: ['GGATCC'] } } as unknown as RestrictionRecord;
const mixedAnalysis = { ...analysis, analysis: { ...analysis.analysis, counts: { ...analysis.analysis.counts, recognition_site_count_definite: 3, double_strand_break_count: 3 }, enzyme_summaries: [...analysis.analysis.enzyme_summaries, { enzyme_id: 'BamHI', canonical_name: 'BamHI', analysis_capability: 'digest_simulation', cleavage_status: 'known_double_strand', recognition_site_count_definite: 2, recognition_site_count_possible: 0, double_strand_break_count: 2, nick_count: 0, limitations: [] }] } } as unknown as RestrictionAnalysisBatch;
const end = (side: 'left' | 'right', kind: 'five_prime_overhang' | 'three_prime_overhang' | 'blunt') => ({ kind, enzyme_created: true, side, protruding_strand: kind === 'blunt' ? null : 'top', overhang_sequence_5to3: kind === 'blunt' ? null : 'AATT', length_nt: kind === 'blunt' ? 0 : 4, contributing_enzyme_ids: ['EcoRI'], contributor_group_id: 'cut:1' });
const simulation = { fragments: [
    { fragment_index: 0, reference_span_bp: 3, source_segments: [[0, 3]], left_end: end('left', 'five_prime_overhang'), right_end: end('right', 'three_prime_overhang') },
    { fragment_index: 1, reference_span_bp: 7, source_segments: [[3, 10]], left_end: end('left', 'blunt'), right_end: end('right', 'blunt') },
] } as unknown as RestrictionDigestSimulation;

describe('DigestPanel backend authority', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;
    afterEach(async () => { if (root) await act(async () => root?.unmount()); container?.remove(); });

    it('keeps catalog/all-analysis authority and digest highlights stable across map-only selection changes', async () => {
        const toolkitSource = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/MolBioToolkitV2.tsx'), 'utf8');
        const authorityEffect = toolkitSource.slice(toolkitSource.indexOf('const restrictionSource ='), toolkitSource.indexOf('const runRestrictionDigest ='));
        expect(authorityEffect).not.toContain('restrictionSelectionKey');
        expect(authorityEffect).toContain('}, [restrictionSource]);');
        expect(authorityEffect).toContain('fetchRestrictionAnalysisBatch');

        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const highlight = vi.fn();
        const mapChange = vi.fn();
        await act(async () => root?.render(<DigestPanel sequenceData={sequence} sequenceId={null} onHighlight={highlight} selectedEnzymes={[]} onEnzymesChange={mapChange} catalog={catalog} productEvidence={products} catalogRecords={[record]} analysis={analysis} authorityLoading={false} authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null} onDigestSelectionChange={vi.fn()} onSimulateDigest={vi.fn()} />));
        const highlighted = highlight.mock.calls.at(-1)?.[0];
        const map = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Map');
        await act(async () => map?.click());
        expect(mapChange).toHaveBeenCalledWith(['EcoRI']);
        expect(highlight.mock.calls.at(-1)?.[0]).toBe(highlighted);
        expect(container.querySelector('[data-fragment-index="0"]')).toBeTruthy();
    });

    it('renders complete catalog discovery and exact ordered chunk hashes', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        await act(async () => root?.render(<DigestPanel sequenceData={sequence} sequenceId={null} onHighlight={vi.fn()} selectedEnzymes={[]} onEnzymesChange={vi.fn()} catalog={catalog} productEvidence={products} catalogRecords={[record, recognitionOnly]} analysis={analysis} authorityLoading={false} authorityError={null} digestSimulation={null} digestLoading={false} digestError={null} onDigestSelectionChange={vi.fn()} onSimulateDigest={vi.fn()} />));
        const all = [...container.querySelectorAll('button')].find((button) => button.textContent === 'All');
        await act(async () => all?.click());
        expect(container.textContent).toContain('MysteryI');
        expect(container.textContent).toContain('geometry unavailable');
        expect(container.querySelector(`[data-restriction-chunk-result-sha256="${'b'.repeat(64)}"]`)).toBeTruthy();
    });

    it('clears fragment highlights when digest authority disappears and on unmount', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const highlight = vi.fn();
        const props = { sequenceData: sequence, sequenceId: null, onHighlight: highlight, selectedEnzymes: [], onEnzymesChange: vi.fn(), catalog, productEvidence: products, catalogRecords: [record], analysis, authorityLoading: false, authorityError: null, digestLoading: false, onDigestSelectionChange: vi.fn(), onSimulateDigest: vi.fn() };
        await act(async () => root?.render(<DigestPanel {...props} digestSimulation={simulation} digestError={null} />));
        expect(highlight.mock.calls.at(-1)?.[0]).toHaveLength(2);
        await act(async () => root?.render(<DigestPanel {...props} digestSimulation={null} digestError="Restriction digest is unavailable." />));
        expect(highlight.mock.calls.at(-1)?.[0]).toEqual([]);
        const callsBeforeUnmount = highlight.mock.calls.length;
        await act(async () => root?.unmount());
        root = undefined;
        expect(highlight.mock.calls.length).toBeGreaterThan(callsBeforeUnmount);
        expect(highlight.mock.calls.at(-1)?.[0]).toEqual([]);
    });

    it('shows aggregate match semantics without an unrelated supplier warning', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const simulate = vi.fn();
        await act(async () => root?.render(<DigestPanel sequenceData={sequence} sequenceId={null} onHighlight={vi.fn()} selectedEnzymes={[]} onEnzymesChange={vi.fn()} catalog={catalog} productEvidence={products} catalogRecords={[record]} analysis={analysis} authorityLoading={false} authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null} onDigestSelectionChange={vi.fn()} onSimulateDigest={simulate} />));
        expect(container.textContent).toContain('1 definite enzyme-site match');
        expect(container.textContent).toContain('0 possible matches');
        expect(container.textContent).toContain('1 predicted DSB');
        expect(container.textContent).toContain('Each enzyme is counted separately');
        expect(container.textContent).toContain('context-dependent enzymes may overlap at one sequence position');
        expect(container.textContent).toContain('0 nicks');
        expect(container.textContent).not.toContain('Supplier product evidence unavailable');
        expect(container.querySelector('[data-product-evidence-state="unavailable"]')).toBeNull();
        expect([...container.querySelectorAll('button')].some((button) => button.textContent === 'Commercial reported')).toBe(false);
        const add = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Digest');
        await act(async () => add?.click());
        const run = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('Run Digest'));
        await act(async () => run?.click());
        expect(simulate).toHaveBeenCalledWith(['EcoRI']);
        expect(container.textContent).toContain('5′ AATT overhang');
        expect(container.textContent).toContain('3′ AATT overhang');
        expect(container.textContent).toContain('blunt end');
        expect(container.textContent).not.toContain('top_strand_sequence');
    });

    it('maps every currently filtered enzyme in one action and clears the active map', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const mapChange = vi.fn();
        const render = async (selectedEnzymes: string[]) => act(async () => root?.render(<DigestPanel sequenceData={sequence} sequenceId={null} onHighlight={vi.fn()} selectedEnzymes={selectedEnzymes} onEnzymesChange={mapChange} catalog={catalog} productEvidence={products} catalogRecords={[record]} analysis={analysis} authorityLoading={false} authorityError={null} digestSimulation={null} digestLoading={false} digestError={null} onDigestSelectionChange={vi.fn()} onSimulateDigest={vi.fn()} />));
        await render([]);
        const search = container.querySelector('input[placeholder="Search enzyme or recognition site…"]') as HTMLInputElement;
        const setSearch = async (value: string) => act(async () => {
            Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(search, value);
            search.dispatchEvent(new Event('input', { bubbles: true }));
        });
        await setSearch('missing');
        expect([...container.querySelectorAll('button')].find((button) => button.textContent === 'Map filtered (0)')?.hasAttribute('disabled')).toBe(true);
        await setSearch('EcoRI');
        const mapFiltered = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Map filtered (1)');
        await act(async () => mapFiltered?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['EcoRI']);
        await render(['EcoRI']);
        const clearMapped = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Clear map (1)');
        await act(async () => clearMapped?.click());
        expect(mapChange).toHaveBeenLastCalledWith([]);
    });

    it('keeps externally supplied mappings when a quick-map group is turned off', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const mapChange = vi.fn();
        const stableCallbacks = { onHighlight: vi.fn(), onDigestSelectionChange: vi.fn(), onSimulateDigest: vi.fn() };
        const render = async (selectedEnzymes: string[]) => act(async () => root?.render(<DigestPanel {...stableCallbacks} sequenceData={sequence} sequenceId={null} selectedEnzymes={selectedEnzymes} onEnzymesChange={mapChange} catalog={catalog} productEvidence={products} catalogRecords={[record, bamRecord]} analysis={mixedAnalysis} authorityLoading={false} authorityError={null} digestSimulation={null} digestLoading={false} digestError={null} />));
        await render(['EcoRI']);
        const button = (label: string) => [...container!.querySelectorAll('button')].find((candidate) => candidate.textContent === label);
        await act(async () => button('Map all 1x')?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['EcoRI']);
        await render(['EcoRI']);
        await act(async () => button('Map all 1x')?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['EcoRI']);
    });

    it('keeps a row-level manual mapping when a quick-map group is turned off', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const mapChange = vi.fn();
        const stableCallbacks = { onHighlight: vi.fn(), onDigestSelectionChange: vi.fn(), onSimulateDigest: vi.fn() };
        const render = async (selectedEnzymes: string[]) => act(async () => root?.render(<DigestPanel {...stableCallbacks} sequenceData={sequence} sequenceId={null} selectedEnzymes={selectedEnzymes} onEnzymesChange={mapChange} catalog={catalog} productEvidence={products} catalogRecords={[record, bamRecord]} analysis={mixedAnalysis} authorityLoading={false} authorityError={null} digestSimulation={null} digestLoading={false} digestError={null} />));
        await render([]);
        const button = (label: string) => [...container!.querySelectorAll('button')].find((candidate) => candidate.textContent === label);
        await act(async () => button('Map all 1x')?.click());
        await render(['EcoRI']);
        await act(async () => button('2x')?.click());
        await act(async () => button('Map')?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['EcoRI', 'BamHI']);
        await render(['EcoRI', 'BamHI']);
        await act(async () => button('Map all 1x')?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['BamHI']);
    });

    it('keeps filtered manual mappings when a quick-map group is turned off', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const mapChange = vi.fn();
        const stableCallbacks = { onHighlight: vi.fn(), onDigestSelectionChange: vi.fn(), onSimulateDigest: vi.fn() };
        const render = async (selectedEnzymes: string[]) => act(async () => root?.render(<DigestPanel {...stableCallbacks} sequenceData={sequence} sequenceId={null} selectedEnzymes={selectedEnzymes} onEnzymesChange={mapChange} catalog={catalog} productEvidence={products} catalogRecords={[record, bamRecord]} analysis={mixedAnalysis} authorityLoading={false} authorityError={null} digestSimulation={null} digestLoading={false} digestError={null} />));
        await render([]);
        const button = (label: string) => [...container!.querySelectorAll('button')].find((candidate) => candidate.textContent === label);
        await act(async () => button('Map all 1x')?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['EcoRI']);
        await render(['EcoRI']);
        await act(async () => button('2x')?.click());
        await act(async () => button('Map filtered (1)')?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['EcoRI', 'BamHI']);
        await render(['EcoRI', 'BamHI']);
        await act(async () => button('Map all 1x')?.click());
        expect(mapChange).toHaveBeenLastCalledWith(['BamHI']);
    });
});
