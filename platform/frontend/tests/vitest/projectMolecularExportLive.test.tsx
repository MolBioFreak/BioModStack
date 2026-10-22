/** Explicit opt-in dogfood: only a clearly labelled QA project may be mutated. */
import React, { act } from 'react';
import { request } from 'node:http';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { api, fetchMolecularRevisionSummaries, fetchMolBioNgsDomainState, fetchProjectHub } from '../../src/lib/api';
import { ProjectAttachmentDialog } from '../../src/components/project-manager/ProjectAttachmentDialog';
import { MOLECULAR_MEMBER_ADAPTER } from '../../src/lib/molecularProjectMembership';

const origin = process.env.BNS_DOGFOOD_ORIGIN;
const projectId = process.env.BNS_DOGFOOD_PROJECT;
const experimentId = process.env.BNS_DOGFOOD_EXPERIMENT;
const domainId = process.env.BNS_DOGFOOD_DOMAIN;
const sequenceId = process.env.BNS_DOGFOOD_SEQUENCE;
const setup = process.env.BNS_DOGFOOD_SETUP === '1';
const includeHistory = process.env.BNS_DOGFOOD_HISTORY === '1';
it.skipIf(!origin || !projectId || (!setup && (!experimentId || !domainId)) || !sequenceId)('exports from the mounted real dialog into the live native PM stores and reopens the result', async () => {
    const previous = api.defaults.adapter;
    // This is a mounted component/API integration check, not a browser session.
    api.defaults.adapter = async (config) => {
        const url = new URL(config.url!, origin);
        for (const [key, value] of Object.entries(config.params ?? {})) if (value != null) url.searchParams.set(key, String(value));
        return new Promise((resolve, reject) => {
            const req = request(url, { method: config.method?.toUpperCase(), headers: { 'Content-Type': 'application/json' } }, (response) => {
                let body = ''; response.setEncoding('utf8'); response.on('data', (chunk) => body += chunk);
                response.on('end', () => {
                    const data = body ? JSON.parse(body) : null;
                    if ((response.statusCode ?? 500) >= 400) return reject(Object.assign(new Error(JSON.stringify(data)), { isAxiosError: true, response: { status: response.statusCode, data } }));
                    resolve({ data, status: response.statusCode!, statusText: response.statusMessage!, headers: {}, config });
                });
            });
            req.on('error', reject); if (config.data) req.write(config.data); req.end();
        });
    };
    let root: ReturnType<typeof createRoot> | undefined;
    const host = document.createElement('div'); document.body.append(host);
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    try {
        const project = (await api.get(`/api/projects/${projectId}`)).data;
        expect(project.name).toMatch(/^BNS integration check/);
        const revision = (await fetchMolecularRevisionSummaries(sequenceId!, 1))[0];
        const state = domainId ? await fetchMolBioNgsDomainState(domainId) : null;
        let activeExperimentId = experimentId;
        let activeDomainId = domainId;
        let attachedState: string | undefined;
        root = createRoot(host);
        await act(() => root!.render(<MemoryRouter><QueryClientProvider client={client}><ProjectAttachmentDialog
            open initialProjectId={projectId} source={{ adapterId: MOLECULAR_MEMBER_ADAPTER,
                entityId: new URLSearchParams({ sequence_id: sequenceId!, revision_id: revision.revision_id }).toString(),
                label: 'Public QA plasmid', availability: 'available', revision: revision.revision_id,
                historyJournal: [{ timestamp: '2026-10-22', label: 'QA annotation', summary: 'Recorded local journal preserved by Project export' }],
            }} destination={experimentId && domainId ? { globalExperimentId: experimentId, domainExperimentId: domainId, stateRevisionId: state?.current_state_revision_id } : undefined}
            onClose={() => {}} onAttached={(_receipt, nextState, destination) => { attachedState = nextState; activeExperimentId = destination?.globalExperimentId; activeDomainId = destination?.domainExperimentId; }} /></QueryClientProvider></MemoryRouter>));
        async function until(check: () => boolean, timeout = 15000) {
            const deadline = Date.now() + timeout;
            while (!check()) {
                const failure = host.querySelector('[role="alert"]');
                if (failure?.textContent) throw new Error(`Mounted export failed: ${failure.textContent}`);
                if (Date.now() > deadline) throw new Error(`Mounted export did not complete: ${host.textContent}`);
                await act(async () => { await new Promise((resolve) => setTimeout(resolve, 50)); });
            }
        }
        expect(host.querySelector('[aria-label="Attachment Global Experiment"]')).toBeNull();
        expect(host.textContent).not.toContain('Global Project');
        await until(() => [...host.querySelectorAll('button')].some((button) => button.textContent === 'Export to Project' && !button.disabled));
        if (includeHistory) await act(() => host.querySelector<HTMLInputElement>('input[type="checkbox"]')!.click());
        await act(() => [...host.querySelectorAll('button')].find((button) => button.textContent === 'Export to Project')!.click());
        await until(() => Boolean(attachedState), 60000);
        const reopened = await fetchProjectHub(projectId!, activeExperimentId!, activeDomainId!, attachedState!);
        expect(reopened.plasmids.some((item) => item.sequence_id === sequenceId)).toBe(true);
        expect(reopened.project.add_plasmid_href).toContain(`domain_experiment_id=${activeDomainId}`);
        if (includeHistory) {
            const notes = (await api.get(`/api/projects/${projectId}/experiments/${activeExperimentId}/domains/${activeDomainId}/records`)).data.items;
            expect(notes.some((item: { body: string }) => item.body.includes('Recorded local journal preserved by Project export'))).toBe(true);
        }
    } finally {
        if (root) await act(() => root!.unmount()); client.clear(); host.remove(); api.defaults.adapter = previous;
    }
}, 90000);
