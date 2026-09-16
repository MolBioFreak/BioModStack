import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-plotly.js', () => ({
    default: ({ data, layout }: { data?: Array<{ type?: string }>; layout?: { title?: { text?: string }; xaxis?: { title?: { text?: string } } } }) => (
        <div data-testid="scientific-plot" data-trace-types={(data || []).map((trace) => trace.type || '').join(',')} data-xaxis-title={layout?.xaxis?.title?.text}>
            {layout?.title?.text || 'plot'}
        </div>
    ),
}));

import { api } from '../../src/lib/api';
import { OntFastqQcResultPanel } from '../../src/components/ngs/OntFastqQcResultPanel';
import { parseOntFastqQcResult, type OntFastqQcResult } from '../../src/lib/ontFastqQcResult';

const JOB_ID = '31f02bd5-830f-4558-aa78-3873c515de68';
const FIXTURE_PATH = resolve(process.cwd(), '../api/tests/fixtures/ont_fastq_qc_result_retry3_v1.json');

let container: HTMLDivElement;
let root: Root;

function resultFixture(): OntFastqQcResult {
    return parseOntFastqQcResult(JSON.parse(readFileSync(FIXTURE_PATH, 'utf8')) as unknown, JOB_ID);
}

beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
});

afterEach(async () => {
    await act(async () => root.unmount());
    document.body.replaceChildren();
});

