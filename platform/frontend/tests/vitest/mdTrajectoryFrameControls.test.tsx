import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

vi.mock('react-plotly.js', () => ({ default: () => <div data-testid="plot" /> }));
vi.mock('../../src/components/MolstarViewer', () => ({
    default: ({ molecularDynamics }: { molecularDynamics?: { playback?: { selectedFrame?: unknown } } }) => (
        <div data-testid="molstar-scene">{JSON.stringify(molecularDynamics?.playback?.selectedFrame ?? null)}</div>
    ),
}));

import MDResultsPane from '../../src/components/MDResultsPane';

(globalThis as typeof globalThis & { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const shaA = 'a'.repeat(64);
const shaB = 'b'.repeat(64);
const frames = [
    { display_frame: 0, source_frame: 0, time_ps: 0, step: 0 },
    { display_frame: 1, source_frame: 10, time_ps: 200, step: 100_000 },
    { display_frame: 2, source_frame: 20, time_ps: 400, step: 200_000 },
    { display_frame: 3, source_frame: 30, time_ps: 600, step: 300_000 },
    { display_frame: 4, source_frame: 40, time_ps: 800, step: 400_000 },
    { display_frame: 5, source_frame: 50, time_ps: 1000, step: 500_000 },
];

const seedPlayback = (client: QueryClient, jobId: string) => {
    client.setQueryData(['md-summary', jobId], response({
        schema: 'bms.md.summary.v1', job_id: jobId, status: 'completed', result_state: 'completed',
        source: 'validated_job_owned_manifests', bounded: true, aggregate_manifest_sha256: shaA,
        replica_count: 1, artifact_count: 3,
        replicas: [{ replica: 0, status: 'completed', engine: { name: 'gromacs' }, performance: {} }],
        analysis_status: 'absent',
        trajectory_playback: { supported: true, reason: 'qualified', replicas: [{ replica: 0, frame_count: 6 }] },
    }));
    client.setQueryData(['md-artifacts', jobId], response({
        schema: 'bms.md.artifact-inventory.v1', job_id: jobId, source: 'validated_job_owned_manifests', bounded: true,
        artifacts: [
            { id: 'top', replica: 0, name: 'topology.gro', bytes: 1, sha256: shaA, semantic_role: 'analysis_topology', atom_order_identity: 'order-0', format: 'gro', content_url: '/top' },
            { id: 'traj', replica: 0, name: 'production.xtc', bytes: 1, sha256: shaB, semantic_role: 'analysis_trajectory', atom_order_identity: 'order-0', format: 'xtc', content_url: '/traj' },
            { id: 'map', replica: 0, name: 'trajectory-frame-map.json', bytes: 1, sha256: shaA, semantic_role: 'trajectory_frame_map', atom_order_identity: 'order-0', format: 'json', content_url: '/map' },
        ],
    }));
    client.setQueryData(['md-analysis', jobId], response({
        schema: 'bms.md.analysis-report-set.v1', job_id: jobId, status: 'absent', bounded: true,
        replica_states: [{ replica: 0, status: 'absent' }], reports: [],
        ensemble: { statistical_unit: 'replica', frame_pooling: false, completed_replicas: 0, mean_of_replica_mean_rmsd_angstrom: null, sample_stdev_of_replica_mean_rmsd_angstrom: null },
        evidence: { status: 'insufficient_evidence', reason: 'fixture', frames_are_independent_replicates: false },
        retry: { eligible: false, active: false, reason: 'not available' },
    }));
    client.setQueryData(['md-trajectory-frame-map', jobId, 'map'], {
        schema: 'bms.md.trajectory-frame-map.v1', job_id: jobId, replica: 0, frame_count: 6, frames,
    });
};

afterEach(() => document.body.replaceChildren());

describe('governed MD trajectory frame controls', () => {
    it('plays, pauses, and loops the governed display-frame sequence', async () => {
        vi.useFakeTimers();
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        seedPlayback(client, 'job-md-playback');
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(<QueryClientProvider client={client}><MDResultsPane jobId="job-md-playback" /></QueryClientProvider>);
            await Promise.resolve();
        });

        const receipt = () => container.querySelector('[data-bms-md-frame-receipt]')?.textContent ?? '';
        const play = container.querySelector<HTMLButtonElement>('[data-bms-md-playback="play"]');
        expect(play).toBeTruthy();
        await act(async () => play!.click());
        expect(container.querySelector('[data-bms-md-playback="pause"]')).toBeTruthy();

        await act(async () => { vi.advanceTimersByTime(500); });
        expect(receipt()).toContain('Display frame 1');
        await act(async () => { vi.advanceTimersByTime(2_000); });
        expect(receipt()).toContain('Display frame 5');
        await act(async () => { vi.advanceTimersByTime(500); });
        expect(receipt()).toContain('Display frame 0');

        const pause = container.querySelector<HTMLButtonElement>('[data-bms-md-playback="pause"]');
        await act(async () => pause!.click());
        await act(async () => { vi.advanceTimersByTime(1_000); });
        expect(receipt()).toContain('Display frame 0');
        expect(container.textContent).toContain('Loop on');

        await act(async () => root.unmount());
        vi.useRealTimers();
        client.clear();
    });

    it('addresses bounded display frames while showing authoritative source/time/step provenance', async () => {
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        seedPlayback(client, 'job-md');
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);

        await act(async () => {
            root.render(<QueryClientProvider client={client}><MDResultsPane jobId="job-md" /></QueryClientProvider>);
            await Promise.resolve();
        });

        const receipt = () => container.querySelector('[data-bms-md-frame-receipt]')?.textContent ?? '';
        const scene = () => container.querySelector('[data-testid="molstar-scene"]')?.textContent ?? '';
        expect(receipt()).toContain('Display frame 0');
        expect(receipt()).toContain('source 0');
        expect(receipt()).toContain('0 ps');
        expect(receipt()).toContain('step 0');
        expect(scene()).toContain('"displayFrame":0');

        for (const displayFrame of [3, 5]) {
            const button = container.querySelector<HTMLButtonElement>(`[data-bms-md-display-frame="${displayFrame}"]`);
            expect(button).toBeTruthy();
            await act(async () => button!.click());
        }

        expect(receipt()).toContain('Display frame 5');
        expect(receipt()).toContain('source 50');
        expect(receipt()).toContain('1000 ps');
        expect(receipt()).toContain('step 500000');
        expect(scene()).toContain('"displayFrame":5');
        expect(scene()).toContain('"sourceFrame":50');

        await act(async () => root.unmount());
        client.clear();
    });
});
