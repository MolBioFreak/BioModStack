// @vitest-environment jsdom

import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

Object.defineProperty(globalThis, 'IS_REACT_ACT_ENVIRONMENT', { configurable: true, value: true });

const { fetchSortableAlignmentReads, fetchAlignmentRead, requestOntRawSignalWaveform } = vi.hoisted(() => ({
    fetchSortableAlignmentReads: vi.fn(),
    fetchAlignmentRead: vi.fn(),
    requestOntRawSignalWaveform: vi.fn(),
}));

vi.mock('../../src/lib/ngsAlignmentSession', async () => {
    const actual = await vi.importActual<typeof import('../../src/lib/ngsAlignmentSession')>('../../src/lib/ngsAlignmentSession');
    return { ...actual, fetchSortableAlignmentReads, fetchAlignmentRead };
});

vi.mock('../../src/lib/api', () => ({
    fetchOntRawSignalWaveform: vi.fn(),
    requestOntRawSignalWaveform,
}));

import { RawReadInspector } from '../../src/components/ngs/RawReadInspector';

const locusSlice = {
    schema: 'bms.ngs.alignment-locus-slice.v1',
    job_id: 'job-a',
    session_id: 'session-a',
    slice_id: 'slice-a',
    state: 'ready',
    contig: 'ref',
    start_1based: 10,
    end_1based: 30,
    overlapping_read_count: 2,
    selected_read_count: 2,
    selected_record_count: 2,
    capped: false,
    policy: { id: 'bounded-full-source-locus-slice', version: 1, max_reads: 5_000, max_records: 20_000, max_bytes: 64, max_span_bp: 10_000, max_seconds: 30 },
    bam: {}, index: {}, manifest: {},
} as never;

const reads = [
    {
        read_id: 'read-a', length: 100, mean_quality: 13.2, contig: 'ref', start_1based: 12,
        alignment_end_1based: 111, strand: '+', mapq: 60, cigar: '100M', flags: 0, unmapped: false,
        aligned_reference_bases: 100, current_median_pa: 78.25,
    },
    {
        read_id: 'read-b', length: 90, mean_quality: 11.8, contig: 'ref', start_1based: 14,
        alignment_end_1based: 103, strand: '+', mapq: 45, cigar: '90M', flags: 0, unmapped: false,
        aligned_reference_bases: 90, current_median_pa: null,
    },
];

function page(sortBy = 'mean_quality') {
    return {
        schema: 'bms.ngs.sortable-read-page.v1', job_id: 'job-a', session_id: 'session-a', slice_id: 'slice-a',
        authority_sha256: 'a'.repeat(64), selected_read_count: 2, overlapping_read_count: 2,
        capped: false, filtered_read_count: 2, sort_by: sortBy, sort_direction: 'desc', null_order: 'last',
        tie_breaker: ['read_id', 'start_1based', 'flags'], signal_metrics_state: 'ready',
        signal_metrics_artifact_sha256: 'b'.repeat(64), raw_representation_id: 'rep-a',
        mapping_metrics_state: 'not_bound', metric_contract: 'bms.ont.literature-backed-read-metrics.v1',
        reads, next_cursor: null, limit: 50,
    } as never;
}

function button(container: HTMLElement, label: string): HTMLButtonElement {
    const found = Array.from(container.querySelectorAll('button')).find((candidate) => candidate.textContent === label);
    if (!(found instanceof HTMLButtonElement)) throw new Error(`Missing button: ${label}`);
    return found;
}

function deferred<T>() {
    let resolve!: (value: T) => void;
    let reject!: (reason: unknown) => void;
    const promise = new Promise<T>((resolvePromise, rejectPromise) => {
        resolve = resolvePromise;
        reject = rejectPromise;
    });
    return { promise, resolve, reject };
}

function changeInput(input: HTMLInputElement, value: string) {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')?.set?.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
}

