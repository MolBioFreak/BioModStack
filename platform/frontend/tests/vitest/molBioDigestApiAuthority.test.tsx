import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import './setup';
import { DigestPanel } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import type { RestrictionAnalysisResponse, RestrictionCatalogReceipt, RestrictionDigestSimulation, RestrictionProductReleaseReceipt, RestrictionRecord } from '../../src/lib/restrictionAnalysis';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';

const sequence: SequenceData = { name: 'API fixture', sequence: 'TTGAATTCAA', circular: false, sequenceType: 'dna', features: [] };
const catalog = { catalog_id: 'catalog-v1', catalog_sha256: 'a'.repeat(64), source_release: 'REBASE 404', counts: { total: 1, geometry_ready: 1, commercial_geometry_ready: 1, unknown_geometry: 0, nicking: 0, two_event_double_strand: 0 } } as RestrictionCatalogReceipt;
const products = { release_id: 'bms-restriction-products-permission-pending-v1', release_version: '1.0.0', content_sha256: 'c'.repeat(64), raw_sha256: 'd'.repeat(64), schema_raw_sha256: 'e'.repeat(64), created_at: null, created_at_policy: 'omitted_until_permissioned_evidence_release', source_policy: 'no_runtime_scraping_written_redistribution_permission_required', redistribution_permission_state: 'unavailable', permission_receipt: null, product_evidence_available: false, record_count: 0, active_claim_count: 0, core_catalog_digest_binding: 'independent_no_binding' } as RestrictionProductReleaseReceipt;
const record = { enzyme_id: 'EcoRI', canonical_name: 'EcoRI', aliases: [], recognition: { site_iupac: 'GAATTC', site_alternatives_iupac: ['GAATTC'], palindromic: true }, cleavage: { status: 'known_double_strand', events: [{ top_offset: 1, bottom_offset: 5, overhang_kind: 'five_prime' }], nick: null }, enzyme_kind: 'double_strand_endonuclease', analysis_capability: 'digest_simulation', supplier_provenance: { reported_commercial: true, historical_supplier_codes: [] } } as unknown as RestrictionRecord;
const occurrence = { occurrence_id: 'occ:1', occurrence_ordinal: 0, enzyme_id: 'EcoRI', canonical_name: 'EcoRI', orientation: 'forward', certainty: 'definite', site_start: 2, site_end_unwrapped: 8, site_segments: [[2, 8]], wraps_origin: false, double_strand_events: [{ enzyme_id: 'EcoRI', occurrence_id: 'occ:1', event_ordinal: 0, orientation: 'forward', status: 'complete', top_boundary: 3, bottom_boundary: 7, top_boundary_unwrapped: 3, bottom_boundary_unwrapped: 7, contributor_group_id: 'cut:1' }], nicks: [], limitations: [] };
const analysis = { result_sha256: 'b'.repeat(64), analysis: { counts: { recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0 }, enzyme_summaries: [{ enzyme_id: 'EcoRI', canonical_name: 'EcoRI', analysis_capability: 'digest_simulation', cleavage_status: 'known_double_strand', recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0, limitations: [] }], occurrences: [occurrence] } } as unknown as RestrictionAnalysisResponse;
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
        expect(authorityEffect).not.toMatch(/fetchRestrictionAnalysis\([\s\S]*?enzymeIds/);

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

    it('keeps recognition, DSB and nick counts separate and submits only stable IDs', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const simulate = vi.fn();
        await act(async () => root?.render(<DigestPanel sequenceData={sequence} sequenceId={null} onHighlight={vi.fn()} selectedEnzymes={[]} onEnzymesChange={vi.fn()} catalog={catalog} productEvidence={products} catalogRecords={[record]} analysis={analysis} authorityLoading={false} authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null} onDigestSelectionChange={vi.fn()} onSimulateDigest={simulate} />));
        expect(container.textContent).toContain('1 recognition sites');
        expect(container.textContent).toContain('1 DSBs');
        expect(container.textContent).toContain('0 nicks');
        expect(container.textContent).toContain('Supplier product evidence unavailable');
        expect(container.querySelector('[data-product-evidence-state="unavailable"]')).toBeTruthy();
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
});
