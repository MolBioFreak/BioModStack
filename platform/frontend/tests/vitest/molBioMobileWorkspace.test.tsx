import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, describe, expect, it, vi } from 'vitest';

import {
    MobileMolBioReadPanel,
    MobileMolBioWorkspace,
    parseMobileMolBioWorkups,
} from '../../src/components/MolBioToolkit/MobileMolBioWorkspace';
import type { SequenceData } from '../../src/components/MolBioToolkit/types';

const SEQUENCE: SequenceData = {
    name: 'PL931',
    sequence: 'AAAACCGGTTTT',
    circular: true,
    sequenceType: 'dna',
    features: [{
        id: 'feature-1',
        name: 'AgeI cassette',
        type: 'misc_feature',
        start: 4,
        end: 10,
        strand: 1,
        color: '#22d3ee',
    }],
    primers: [{
        id: 'primer-1',
        name: 'PL931-F',
        sequence: 'AAAACCGG',
        start: 1,
        end: 8,
        strand: 1,
        tm: 61.2,
    }],
};

function DigestStateProbe() {
    const [value, setValue] = React.useState('empty');
    return (
        <div data-digest-state-probe="true">
            <span>{value}</span>
            <button type="button" onClick={() => setValue('completed')}>Complete digest</button>
        </div>
    );
}

describe('MobileMolBioWorkspace', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        container = undefined;
    });

    async function renderWorkspace(overrides: Partial<React.ComponentProps<typeof MobileMolBioWorkspace>> = {}) {
        if (!root) {
            container = document.createElement('div');
            document.body.appendChild(container);
            root = createRoot(container);
        }
        const props: React.ComponentProps<typeof MobileMolBioWorkspace> = {
            constructName: 'PL931',
            digestIdentity: 'workspace-1:seq-1',
            digestAvailable: true,
            qcAvailable: true,
            hasSequence: true,
            constructPickerOpen: false,
            surface: 'map',
            onBack: vi.fn(),
            onOpenConstructs: vi.fn(),
            onSurfaceChange: vi.fn(),
            constructs: <div data-test-surface="constructs">construct picker</div>,
            map: <div data-test-surface="map">plasmid map</div>,
            sequence: <div data-test-surface="sequence">sequence viewer</div>,
            details: <div data-test-surface="details">details</div>,
            digest: <div data-test-surface="digest">digest</div>,
            qc: <div data-test-surface="qc">qc</div>,
            ...overrides,
        };
        await act(async () => root?.render(<MobileMolBioWorkspace {...props} />));
        return container as HTMLDivElement;
    }

    it('mounts exactly one full-screen scientific surface', async () => {
        const rendered = await renderWorkspace({ surface: 'digest' });
        const layout = rendered.querySelector<HTMLElement>('[data-molbio-mobile-layout="true"]');
        expect(layout).toBeTruthy();
        expect(layout?.className).toContain('h-[100dvh]');
        expect(layout?.className).toContain('fixed');
        expect(rendered.querySelector('[data-molbio-mobile-surface="digest"]')).toBeTruthy();
        const surface = rendered.querySelector<HTMLElement>('[data-molbio-mobile-surface="digest"]');
        expect(surface?.style.paddingBottom).toContain('safe-area-inset-bottom');
        expect(surface?.style.paddingBottom).toContain('1rem');
        expect(rendered.querySelectorAll('[data-test-surface]').length).toBe(1);
        expect(rendered.textContent).toContain('digest');
    });

    it('fails closed to Map when immutable revision authority disables Digest', async () => {
        const rendered = await renderWorkspace({
            surface: 'digest',
            digestAvailable: false,
            digest: <div data-test-surface="digest">unsafe digest</div>,
        });
        expect(rendered.querySelector('[data-molbio-mobile-surface="map"]')).toBeTruthy();
        expect(rendered.querySelector('[data-molbio-persistent-digest="true"]')).toBeNull();
        expect(rendered.textContent).not.toContain('unsafe digest');
        const digestButton = [...rendered.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent?.trim() === 'Digest');
        expect(digestButton?.disabled).toBe(true);
        expect(digestButton?.title).toContain('current construct');
    });

    it('fails closed to Map when immutable revision authority disables QC', async () => {
        const rendered = await renderWorkspace({
            surface: 'qc',
            qcAvailable: false,
            qc: <div data-test-surface="qc">unsafe QC evidence</div>,
        });
        expect(rendered.querySelector('[data-molbio-mobile-surface="map"]')).toBeTruthy();
        expect(rendered.textContent).not.toContain('unsafe QC evidence');
        const qcButton = [...rendered.querySelectorAll<HTMLButtonElement>('button')]
            .find((button) => button.textContent?.trim() === 'QC');
        expect(qcButton?.disabled).toBe(true);
        expect(qcButton?.title).toContain('current construct');
    });

    it('shows the construct picker instead of a hidden underlying surface', async () => {
        const rendered = await renderWorkspace({ constructPickerOpen: true, surface: 'digest' });
        expect(rendered.querySelector('[data-molbio-mobile-surface="constructs"]')).toBeTruthy();
        const visibleSurfaces = [...rendered.querySelectorAll('[data-test-surface]')]
            .filter((element) => !element.closest('[hidden]'));
        expect(visibleSurfaces.length).toBe(1);
        expect(rendered.textContent).toContain('construct picker');
        const digestCache = rendered.querySelector<HTMLElement>('[data-molbio-persistent-digest="true"]');
        expect(digestCache).toBeTruthy();
        expect(digestCache?.hidden).toBe(true);
    });

    it('shows sequence-load errors without replacing the construct picker', async () => {
        const rendered = await renderWorkspace({
            constructPickerOpen: true,
            error: 'Could not load the requested construct.',
        });
        expect(rendered.querySelector('[data-molbio-mobile-surface="constructs"]')).toBeTruthy();
        const alert = rendered.querySelector<HTMLElement>('[data-molbio-mobile-error="true"]');
        expect(alert).toBeTruthy();
        expect(alert?.getAttribute('role')).toBe('alert');
        expect(alert?.textContent).toContain('Could not load the requested construct.');
    });

    it('preserves completed Digest state through Map and Constructs navigation', async () => {
        const rendered = await renderWorkspace({ surface: 'digest', digest: <DigestStateProbe /> });
        const completeButton = [...rendered.querySelectorAll('button')]
            .find((button) => button.textContent === 'Complete digest');
        await act(async () => completeButton?.click());
        expect(rendered.textContent).toContain('completed');

        await renderWorkspace({ surface: 'map', digest: <DigestStateProbe /> });
        let cache = rendered.querySelector<HTMLElement>('[data-molbio-persistent-digest="true"]');
        expect(cache?.hidden).toBe(true);
        expect(cache?.textContent).toContain('completed');

        await renderWorkspace({ surface: 'digest', digest: <DigestStateProbe /> });
        cache = rendered.querySelector<HTMLElement>('[data-molbio-persistent-digest="true"]');
        expect(cache?.hidden).toBe(false);
        expect(cache?.textContent).toContain('completed');

        await renderWorkspace({ constructPickerOpen: true, surface: 'digest', digest: <DigestStateProbe /> });
        cache = rendered.querySelector<HTMLElement>('[data-molbio-persistent-digest="true"]');
        expect(cache?.hidden).toBe(true);
        expect(cache?.textContent).toContain('completed');

        await renderWorkspace({ constructPickerOpen: false, surface: 'digest', digest: <DigestStateProbe /> });
        expect(rendered.querySelector('[data-molbio-persistent-digest="true"]')?.textContent).toContain('completed');
    });

    it('resets cached Digest state when scientific identity changes', async () => {
        const rendered = await renderWorkspace({
            surface: 'digest',
            digestIdentity: 'workspace-1:seq-1',
            digest: <DigestStateProbe />,
        });
        const completeButton = [...rendered.querySelectorAll('button')]
            .find((button) => button.textContent === 'Complete digest');
        await act(async () => completeButton?.click());
        expect(rendered.textContent).toContain('completed');

        await renderWorkspace({
            surface: 'digest',
            digestIdentity: 'workspace-2:seq-2',
            digest: <DigestStateProbe />,
        });
        expect(rendered.querySelector('[data-digest-state-probe="true"]')?.textContent).toContain('empty');
    });

    it('owns and releases the shell-control suppression class', async () => {
        await renderWorkspace({ surface: 'digest' });
        expect(document.documentElement.classList.contains('bms-molbio-mobile-active')).toBe(true);

        await act(async () => root?.unmount());
        root = undefined;
        expect(document.documentElement.classList.contains('bms-molbio-mobile-active')).toBe(false);
    });
});