describe('sortable read workbench', () => {
    let container: HTMLDivElement;
    let root: Root;

    beforeEach(() => {
        container = document.createElement('div');
        document.body.appendChild(container);
        root = createRoot(container);
        fetchSortableAlignmentReads.mockReset();
        fetchAlignmentRead.mockReset();
        requestOntRawSignalWaveform.mockReset();
        fetchSortableAlignmentReads.mockImplementation(async (_job, _session, _slice, options) => page(options.sortBy));
        fetchAlignmentRead.mockImplementation(async (_job, _session, readId) => ({ ...reads[0], read_id: readId, sequence: 'AC', quality: 'II' }));
    });

    afterEach(async () => {
        await act(async () => root.unmount());
        container.remove();
        vi.restoreAllMocks();
    });

    it('renders the admitted locus population and synchronizes exact IGV and signal actions', async () => {
        const navigate = vi.fn();
        const openSignal = vi.fn();
        await act(async () => {
            root.render(
                <RawReadInspector
                    jobId="job-a"
                    sessionId="session-a"
                    locusSlice={locusSlice}
                    currentLocus={{ contig: 'ref', start: 10, end: 30 }}
                    rawSignalBinding={{ runId: 'run-a', observedGeneration: 2, representationId: 'rep-a' }}
                    onNavigateIgv={navigate}
                    onOpenRawSignal={openSignal}
                />,
            );
        });
        await vi.waitFor(() => expect(container.textContent).toContain('read-a'));
        expect(container.textContent).toContain('2 admitted');
        expect(container.textContent).toContain('Signal metrics ready');
        expect(container.textContent).not.toMatch(/signal drift|robust tail|abrupt transition|ADC rail/i);

        const firstRow = Array.from(container.querySelectorAll('tbody tr'))[0] as HTMLTableRowElement;
        await act(async () => {
            (Array.from(firstRow.querySelectorAll('button')).find((candidate) => candidate.textContent === 'IGV') as HTMLButtonElement).click();
            (Array.from(firstRow.querySelectorAll('button')).find((candidate) => candidate.textContent === 'Signal') as HTMLButtonElement).click();
        });
        expect(navigate).toHaveBeenCalledWith(expect.objectContaining({ read_id: 'read-a' }));
        expect(openSignal).toHaveBeenCalledWith(expect.objectContaining({ read_id: 'read-a' }));

        const select = container.querySelector('select') as HTMLSelectElement;
        await act(async () => {
            select.value = 'current_median_pa';
            select.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await vi.waitFor(() => expect(fetchSortableAlignmentReads).toHaveBeenLastCalledWith(
            'job-a', 'session-a', 'slice-a', expect.objectContaining({ sortBy: 'current_median_pa', sortDirection: 'desc' }),
        ));
        expect(container.textContent).toContain('Median current (pA)');
        expect(container.textContent).toContain('78.250');
        expect(button(container, '↓ Desc').disabled).toBe(false);
    });

    it('does not refetch when a parent recreates an equivalent raw-signal binding object', async () => {
        const render = () => root.render(
            <RawReadInspector
                jobId="job-a"
                sessionId="session-a"
                locusSlice={locusSlice}
                currentLocus={{ contig: 'ref', start: 10, end: 30 }}
                rawSignalBinding={{ runId: 'run-a', observedGeneration: 2, representationId: 'rep-a' }}
                onNavigateIgv={() => undefined}
                onOpenRawSignal={() => undefined}
            />,
        );

        await act(async () => render());
        await vi.waitFor(() => expect(fetchSortableAlignmentReads).toHaveBeenCalledTimes(1));
        await act(async () => render());
        await new Promise((resolve) => window.setTimeout(resolve, 0));

        expect(fetchSortableAlignmentReads).toHaveBeenCalledTimes(1);
    });

    it('aborts stale root requests and only publishes the newest sort response', async () => {
        const first = deferred<ReturnType<typeof page>>();
        const second = deferred<ReturnType<typeof page>>();
        fetchSortableAlignmentReads
            .mockImplementationOnce((_job, _session, _slice, options) => {
                expect(options.signal.aborted).toBe(false);
                return first.promise;
            })
            .mockImplementationOnce(() => second.promise);
        await act(async () => root.render(<RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice} />));
        await vi.waitFor(() => expect(fetchSortableAlignmentReads).toHaveBeenCalledTimes(1));
        const firstSignal = fetchSortableAlignmentReads.mock.calls[0][3].signal as AbortSignal;

        const sort = container.querySelector('select') as HTMLSelectElement;
        await act(async () => {
            sort.value = 'length';
            sort.dispatchEvent(new Event('change', { bubbles: true }));
        });
        await vi.waitFor(() => expect(fetchSortableAlignmentReads).toHaveBeenCalledTimes(2));
        expect(firstSignal.aborted).toBe(true);

        await act(async () => second.resolve({ ...page('length'), reads: [reads[1]] } as never));
        await vi.waitFor(() => expect(container.textContent).toContain('read-b'));
        await act(async () => first.resolve({ ...page(), reads: [reads[0]] } as never));
        expect(container.textContent).not.toContain('read-a');
    });

    it('clears stale rows and authority state when a refresh fails', async () => {
        const failed = deferred<ReturnType<typeof page>>();
        fetchSortableAlignmentReads
            .mockResolvedValueOnce(page())
            .mockImplementationOnce(() => failed.promise);
        await act(async () => root.render(<RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice} />));
        await vi.waitFor(() => expect(container.textContent).toContain('read-a'));

        await act(async () => button(container, 'Refresh').click());
        expect(container.textContent).not.toContain('read-a');
        expect(container.textContent).not.toContain('Signal metrics ready');
        await act(async () => failed.reject(new Error('refresh failed')));
        await vi.waitFor(() => expect(container.textContent).toContain('refresh failed'));
        expect(container.textContent).not.toContain('read-a');
    });

    it('aborts pending detail ownership and blocks stale detail publication on root refresh', async () => {
        const pendingDetail = deferred<Record<string, unknown>>();
        const pendingRefresh = deferred<ReturnType<typeof page>>();
        fetchAlignmentRead.mockImplementationOnce((_job, _session, readId, options) => {
            expect(options.signal.aborted).toBe(false);
            return pendingDetail.promise.then((detail) => ({ ...detail, read_id: readId }));
        });
        fetchSortableAlignmentReads
            .mockResolvedValueOnce(page())
            .mockImplementationOnce(() => pendingRefresh.promise);
        await act(async () => root.render(<RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice} />));
        await vi.waitFor(() => expect(container.textContent).toContain('read-a'));

        await act(async () => (container.querySelector('[aria-label="Inspect details for read read-a"]') as HTMLButtonElement).click());
        await vi.waitFor(() => expect(fetchAlignmentRead).toHaveBeenCalledTimes(1));
        const detailSignal = fetchAlignmentRead.mock.calls[0][3].signal as AbortSignal;

        await act(async () => button(container, 'Refresh').click());
        expect(detailSignal.aborted).toBe(true);
        expect(container.textContent).not.toContain('Basecalled sequence');

        await act(async () => pendingDetail.resolve({ ...reads[0], sequence: 'STALE', quality: 'II' }));
        expect(container.textContent).not.toContain('Basecalled sequence');
        expect(container.textContent).not.toContain('STALE');
    });

    it('invalid metric bounds abort and supersede prior list ownership', async () => {
        const pending = deferred<ReturnType<typeof page>>();
        fetchSortableAlignmentReads.mockImplementationOnce(() => pending.promise);
        await act(async () => root.render(<RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice} />));
        await vi.waitFor(() => expect(fetchSortableAlignmentReads).toHaveBeenCalledTimes(1));
        const signal = fetchSortableAlignmentReads.mock.calls[0][3].signal as AbortSignal;
        const minimum = container.querySelector('[aria-label="Selected metric minimum"]') as HTMLInputElement;
        await act(async () => {
            changeInput(minimum, 'not-a-number');
        });
        await vi.waitFor(() => expect(container.textContent).toContain('Metric bounds must be finite numbers'));
        expect(signal.aborted).toBe(true);
        await act(async () => pending.resolve(page()));
        expect(container.textContent).not.toContain('read-a');
    });

    it('clears and disables metric bounds for Read ID and exposes accessible controls', async () => {
        await act(async () => root.render(<RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice} />));
        await vi.waitFor(() => expect(container.textContent).toContain('read-a'));
        const minimum = container.querySelector('[aria-label="Selected metric minimum"]') as HTMLInputElement;
        const maximum = container.querySelector('[aria-label="Selected metric maximum"]') as HTMLInputElement;
        expect(container.querySelector('[aria-label="Filter reads by ID"]')).toBeTruthy();
        expect(container.querySelector('[aria-label="Sort reads by"]')).toBeTruthy();
        expect(container.querySelector('[aria-label="Toggle sort direction"]')).toBeTruthy();
        expect(container.querySelector('[aria-label="Refresh sortable reads"]')).toBeTruthy();
        await act(async () => {
            changeInput(minimum, '5');
            const sort = container.querySelector('[aria-label="Sort reads by"]') as HTMLSelectElement;
            sort.value = 'read_id';
            sort.dispatchEvent(new Event('change', { bubbles: true }));
        });
        expect(minimum.value).toBe('');
        expect(maximum.value).toBe('');
        expect(minimum.disabled).toBe(true);
        expect(maximum.disabled).toBe(true);
        expect(container.querySelector('[aria-label="Navigate read read-a in IGV"]')).toBeTruthy();
        expect(container.querySelector('[aria-label="Open signal for read read-a"]')).toBeTruthy();
        expect(container.querySelector('[aria-label="Inspect details for read read-a"]')).toBeTruthy();
    });

    it('disables row IGV controls unless the exact alignment locus is complete', async () => {
        const incompleteReads = [
            { ...reads[0], read_id: 'missing-contig', contig: null },
            { ...reads[0], read_id: 'missing-start', start_1based: null },
            { ...reads[0], read_id: 'missing-end', alignment_end_1based: null },
            { ...reads[0], read_id: 'complete-locus' },
        ];
        fetchSortableAlignmentReads.mockResolvedValueOnce({ ...page(), reads: incompleteReads } as never);
        await act(async () => root.render(
            <RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice} onNavigateIgv={() => undefined} />,
        ));
        await vi.waitFor(() => expect(container.textContent).toContain('complete-locus'));

        expect((container.querySelector('[aria-label="Navigate read missing-contig in IGV"]') as HTMLButtonElement).disabled).toBe(true);
        expect((container.querySelector('[aria-label="Navigate read missing-start in IGV"]') as HTMLButtonElement).disabled).toBe(true);
        expect((container.querySelector('[aria-label="Navigate read missing-end in IGV"]') as HTMLButtonElement).disabled).toBe(true);
        expect((container.querySelector('[aria-label="Navigate read complete-locus in IGV"]') as HTMLButtonElement).disabled).toBe(false);
    });

    it('disables selected-read IGV control when alignment end is absent', async () => {
        const missingEnd = { ...reads[0], read_id: 'missing-end', alignment_end_1based: null };
        fetchSortableAlignmentReads.mockResolvedValueOnce({ ...page(), reads: [missingEnd] } as never);
        fetchAlignmentRead.mockResolvedValueOnce({ ...missingEnd, sequence: 'AC', quality: 'II' });
        await act(async () => root.render(
            <RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice} onNavigateIgv={() => undefined} />,
        ));
        await vi.waitFor(() => expect(container.textContent).toContain('missing-end'));

        await act(async () => (container.querySelector('[aria-label="Inspect details for read missing-end"]') as HTMLButtonElement).click());
        await vi.waitFor(() => expect(container.textContent).toContain('Basecalled sequence'));
        expect((container.querySelector('[aria-label="Navigate selected read missing-end in IGV"]') as HTMLButtonElement).disabled).toBe(true);
    });

    it('drops detail and stale waveform publication when primitive binding identity changes', async () => {
        const oldWaveform = deferred<Record<string, unknown>>();
        requestOntRawSignalWaveform.mockImplementationOnce(() => oldWaveform.promise);
        const render = (representationId: string) => root.render(
            <RawReadInspector jobId="job-a" sessionId="session-a" locusSlice={locusSlice}
                rawSignalBinding={{ runId: 'run-a', observedGeneration: 2, representationId }} />,
        );
        await act(async () => render('rep-a'));
        await vi.waitFor(() => expect(container.textContent).toContain('read-a'));
        await act(async () => (container.querySelector('[aria-label="Inspect details for read read-a"]') as HTMLButtonElement).click());
        await vi.waitFor(() => expect(requestOntRawSignalWaveform).toHaveBeenCalledTimes(1));

        await act(async () => render('rep-b'));
        expect(container.textContent).not.toContain('Basecalled sequence');
        await act(async () => oldWaveform.resolve({
            state: 'ready', lookup_id: 'old', run_id: 'run-a', observed_generation: 2,
            representation_id: 'rep-a', read_id: 'read-a', samples: [1, 2], sample_count: 2,
        }));
        expect(container.textContent).not.toContain('Exact raw signal');
    });
});
