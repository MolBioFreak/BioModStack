import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AlignmentPage from '../../src/components/alignments/AlignmentPage';
import { parseFamilyAlignmentCapabilities, parseProteinProjectCapabilityInventory } from '../../src/lib/projectManager';
import { api } from '../../src/lib/api';

const getSetup = vi.hoisted(() => vi.fn());
vi.mock('../../src/lib/projectManager', async original => ({ ...(await original<Record<string, unknown>>()), getProjectWorkflowSetup: getSetup }));
const row = (id: string, domain: string) => ({ capability_id: id, label: id, domain_kind: domain, project_setup_state: 'ready', project_setup_adapter_id: 'adapter', project_native_owner_id: `${id}:family_msa`, safe_setup_destination: `/alignments?model=${id}&mode=family_msa`, launch_mode: 'typed_launcher_handoff', product_taxonomy: { family: 'family_alignment' }, scientific_capabilities: { mafft: ['protein', 'dna', 'rna'], muscle: ['protein', 'dna', 'rna'], famsa: ['protein'], twilight: ['dna', 'rna'] }, result_adapter_ids: [`bms.family-msa.${id}.adapter.v1`] });

describe('alignment route receiver', () => {
    it('polls a running Job through publication and opens its document', async () => {
        let published = false;
        const get = vi.spyOn(api, 'get').mockImplementation(async url => {
            const path = String(url);
            if (path === '/api/jobs/live') return {data: {status: 'running'}};
            if (path.includes('/documents/')) return {data: {result_id: 'a', rows: [{row_id: 'r', label: 'one', aligned: 'AC'}]}};
            if (!published) throw new Error('manifest pending (409)');
            return {data: {artifacts: [{id: 'a', role: 'alignment'}]}};
        });
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/alignments/jobs/live']}><Routes><Route path="/alignments/jobs/:jobId" element={<AlignmentPage/>}/></Routes></MemoryRouter></QueryClientProvider>); await new Promise(r => setTimeout(r, 50)); });
        await act(async () => { await new Promise(r => setTimeout(r, 50)); });
        expect(div.textContent).toContain('manifest pending');
        published = true;
        await act(async () => { await new Promise(r => setTimeout(r, 6200)); });
        await act(async () => { await new Promise(r => setTimeout(r, 100)); });
        expect(div.textContent).toContain('Result: a');
        expect(div.textContent).not.toContain('manifest pending');
        await act(async () => root.unmount()); client.clear(); get.mockRestore();
    }, 10000);
    it('cancels the old Job request on identity switch and permits retry after terminal failure', async () => {
        let oldSignal: AbortSignal | undefined; let available = false;
        const get = vi.spyOn(api, 'get').mockImplementation(async (url, config) => {
            const path = String(url);
            if (path === '/api/jobs/old') return {data: {status: 'running'}};
            if (path === '/api/family-msa/jobs/old') { oldSignal = config?.signal as AbortSignal; return await new Promise(() => {}); }
            if (path === '/api/jobs/new') return {data: {status: 'failed'}};
            if (path.includes('/documents/')) return {data: {result_id: 'b', rows: []}};
            if (!available) throw new Error('missing manifest');
            return {data: {artifacts: [{id: 'b', role: 'alignment'}]}};
        });
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        const render = (id: string) => <QueryClientProvider client={client}><MemoryRouter key={id} initialEntries={[`/alignments/jobs/${id}`]}><Routes><Route path="/alignments/jobs/:jobId" element={<AlignmentPage/>}/></Routes></MemoryRouter></QueryClientProvider>;
        await act(async () => { root.render(render('old')); await new Promise(r => setTimeout(r, 30)); });
        await act(async () => { root.render(render('new')); await new Promise(r => setTimeout(r, 50)); });
        expect(oldSignal?.aborted).toBe(true);
        expect(div.textContent).toContain('missing manifest');
        available = true;
        await act(async () => { div.querySelector('button')!.click(); await new Promise(r => setTimeout(r, 60)); });
        await act(async () => { await new Promise(r => setTimeout(r, 60)); });
        expect(div.textContent).toContain('Result: b');
        await act(async () => root.unmount()); client.clear(); get.mockRestore();
    });
    it('parses both server capability domains and rejects mismatched routes', () => {
        const inventory = { schema: 'bms.family-msa-project-workflow-picker.v1', capabilities: [row('family_msa_protein', 'protein_in_silico'), row('family_msa_nucleic', 'ngs_molbio')] };
        expect(parseFamilyAlignmentCapabilities(inventory).map(x => x.domain_kind)).toEqual(['protein_in_silico', 'ngs_molbio']);
        expect(() => parseFamilyAlignmentCapabilities({ ...inventory, capabilities: [row('family_msa_nucleic', 'protein_in_silico')] })).toThrow();
    });
    it('accepts a model/mode native setup only when it matches its owner', () => {
        const capability = { capability_id: 'protein.native.laproteina.evaluate', label: 'evaluate', state: 'ready', adapter_id: 'adapter', native_owner_id: 'laproteina:evaluate', setup_destination: '/submit?model=laproteina&mode=evaluate', source_requirements: [], follow_up_compatible_capability_ids: [] };
        const inventory = {schema: 'bms.protein-project-workflow-picker.v1', capabilities: [capability]};
        expect(parseProteinProjectCapabilityInventory(inventory).capabilities).toHaveLength(1);
        expect(() => parseProteinProjectCapabilityInventory({...inventory, capabilities: [{...capability, setup_destination: '/submit?model=laproteina&mode=generate'}]})).toThrow();
        expect(() => parseProteinProjectCapabilityInventory({...inventory, capabilities: [{...capability, setup_destination: '/submit?model=laproteina&mode=evaluate&mode=generate'}]})).toThrow();
    });
    it('shows retained source observations beside the first-record document', async () => {
        const get = vi.spyOn(api, 'get').mockImplementation(async url => {
            const path = String(url);
            if (path.includes('/documents/')) return {data: {result_id:'a', rows:[{row_id:'r', label:'x', aligned:'AC'}]}};
            if (path.includes('/artifacts/meta')) return {data: {format_observations:['Stockholm contains 2 records; only the first is displayed. Additional records and annotations remain in the original file.']}};
            return {data: {artifacts:[{id:'a', role:'alignment'}, {id:'meta', role:'source_metadata'}, {id:'raw', role:'source'}]}};
        });
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/alignments?project_id=p&receipt_id=r']}><Routes><Route path="/alignments" element={<AlignmentPage/>}/></Routes></MemoryRouter></QueryClientProvider>));
        for (let i = 0; i < 5; i++) await act(async () => { await new Promise(r => setTimeout(r, 30)); });
        expect(div.textContent).toContain('only the first is displayed');
        expect(div.textContent).toContain('Result: a');
        expect(div.querySelector('a[href="/api/family-msa/projects/p/receipts/r/artifacts/raw"]')).not.toBeNull();
        await act(async () => root.unmount()); client.clear(); get.mockRestore();
    });
    it('reopens exact retained Project documents and downloads', async () => {
        const get = vi.spyOn(api, 'get').mockImplementation(async (url) => ({data: String(url).includes('/documents/') ? {result_id:'a', rows:[{row_id:'r', label:'one', aligned:'AC'}]} : {artifacts:[{id:'a', role:'alignment'}]}}));
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/alignments?project_id=p&receipt_id=r']}><Routes><Route path="/alignments" element={<AlignmentPage/>}/></Routes></MemoryRouter></QueryClientProvider>); });
        await act(async () => { await new Promise(r => setTimeout(r, 20)); });
        expect(div.textContent).toContain('Result: a');
        expect(div.textContent).toContain('Project: p');
        expect(div.textContent).not.toContain('No project selected');
        expect(div.querySelector('iframe[title="JBrowseMSA"]')?.getAttribute('sandbox')).toBe('allow-scripts');
        expect(div.textContent).not.toContain('JBrowseMSA is not connected');
        expect(div.querySelector('a[href="/api/family-msa/projects/p/receipts/r/artifacts/a"]')).not.toBeNull();
        expect(get).toHaveBeenCalledWith('/api/family-msa/projects/p/receipts/r/documents/a', expect.anything());
        await act(async () => root.unmount()); client.clear(); get.mockRestore();
    });
    it('receives the workspace_id Project Results continuation', async () => {
        const get = vi.spyOn(api, 'get').mockImplementation(async url => ({data: String(url).includes('/documents/') ? {result_id:'a', rows:[{row_id:'r', label:'one', aligned:'AC'}]} : {artifacts:[{id:'a', role:'alignment'}]}}));
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/alignments?workspace_id=p&receipt_id=r']}><Routes><Route path="/alignments" element={<AlignmentPage/>}/></Routes></MemoryRouter></QueryClientProvider>); });
        await act(async () => { await new Promise(r => setTimeout(r, 30)); });
        expect(div.textContent).toContain('Result: a');
        expect(get).toHaveBeenCalledWith('/api/family-msa/projects/p/receipts/r/documents/a', expect.anything());
        await act(async () => root.unmount()); client.clear(); get.mockRestore();
    });
    it('receives the Job query used by existing Project receipts', async () => {
        const get = vi.spyOn(api, 'get').mockResolvedValue({data:{artifacts:[]}});
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/alignments?job_id=job-1']}><Routes><Route path="/alignments" element={<AlignmentPage/>}/></Routes></MemoryRouter></QueryClientProvider>));
        expect(get).toHaveBeenCalledWith('/api/family-msa/jobs/job-1', expect.anything());
        expect(div.textContent).not.toContain('Alignment library is not connected yet.');
        await act(async () => root.unmount()); client.clear(); get.mockRestore();
    });
    it('loads Project setup then offers its exact return link', async () => {
        let resolve!: (value: unknown) => void;
        getSetup.mockReturnValueOnce(new Promise(r => { resolve = r; }));
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
        await act(async () => root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/alignments?model=family_msa_protein&setup_context_id=s&project_id=p']}><AlignmentPage/></MemoryRouter></QueryClientProvider>));
        expect(div.textContent).toContain('Loading Project workflow setup');
        await act(async () => { resolve({ project_label: 'Project P', experiment_label: 'Experiment E', workflow_label: 'Alignment', state: 'open', return_uri: '/projects/p?focus=e', field_errors: {} }); await new Promise(r => setTimeout(r, 10)); });
        expect(div.textContent).toContain('Project P');
        expect(div.querySelector('a[ href="/projects/p?focus=e" ]')).not.toBeNull();
        await act(async () => root.unmount()); client.clear();
    });
});
