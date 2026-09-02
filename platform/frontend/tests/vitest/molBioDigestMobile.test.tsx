import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';
import './setup';
import { DigestPanel } from '../../src/components/MolBioToolkit/panels/DigestPanel';
import type { RestrictionAnalysisBatch, RestrictionCatalogReceipt, RestrictionDigestSimulation, RestrictionProductReleaseReceipt, RestrictionRecord } from '../../src/lib/restrictionAnalysis';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';

const sequence: SequenceData = { name: 'Mobile fixture', sequence: 'TTGAATTCAA', circular: false, sequenceType: 'dna', features: [] };
const catalog = { catalog_id: 'catalog-v1', catalog_sha256: 'a'.repeat(64), counts: { total: 1 } } as unknown as RestrictionCatalogReceipt;
const products = { release_id: 'bms-restriction-products-permission-pending-v1', release_version: '1.0.0', content_sha256: 'c'.repeat(64), raw_sha256: 'd'.repeat(64), schema_raw_sha256: 'e'.repeat(64), created_at: null, created_at_policy: 'omitted_until_permissioned_evidence_release', source_policy: 'no_runtime_scraping_written_redistribution_permission_required', redistribution_permission_state: 'unavailable', permission_receipt: null, product_evidence_available: false, record_count: 0, active_claim_count: 0, core_catalog_digest_binding: 'independent_no_binding' } as RestrictionProductReleaseReceipt;
const record = { enzyme_id: 'EcoRI', canonical_name: 'EcoRI', aliases: [], recognition: { site_iupac: 'GAATTC', site_alternatives_iupac: ['GAATTC'], palindromic: true }, cleavage: { status: 'known_double_strand', events: [], nick: null }, enzyme_kind: 'double_strand_endonuclease', analysis_capability: 'digest_simulation', supplier_provenance: { reported_commercial: true, historical_supplier_codes: [] } } as unknown as RestrictionRecord;
const analysis = { authority_key: 'mobile-authority', chunks: [{ result_sha256: 'b'.repeat(64) }], analysis: { counts: { recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0 }, enzyme_summaries: [{ enzyme_id: 'EcoRI', canonical_name: 'EcoRI', analysis_capability: 'digest_simulation', cleavage_status: 'known_double_strand', recognition_site_count_definite: 1, recognition_site_count_possible: 0, double_strand_break_count: 1, nick_count: 0, limitations: [] }], occurrences: [{ occurrence_id: 'occ:1', enzyme_id: 'EcoRI', site_start: 2 }] } } as unknown as RestrictionAnalysisBatch;
const simulation = { fragments: [{ fragment_index: 0, reference_span_bp: 10, source_segments: [[0, 10]], left_end: { kind: 'five_prime_overhang', side: 'left', overhang_sequence_5to3: 'AATT' }, right_end: { kind: 'blunt', side: 'right', overhang_sequence_5to3: null } }] } as unknown as RestrictionDigestSimulation;

describe('DigestPanel mobile API workflow', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;
    afterEach(async () => { if (root) await act(async () => root?.unmount()); container?.remove(); });
    it('keeps one enzyme scroller, touch-safe sticky footer, and exact API output usable', async () => {
        container = document.createElement('div'); document.body.appendChild(container); root = createRoot(container);
        const simulate = vi.fn();
        await act(async () => root?.render(<DigestPanel mobile sequenceData={sequence} sequenceId={null} onHighlight={vi.fn()} selectedEnzymes={[]} onEnzymesChange={vi.fn()} catalog={catalog} productEvidence={products} catalogRecords={[record]} analysis={analysis} authorityLoading={false} authorityError={null} digestSimulation={simulation} digestLoading={false} digestError={null} onDigestSelectionChange={vi.fn()} onSimulateDigest={simulate} />));
        const panel = container.querySelector('[data-digest-layout="mobile"]');
        const scroller = container.querySelector('[data-digest-scroll-region="enzymes"]');
        const footer = container.querySelector('[data-digest-mobile-footer="true"]');
        expect(panel).toBeTruthy(); expect(scroller).toBeTruthy(); expect(footer).toBeTruthy(); expect(scroller?.contains(footer)).toBe(false);
        for (const target of container.querySelectorAll<HTMLElement>('[data-digest-mobile-touch-target="true"]')) expect(target.className).toContain('min-h-12');
        const add = [...container.querySelectorAll('button')].find((button) => button.textContent === 'Add');
        await act(async () => add?.click());
        const run = [...container.querySelectorAll('button')].find((button) => button.textContent?.includes('Run Digest (1 enzyme)'));
        await act(async () => run?.click());
        expect(simulate).toHaveBeenCalledWith(['EcoRI']);
        expect(container.querySelector('[data-digest-mobile-result="true"]')).toBeTruthy();
        expect(container.textContent).toContain('5′ AATT overhang');
        expect(container.textContent).toContain('blunt end');
        expect(container.textContent).toContain('Supplier product evidence unavailable');
        expect(container.querySelector('[data-product-evidence-state="unavailable"]')).toBeTruthy();
    });
});
