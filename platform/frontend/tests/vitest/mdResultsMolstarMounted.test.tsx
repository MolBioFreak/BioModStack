import React, { StrictMode, act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, describe, expect, it, vi } from 'vitest';

const ownerStats = vi.hoisted(() => ({ initialized: 0, disposed: 0, live: 0, maximumLive: 0, representationPresets: [] as string[] }));
vi.mock('../../src/structureViewer/runtime/createDirectMolstarEngineOwner', () => ({
    createDirectMolstarEngineOwner: () => {
        let disposed = false;
        return {
            initialize: async () => {
                ownerStats.initialized += 1;
                ownerStats.live += 1;
                ownerStats.maximumLive = Math.max(ownerStats.maximumLive, ownerStats.live);
                return {
                    status: 'ok', generation: ownerStats.initialized,
                    plugin: {
                        commands: { dispatch: async () => undefined },
                        canvas3d: { setProps: () => undefined, camera: { setState: () => undefined }, requestCameraReset: () => undefined },
                        managers: {
                            interactivity: { setProps: () => undefined },
                            structure: { hierarchy: { current: { structures: [] } } },
                        },
                        behaviors: { interaction: { click: { subscribe: () => ({ unsubscribe: () => undefined }) } } },
                        builders: {
                            data: { download: async () => ({}) },
                            structure: {
                                parseTrajectory: async () => ({}),
                                hierarchy: {
                                    applyPreset: async (_trajectory: unknown, _preset: string, params: { representationPreset?: string }) => {
                                        if (params.representationPreset) ownerStats.representationPresets.push(params.representationPreset);
                                    },
                                },
                            },
                        },
                        clear: async () => undefined,
                    },
                };
            },
            dispose: () => {
                if (!disposed) {
                    disposed = true;
                    ownerStats.disposed += 1;
                    ownerStats.live -= 1;
                }
            },
        };
    },
}));

import MDResultsPane from '../../src/components/MDResultsPane';

const response = (data: unknown) => ({ data, status: 200, statusText: 'OK', headers: {}, config: {} });
const shaA = 'a'.repeat(64);
const shaB = 'b'.repeat(64);

const seedJob = (client: QueryClient, jobId: string, marker: string, replicas = 1) => {
    client.setQueryData(['md-summary', jobId], response({
        schema: 'bms.md.summary.v1', job_id: jobId, status: 'completed', result_state: 'completed',
        source: 'validated_job_owned_manifests', bounded: true, aggregate_manifest_sha256: shaA,
        replica_count: replicas, artifact_count: replicas * 3,
        replicas: Array.from({ length: replicas }, (_, replica) => ({ replica, status: 'completed', engine: { name: 'gromacs' }, performance: {} })),
        analysis_status: 'completed', trajectory_playback: { supported: false, reason: 'not qualified' },
    }));
    client.setQueryData(['md-artifacts', jobId], response({
        schema: 'bms.md.artifact-inventory.v1', job_id: jobId, source: 'validated_job_owned_manifests', bounded: true,
        artifacts: Array.from({ length: replicas }, (_, replica) => [
            { id: `top-${marker}-${replica}`, replica, name: `final-${marker}-${replica}.pdb`, bytes: 1, sha256: shaA, semantic_role: 'analysis_topology', atom_order_identity: `order-${replica}`, format: 'pdb', content_url: `/top-${marker}-${replica}` },
            { id: `traj-${marker}-${replica}`, replica, name: `production-${marker}-${replica}.xtc`, bytes: 1, sha256: shaB, semantic_role: 'analysis_trajectory', atom_order_identity: `order-${replica}`, format: 'xtc', content_url: `/traj-${marker}-${replica}` },
            { id: `final-${marker}-${replica}`, replica, name: `final-${marker}-${replica}.pdb`, bytes: 1, sha256: shaA, semantic_role: 'representative_structure', atom_order_identity: null, format: 'pdb', content_url: `/final-${marker}-${replica}`, selection_method: `terminal_${marker}`, source_frame: replica + 10, time_ps: 100, source_trajectory_sha256: shaB },
        ]).flat(),
    }));
    client.setQueryData(['md-analysis', jobId], response({
        schema: 'bms.md.analysis-report-set.v1', job_id: jobId, status: 'completed', bounded: true,
        replica_states: Array.from({ length: replicas }, (_, replica) => ({ replica, status: 'completed' })), reports: [],
        ensemble: { statistical_unit: 'replica', frame_pooling: false, completed_replicas: replicas, mean_of_replica_mean_rmsd_angstrom: null, sample_stdev_of_replica_mean_rmsd_angstrom: null, mean_of_replica_final_rmsd_angstrom: null, sample_stdev_of_replica_final_rmsd_angstrom: null },
        evidence: { status: 'insufficient_evidence', reason: marker, frames_are_independent_replicates: false },
        retry: { eligible: false, active: false, reason: 'analysis_not_retryable' },
    }));
};