describe('MobileMolBioReadPanel', () => {
    let root: Root | undefined;
    let container: HTMLDivElement | undefined;

    afterEach(async () => {
        if (root) await act(async () => root?.unmount());
        container?.remove();
        root = undefined;
        container = undefined;
    });

    async function renderPanel(
        mode: 'details' | 'qc',
        workupsStatus: 'idle' | 'loading' | 'ready' | 'unavailable' = 'ready',
    ) {
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        await act(async () => {
            root?.render(
                <MobileMolBioReadPanel
                    mode={mode}
                    sequenceData={SEQUENCE}
                    workupsStatus={workupsStatus}
                    workups={[{
                        job_id: 'job-pass',
                        scientific_status: 'PASS',
                        revision_relation: 'current',
                        manifest_available: true,
                    }]}
                />,
            );
        });
        return container;
    }

    it('shows construct, feature, and primer details without mutation controls', async () => {
        const rendered = await renderPanel('details');
        expect(rendered.textContent).toContain('PL931');
        expect(rendered.textContent).toContain('AgeI cassette');
        expect(rendered.textContent).toContain('PL931-F');
        expect(rendered.textContent).toContain('Read only');
        expect(rendered.querySelector('input, textarea, select, button')).toBeNull();
    });

    it('shows current sequencing QC status as inspectable data', async () => {
        const rendered = await renderPanel('qc');
        expect(rendered.textContent).toContain('PASS');
        expect(rendered.textContent).toContain('Current revision');
        expect(rendered.textContent).toContain('Manifest available');
        expect(rendered.textContent).toContain('job-pass');
    });

    it('accepts only fully typed QC workup payloads', () => {
        const valid = {
            workups: [{
                job_id: 'job-pass',
                scientific_status: 'PASS',
                revision_relation: 'current',
                manifest_available: true,
            }],
        };
        expect(parseMobileMolBioWorkups(valid)).toEqual(valid.workups);
        expect(parseMobileMolBioWorkups({ workups: [{ scientific_status: 'PASS' }] })).toBeNull();
        expect(parseMobileMolBioWorkups({
            workups: [{ ...valid.workups[0], revision_relation: 'unknown' }],
        })).toBeNull();
        expect(parseMobileMolBioWorkups({
            workups: [{ ...valid.workups[0], manifest_available: 'yes' }],
        })).toBeNull();
    });

    it('reports unavailable QC evidence without claiming that no workups exist', async () => {
        const rendered = await renderPanel('qc', 'unavailable');
        const alert = rendered.querySelector('[role="alert"]');
        expect(alert).toBeTruthy();
        expect(alert?.textContent).toContain('could not be loaded');
        expect(rendered.textContent).not.toContain('No sequencing QC workups are linked');
        expect(rendered.textContent).not.toContain('job-pass');
    });
});