describe('ONT FASTQ-QC decision report', () => {
    it('renders decision evidence, distinct depth semantics, downloads, and affected-base viewer action', async () => {
        const onOpenViewer = vi.fn();
        const Panel = OntFastqQcResultPanel as React.ComponentType<Record<string, unknown>>;

        await act(async () => {
            root.render(<Panel result={resultFixture()} loading={false} error={null} onOpenViewer={onOpenViewer} />);
        });

        expect(container.textContent).toContain('REVIEW REQUIRED');
        expect(container.textContent).toContain('Execution completed');
        for (const exactSummary of [
            'Total reads61,708integer read count',
            'Mapped reads61,573 / 61,708mapped count over total reads',
            'Total bases315,879,481 bpsequenced bases',
            'Reference coverage100.00%bases with ≥1 base-covering alignment record',
            'Decision minimum support depth49,126alignment observations from per-base support; deletion-spanning observations participate',
            'Coverage-envelope minimum24,840 at 3,516base-covering alignment records from samtools depth -aa; deletion bases are excluded',
            'Consensus identity99.9820%observed consensus versus bound reference',
        ]) {
            expect(container.textContent).toContain(exactSummary);
        }
        for (const label of ['Expected-reference screen', 'Coverage', 'Read support', 'Sequence identity', 'Topology']) {
            expect(container.textContent).toContain(label);
        }
        expect(container.textContent).toContain('expected_reference_mapping_only');
        expect(container.textContent).toContain('Does not establish organism identity or taxonomic contamination exclusion.');
        expect(container.textContent).toContain('Unmapped fraction');
        expect(container.textContent).toContain('Topology state');
        expect(container.textContent).toContain('Evidence sha256');
        expect(container.textContent).toContain('Purpose: Show read-length shape against the 5,570 bp reference');
        expect(container.textContent).toContain('Server-derived fixed_width_v1');
        expect(container.textContent).toContain('Historical producer expected plasmid size: 7,000 bp');
        expect(container.textContent).toContain('historical copy-number and multimer metrics do not control this decision');
        expect(container.textContent).not.toContain('dimer-sized reads');
        expect(container.textContent).toContain('Purpose: Identify low aligned-base coverage');
        expect(container.textContent).toContain('samtools_depth_aa_default_filters_excludes_deletions_v1');
        expect(container.textContent).toContain('Envelope minimum: 24,840 at position 3,516');
        expect(container.textContent).toContain('Decision support minimum: 49,126 (alignment observations; separate per-base-support basis)');
        const scientificPlots = container.querySelectorAll<HTMLElement>('[data-testid="scientific-plot"]');
        expect(scientificPlots).toHaveLength(2);
        expect(scientificPlots[1]?.dataset.traceTypes).toBe('scatter');
        expect(container.textContent).toContain('MIXED_ALLELES_DETECTED');
        expect(container.textContent).toContain('VARIANT_SUPPORT_AMBIGUOUS');
        expect(container.textContent).toContain('Affected interval');
        expect(container.textContent).toContain('DEL');
        expect(container.textContent).toContain('reference_bases: Deleted reference base 3516');
        expect(container.textContent).toContain('e122e032836df10c');

        const download = container.querySelector<HTMLAnchorElement>('a[href*="/ngs-artifacts/"]');
        expect(download).not.toBeNull();
        expect(download?.getAttribute('download')).not.toBeNull();
        expect(container.textContent).not.toContain('/secret/');

        const fixture = resultFixture();
        const groupedOrders = fixture.artifacts
            .slice()
            .sort((left, right) => left.display_order - right.display_order)
            .reduce<Array<{ role: string; orders: number[] }>>((groups, artifact) => {
                const group = groups.find((candidate) => candidate.role === artifact.scientific_role);
                if (group) group.orders.push(artifact.display_order);
                else groups.push({ role: artifact.scientific_role, orders: [artifact.display_order] });
                return groups;
            }, []);
        const governedArtifacts = Array.from(container.querySelectorAll<HTMLElement>('[data-artifact-display-order]'));
        expect(governedArtifacts.map((node) => Number(node.dataset.artifactDisplayOrder))).toEqual(
            groupedOrders.flatMap((group) => group.orders),
        );
        expect(Array.from(container.querySelectorAll<HTMLElement>('[data-artifact-role]')).map((node) => node.dataset.artifactRole)).toEqual(
            groupedOrders.map((group) => group.role),
        );
        expect(governedArtifacts.find((node) => Number(node.dataset.artifactDisplayOrder) === 3)?.textContent).toContain('Modified bases');

        const variantButton = Array.from(container.querySelectorAll('button'))
            .find((button) => button.textContent?.includes('View in IGV'));
        expect(variantButton).toBeDefined();
        await act(async () => variantButton?.dispatchEvent(new MouseEvent('click', { bubbles: true })));
        expect(onOpenViewer).toHaveBeenCalledWith('eGFP_plasmid:3416-3616');
    });

    it('does not interpret absent variant records as a completed negative analysis', async () => {
        const payload = resultFixture();
        payload.verification.verdict = 'FAIL';
        payload.verification.reason_codes = ['CONSENSUS_UNAVAILABLE'];
        payload.verification.summary.sequence_identity_fraction = null;
        payload.verification.variants = [];
        if (payload.pagination) payload.pagination.variants.total = 0;
        Object.assign(payload.verification.checks.sequence_identity, {
            status: 'not_evaluated', reason_codes: ['CONSENSUS_UNAVAILABLE'], metrics: {}, units: {},
        });
        await act(async () => {
            root.render(<OntFastqQcResultPanel result={payload} loading={false} error={null} />);
        });
        expect(container.textContent).toContain('No normalized variant records available; see sequence-identity status and reasons.');
        expect(container.textContent).toContain('CONSENSUS_UNAVAILABLE');
        expect(container.textContent).not.toContain('No normalized variants.');
    });

    it.each(['ready', 'unavailable'] as const)('renders sparse FAIL evidence and null identity with alignment %s', async (readiness) => {
        const payload = resultFixture();
        payload.verification.verdict = 'FAIL';
        payload.verification.reason_codes = ['CONSENSUS_UNAVAILABLE'];
        payload.verification.summary.sequence_identity_fraction = null;
        Object.assign(payload.verification.checks.sequence_identity, {
            status: 'fail', reason_codes: ['CONSENSUS_UNAVAILABLE'],
            metrics: { identity_fraction: null }, units: { identity_fraction: 'fraction' },
        });
        payload.verification.checks.coverage.metrics = {};
        payload.verification.checks.coverage.units = {};
        payload.verification.checks.topology.metrics = { state: 'unavailable', evidence_sha256: {} };
        payload.verification.checks.topology.units = { state: 'categorical' };
        const missing = payload.artifacts.find((artifact) => artifact.state !== 'present')!;
        missing.state = 'missing_required';
        missing.unavailable_reason = 'Required output unavailable';
        missing.content_disposition = 'attachment';
        missing.filename_extension = 'fasta';
        payload.stages[0].status = 'missing';
        payload.stages[0].output_count = 0;
        payload.authority.alignment_readiness = readiness;
        if (readiness === 'unavailable') {
            for (const session of payload.alignment_sessions) {
                Object.assign(session, { ready: false, reference_contig: null, unavailable_reason: 'Alignment unavailable' });
            }
        }
        // Exercise the parser before rendering, not a bypassed typed fixture.
        const result = parseOntFastqQcResult(payload, JOB_ID);
        const onOpenViewer = vi.fn();
        await act(async () => root.render(
            <OntFastqQcResultPanel result={result} loading={false} error={null} onOpenViewer={onOpenViewer} />,
        ));
        expect(container.textContent).toContain('Scientific verdict: FAIL');
        expect(container.textContent).toContain('CONSENSUS_UNAVAILABLE');
        expect(container.textContent).toContain('Consensus identity—observed consensus versus bound reference');
        expect(container.textContent).not.toContain('Consensus identity0.0000%');
        expect(container.textContent).toContain('Identity fraction— fraction');
        expect(container.textContent).toContain('Decision minimum support depth—');
        expect(container.textContent).toContain('Required output unavailable');
        expect(container.textContent).toContain('fastq_align: missing · 0 governed outputs');
        expect(container.querySelectorAll('a[href*="/ngs-artifacts/"]').length).toBeGreaterThan(0);
        const unavailableArtifact = container.querySelector(`[data-artifact-display-order="${missing.display_order}"]`);
        expect(unavailableArtifact?.querySelector('a')).toBeNull();
        expect(unavailableArtifact?.tagName).not.toBe('A');
        expect(container.querySelectorAll('[data-testid="scientific-plot"]')).toHaveLength(2);
        const viewerButtons = [...container.querySelectorAll('button')].filter((button) => button.textContent?.includes('IGV'));
        expect(viewerButtons.length).toBeGreaterThan(1);
        for (const button of viewerButtons) {
            expect(button.disabled).toBe(readiness === 'unavailable');
            await act(async () => button.click());
        }
        expect(onOpenViewer.mock.calls.length).toBe(readiness === 'ready' ? viewerButtons.length : 0);
    });

    it('labels coverage with the actual bound reference rather than a historical fixture', async () => {
        const result = resultFixture();
        result.verification.summary.reference_name = 'pGM12_pEb-HS2-fluc';
        await act(async () => root.render(
            <OntFastqQcResultPanel result={result} loading={false} error={null} />,
        ));
        const coverage = container.querySelectorAll<HTMLElement>('[data-testid="scientific-plot"]')[1];
        expect(coverage.textContent).toBe('Deletion-excluding aligned-base coverage by pGM12_pEb-HS2-fluc coordinate');
        expect(coverage.dataset.xaxisTitle).toBe('pGM12_pEb-HS2-fluc coordinate (1-based)');
        expect(coverage.textContent).not.toContain('eGFP');
    });

    it('offers bounded manual recovery for an alignment-access denial', async () => {
        const onRecoverAccess = vi.fn();
        await act(async () => {
            root.render(
                <OntFastqQcResultPanel
                    result={null}
                    loading={false}
                    error="alignment access denied"
                    onRecoverAccess={onRecoverAccess}
                    recoveryPending={false}
                />,
            );
        });

        const button = [...container.querySelectorAll('button')]
            .find((candidate) => candidate.textContent?.includes('Restore access'));
        expect(button).toBeTruthy();
        await act(async () => {
            button?.dispatchEvent(new MouseEvent('click', { bubbles: true }));
        });
        expect(onRecoverAccess).toHaveBeenCalledTimes(1);
    });
});