afterEach(() => { document.body.replaceChildren(); });

describe('mounted MD results and sole Molstar route lifecycle', () => {
    it('tears down A before B, isolates stale A cache updates, switches replicas, and disposes the sole host', async () => {
        Object.assign(ownerStats, { initialized: 0, disposed: 0, live: 0, maximumLive: 0, representationPresets: [] });
        const client = new QueryClient({ defaultOptions: { queries: { retry: false, staleTime: Infinity } } });
        seedJob(client, 'job-a', 'A');
        seedJob(client, 'job-b', 'B', 2);
        const container = document.createElement('div');
        document.body.appendChild(container);
        const root = createRoot(container);
        const renderJob = async (jobId: string) => {
            await act(async () => {
                root.render(<StrictMode><QueryClientProvider client={client}><MDResultsPane key={jobId} jobId={jobId} /></QueryClientProvider></StrictMode>);
                await new Promise((resolve) => setTimeout(resolve, 20));
            });
        };

        const waitForSoleHost = async () => vi.waitFor(
            () => expect(container.querySelectorAll('[data-bms-structure-viewer-host]').length).toBe(1),
            { timeout: 1500, interval: 20 },
        );

        await renderJob('job-a');
        await act(async () => {
            await vi.dynamicImportSettled();
            await Promise.resolve();
        });
        await waitForSoleHost();
        expect(ownerStats.representationPresets).toContain('auto');
        const paneA = container.querySelector('[data-bms-result-pane="molecular-dynamics"]');
        expect(paneA).toBeTruthy();
        await renderJob('job-b');
        await waitForSoleHost();
        const paneB = container.querySelector('[data-bms-result-pane="molecular-dynamics"]');
        expect(paneB).toBeTruthy();
        expect(paneB).not.toBe(paneA);
        expect(container.textContent).toContain('terminal_B');
        expect(container.querySelectorAll('[data-bms-structure-viewer-host]').length).toBe(1);
        expect(container.querySelectorAll('canvas').length).toBeLessThanOrEqual(1);

        seedJob(client, 'job-a', 'STALE_A');
        await act(async () => { await Promise.resolve(); });
        expect(container.textContent).toContain('terminal_B');
        expect(container.textContent).not.toContain('STALE_A');

        const replicaSelect = container.querySelector('select');
        expect(replicaSelect).toBeTruthy();
        await act(async () => {
            if (replicaSelect) {
                replicaSelect.value = '1';
                replicaSelect.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
        expect(container.textContent).toContain('source frame 11');
        expect(container.querySelectorAll('[data-bms-structure-viewer-host]').length).toBe(1);

        await act(async () => { root.unmount(); });
        expect(container.childElementCount).toBe(0);
        expect(container.querySelectorAll('[data-bms-structure-viewer-host]').length).toBe(0);
        expect(container.querySelectorAll('canvas').length).toBe(0);
        expect(ownerStats.initialized).toBeGreaterThanOrEqual(2);
        expect(ownerStats.maximumLive).toBe(1);
        expect(ownerStats.live).toBe(0);
        expect(ownerStats.disposed).toBe(ownerStats.initialized);
        client.clear();
    });
});
