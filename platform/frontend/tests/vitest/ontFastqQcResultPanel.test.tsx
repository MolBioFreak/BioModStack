import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-plotly.js', () => ({
    default: ({ data, layout }: { data?: Array<{ type?: string }>; layout?: { title?: { text?: string } } }) => (
        <div data-testid="scientific-plot" data-trace-types={(data || []).map((trace) => trace.type || '').join(',')}>
            {layout?.title?.text || 'plot'}
        </div>
    ),
}));

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
