/** Explicit opt-in dogfood: only a clearly labelled QA project may be mutated. */
import React, { act } from 'react';
import { request } from 'node:http';
import { Buffer } from 'node:buffer';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter } from 'react-router-dom';
import { createRoot } from 'react-dom/client';
import { expect, it } from 'vitest';
import { api, fetchMolecularRevisionSummaries, fetchMolBioNgsDomainState, fetchProjectHub } from '../../src/lib/api';
import { ProjectAttachmentDialog } from '../../src/components/project-manager/ProjectAttachmentDialog';
import { NgsDataRecordDialog, NgsDataRecords } from '../../src/components/project-manager/NgsDataRecords';
import { MOLECULAR_MEMBER_ADAPTER } from '../../src/lib/molecularProjectMembership';

const origin = process.env.BNS_DOGFOOD_ORIGIN;
const projectId = process.env.BNS_DOGFOOD_PROJECT;
const experimentId = process.env.BNS_DOGFOOD_EXPERIMENT;
const domainId = process.env.BNS_DOGFOOD_DOMAIN;
const sequenceId = process.env.BNS_DOGFOOD_SEQUENCE;
const setup = process.env.BNS_DOGFOOD_SETUP === '1';
const includeHistory = process.env.BNS_DOGFOOD_HISTORY === '1';
function installLiveAdapter() {
    const previous = api.defaults.adapter;
    // This is a mounted component/API integration check, not a browser session.
    api.defaults.adapter = async (config) => {
        const url = new URL(config.url!, origin);
        for (const [key, value] of Object.entries(config.params ?? {})) if (value != null) url.searchParams.set(key, String(value));
        let body = config.data;
        let contentType = 'application/json';
        if (body instanceof FormData) {
            const boundary = `bns-qa-${crypto.randomUUID()}`;
            const chunks: Buffer[] = [];
            for (const [key, value] of body.entries()) {
                chunks.push(Buffer.from(`--${boundary}\r\nContent-Disposition: form-data; name="${key}"`));
                if (value instanceof Blob) {
                    const buffer = await new Promise<ArrayBuffer>((resolve, reject) => { const reader = new FileReader(); reader.onload = () => resolve(reader.result as ArrayBuffer); reader.onerror = reject; reader.readAsArrayBuffer(value); });
                    chunks.push(Buffer.from(`; filename="${(value as File).name}"\r\nContent-Type: application/octet-stream\r\n\r\n`), Buffer.from(buffer), Buffer.from('\r\n'));
                } else chunks.push(Buffer.from(`\r\n\r\n${value}\r\n`));
            }
            chunks.push(Buffer.from(`--${boundary}--\r\n`)); body = Buffer.concat(chunks); contentType = `multipart/form-data; boundary=${boundary}`;
        }
        return new Promise((resolve, reject) => {
            const req = request(url, { method: config.method?.toUpperCase(), headers: { 'Content-Type': contentType } }, (response) => {
                let body = ''; response.setEncoding('utf8'); response.on('data', (chunk) => body += chunk);
                response.on('end', () => {
                    const data = body ? (String(response.headers['content-type']).includes('application/json') ? JSON.parse(body) : body) : null;
                    if ((response.statusCode ?? 500) >= 400) return reject(Object.assign(new Error(JSON.stringify(data)), { isAxiosError: true, response: { status: response.statusCode, data } }));
                    resolve({ data, status: response.statusCode!, statusText: response.statusMessage!, headers: {}, config });
                });
            });
            req.on('error', reject); if (body) req.write(body); req.end();
        });
    };
    return () => { api.defaults.adapter = previous; };
}
it.skipIf(!origin || !projectId || (!setup && (!experimentId || !domainId)) || !sequenceId)('exports from the mounted real dialog into the live native PM stores and reopens the result', async () => {
    const restoreAdapter = installLiveAdapter();
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
        if (root) await act(() => root!.unmount()); client.clear(); host.remove(); restoreAdapter();
    }
}, 90000);


it.skipIf(!origin || !projectId)('saves a real FASTQ data record through the mounted form in an empty Project and reopens its file and card', async () => {
    const restoreAdapter = installLiveAdapter();
    const client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: 0 } } });
    const host = document.createElement('div'); document.body.append(host); const root = createRoot(host);
    try {
        const scope = (await api.get(`/api/projects/${projectId}`)).data;
        expect(scope.name).toMatch(/^BNS integration check/);
        const project = (await api.post('/api/projects', { schema: 'bms.project.v2', project_scope: 'ngs_molbio_local', name: 'BNS integration check — empty Project FASTQ intake', status: 'active', tags: ['qa'], change_summary: 'Mounted real data-record form acceptance' })).data;
        let saved = false;
        await act(()=>root.render(<QueryClientProvider client={client}><NgsDataRecordDialog projectId={project.id} projectName={project.name} onClose={()=>{saved=true;}} /></QueryClientProvider>));
        const name=host.querySelector<HTMLInputElement>('[aria-label="NGS record name"]')!;
        await act(()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(name,'QA synthetic FASTQ intake');name.dispatchEvent(new Event('input',{bubbles:true}));});
        const upload=host.querySelector<HTMLInputElement>('[aria-label="Upload NGS data files"]')!;
        const raw='@qa_read\nACGTACGT\n+\nIIIIIIII\n';
        await act(()=>{Object.defineProperty(upload,'files',{value:[new File([raw],'qa.fastq',{type:'text/plain'})]});upload.dispatchEvent(new Event('change',{bubbles:true}));});
        await act(()=>host.querySelector('form')!.dispatchEvent(new Event('submit',{bubbles:true,cancelable:true})));
        const deadline=Date.now()+15000;
        while(!saved){
            if(host.querySelector('[role="alert"]'))throw new Error(host.querySelector('[role="alert"]')!.textContent!);
            if(Date.now()>deadline)throw new Error('Live NGS data-record save timed out');
            await act(async()=>{await new Promise(resolve=>setTimeout(resolve,50));});
        }
        const page=(await api.get(`/api/projects/${project.id}/ngs-data-records`)).data;
        expect(page.items).toHaveLength(1);
        const payload=page.items[0].payload;
        expect(payload.files[0].path).toContain(`inputs/bns-projects/${project.id}/`);
        expect(payload.files[0].name).toBe('qa.fastq');
        expect((await api.get(`/api/files/download/${payload.files[0].path}`)).data).toBe(raw);
        expect((await api.get(`/api/projects/${project.id}/experiments`)).data.items).toEqual([]);
        await act(()=>root.render(<QueryClientProvider client={client}><NgsDataRecords projectId={project.id} onAdd={()=>{}} /></QueryClientProvider>));
        const cardDeadline=Date.now()+5000;
        while(!host.textContent?.includes('QA synthetic FASTQ intake')){if(Date.now()>cardDeadline)throw new Error('Persisted record card did not reopen');await act(async()=>{await new Promise(resolve=>setTimeout(resolve,30));});}
        expect(host.textContent).toContain('qa.fastq');
        process.stdout.write(`NGS_RECORD_QA ${JSON.stringify({project_id:project.id,record_id:page.items[0].id,path:payload.files[0].path})}\n`);
    } finally { await act(()=>root.unmount()); client.clear();host.remove();restoreAdapter(); }
},30000);