it('keeps scientific verdict and summary visible when optional observations and detail pages are unavailable', async () => {
    const result = resultFixture();
    result.execution_resources.evidence_status = 'unavailable';
    result.pagination = { artifacts: {offset: 0, count: 0, total: result.artifacts.length, next_offset: 0}, variants: {offset: 0, count: 0, total: result.verification.variants.length, next_offset: 0} };
    result.artifacts = [];
    result.verification.variants = [];
    await act(async () => root.render(<OntFastqQcResultPanel result={result} loading={false} error={null} />));
    expect(container.textContent).toContain('REVIEW REQUIRED');
    expect(container.textContent).toContain('unavailable');
    expect(container.textContent).toContain('Load artifacts');
    expect(container.textContent).toContain('Load variants');
    expect(container.textContent).toContain('61,708');
});


it('loads governed artifact detail through the real fetch/parser and rejects changed authority', async () => {
    const full = resultFixture();
    const summary = structuredClone(full);
    summary.pagination = { artifacts: {offset: 0, count: 0, total: full.artifacts.length, next_offset: 0}, variants: {offset: 0, count: 0, total: full.verification.variants.length, next_offset: 0} };
    summary.artifacts = [];
    summary.verification.variants = [];
    const get = vi.spyOn(api, 'get').mockResolvedValue({data: full});
    try {
        await act(async () => root.render(<OntFastqQcResultPanel result={summary} loading={false} error={null} />));
        const load = Array.from(container.querySelectorAll('button')).find(button => button.textContent === 'Load artifacts');
        expect(load).toBeDefined();
        await act(async () => { load!.click(); });
        expect(get).toHaveBeenCalledWith(`/api/jobs/${JOB_ID}/ngs-result`, {params: {collection: 'artifacts', page_size: 64, artifact_offset: 0}});
        expect(container.querySelector('a[href*="/ngs-artifacts/"]')).not.toBeNull();
        const changed = structuredClone(full);
        changed.authority.sequence_qc_manifest_sha256 = 'b'.repeat(64);
        get.mockResolvedValue({data: changed});
        const variants = Array.from(container.querySelectorAll('button')).find(button => button.textContent === 'Load variants');
        await act(async () => { variants!.click(); });
        expect(container.textContent).toContain('scientific result changed');
        expect(container.textContent).toContain('REVIEW REQUIRED');
    } finally { get.mockRestore(); }
});
