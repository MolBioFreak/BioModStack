import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AlignmentPage from '../../src/components/alignments/AlignmentPage';
import { parseFamilyAlignmentCapabilities } from '../../src/lib/projectManager';
import { api } from '../../src/lib/api';

const getSetup = vi.hoisted(() => vi.fn());
vi.mock('../../src/lib/projectManager', async original => ({ ...(await original<Record<string, unknown>>()), getProjectWorkflowSetup: getSetup }));
const row = (id: string, domain: string) => ({ capability_id: id, label: id, domain_kind: domain, project_setup_state: 'ready', project_setup_adapter_id: 'adapter', project_native_owner_id: `${id}:family_msa`, safe_setup_destination: `/alignments?model=${id}&mode=family_msa`, launch_mode: 'typed_launcher_handoff', product_taxonomy: { family: 'family_alignment' } });

describe('alignment route receiver', () => {
    it('parses both server capability domains and rejects mismatched routes', () => {
        const inventory = { schema: 'bms.family-msa-project-workflow-picker.v1', capabilities: [row('family_msa_protein', 'protein_in_silico'), row('family_msa_nucleic', 'ngs_molbio')] };
        expect(parseFamilyAlignmentCapabilities(inventory).map(x => x.domain_kind)).toEqual(['protein_in_silico', 'ngs_molbio']);
        expect(() => parseFamilyAlignmentCapabilities({ ...inventory, capabilities: [row('family_msa_nucleic', 'protein_in_silico')] })).toThrow();
    });
    it('reopens exact retained Project documents and downloads', async () => {
        const get = vi.spyOn(api, 'get').mockImplementation(async (url) => ({data: String(url).includes('/documents/') ? {result_id:'a', rows:[{row_id:'r', label:'one', aligned:'AC'}]} : {artifacts:[{id:'a', role:'alignment'}]}}));
        const div = document.createElement('div'); const root = createRoot(div); const client = new QueryClient();
        await act(async () => { root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/alignments?project_id=p&receipt_id=r']}><Routes><Route path="/alignments" element={<AlignmentPage/>}/></Routes></MemoryRouter></QueryClientProvider>); });
        await act(async () => { await new Promise(r => setTimeout(r, 20)); });
        expect(div.textContent).toContain('Result: a');
        expect(div.querySelector('a[href="/api/family-msa/projects/p/receipts/r/artifacts/a"]')).not.toBeNull();
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
