import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, expect, test, vi } from 'vitest';
import { ProjectInspector } from '../../src/components/project-manager/ProjectInspector';
import { VirtualFolderPanel } from '../../src/components/project-manager/VirtualFolderPanel';
import type { ProjectManagerReadModel } from '../../src/lib/projectManager';
let root: Root | undefined;
afterEach(async () => { await act(async () => root?.unmount()); document.body.replaceChildren(); });
async function mount(component: React.ReactNode) { const host = document.createElement('div'); document.body.append(host); root = createRoot(host); await act(async () => root!.render(<MemoryRouter>{component}</MemoryRouter>)); return host; }
test('inspector presents repeated context once, retains distinct values and exact original context', async () => {
 const summary = { tree: { nodes: [] }, selection: { node_key:'domain:d', node_type:'domain_experiment', title:'Domain', subtitle:'Goal', available_actions:[], canonical_identity:{}, reconciliation:{state:'current'}, summary:{ objective:'Goal', domain_payload:{comparison_groups:['A'], design_constraints:{temperature:20}, experiment_mode:'binder', missing:[], count:0, flag:false}}, scientific_context:{scientific_objective:'Goal', comparison_groups:['A'], design_constraints:{temperature:20}, experiment_mode:'different', absent:null}}, source_receipt_ids:[], adapter_versions:[] } as unknown as ProjectManagerReadModel;
 const noop = vi.fn(); const host=await mount(<ProjectInspector summary={summary} onOpenCanonical={noop} onAddExisting={noop} onCreateDomain={noop} onEdit={noop} onArchive={noop} onRestore={noop} onRecord={noop}/>);
 const visible = host.cloneNode(true) as HTMLElement; visible.querySelectorAll('details').forEach(d=>d.remove());
 expect(visible.textContent?.match(/Goal/g)).toHaveLength(1);
 expect(visible.textContent?.match(/Comparison Groups/g)).toHaveLength(1);
 expect(visible.textContent?.match(/Design Constraints/g)).toHaveLength(1);
 expect(visible.textContent).toContain('different'); expect(visible.textContent).not.toContain('Missing');
 expect(host.querySelector('pre')?.textContent).toContain(JSON.stringify('comparison_groups'));
 expect(summary.selection.scientific_context.comparison_groups).toEqual(['A']);
});
test('notes collapse only explicitly superseded loaded records and keep exact bodies selectable', async () => {
 const old={resource_id:'old', record_kind:'note', body:'Old provenance\noriginal title\noriginal IDs\nunchanged settings'};
 const current={resource_id:'new', record_kind:'note', body:'[Named result](/designs/job)\nstatus\nsource\nexact provenance', supersedes_record_id:'old'};
 const summary={pagination:{notes:{items:[current,old],next_cursor:null}}} as unknown as ProjectManagerReadModel;
 const select=vi.fn();const host=await mount(<VirtualFolderPanel folder="notes" summary={summary} onLoadMore={vi.fn()} onSelectRecord={select}/>);
 const previous=[...host.querySelectorAll('details')].find(d=>d.querySelector('summary')?.textContent==='Previous records (1)')!;
 expect(previous.open).toBe(false); expect(previous.textContent).toContain('unchanged settings');
 expect(host.querySelector('a')?.getAttribute('href')).toContain('/designs/job');
 await act(async()=>{previous.querySelector('summary')!.click(); previous.querySelector('button')!.click();});
 expect(select).toHaveBeenCalledWith('notes',old);
 expect(summary.pagination.notes.items).toEqual([current,old]);
});
test('an unobserved successor or same-kind note does not hide original records', async () => {
 const summary={pagination:{notes:{items:[{resource_id:'a',record_kind:'note',body:'First',supersedes_record_id:'not-loaded'},{resource_id:'b',record_kind:'note',body:'Second'}],next_cursor:'next'}}} as unknown as ProjectManagerReadModel;
 const host=await mount(<VirtualFolderPanel folder="notes" summary={summary} onLoadMore={vi.fn()} onSelectRecord={vi.fn()}/>);
 expect(host.textContent).not.toContain('Previous records');expect(host.textContent).toContain('First');expect(host.textContent).toContain('Second');expect(host.textContent).toContain('Next notes page');
});

test('typed project references preserve exact named download, source and DOI links', async () => {
 const references = [
  {kind:'url',label:'Challenge rules',value:'https://proteinbase.com/competitions/example?round=3'},
  {kind:'url',label:'MSA',value:'/api/files/download/inputs/reference/target.a3m'},
  {kind:'other',label:'Native results',value:'/designs/job?native_candidate=%5B%22x%22%5D'},
  {kind:'doi',label:'Primary paper',value:'10.1038/example'},
  {kind:'accession',label:'Target',value:'P43220'},
 ];
 const original=JSON.stringify(references);
 const summary={tree:{nodes:[]},selection:{node_key:'project:p',node_type:'project',title:'Review',available_actions:[],canonical_identity:{},reconciliation:{state:'current'},summary:{external_references:references},scientific_context:{}},source_receipt_ids:[],adapter_versions:[]} as unknown as ProjectManagerReadModel;
 const noop=vi.fn();const host=await mount(<ProjectInspector summary={summary} onOpenCanonical={noop} onAddExisting={noop} onCreateDomain={noop} onEdit={noop} onArchive={noop} onRestore={noop} onRecord={noop}/>);
 const links=[...host.querySelectorAll('a')];
 expect(links.map(a=>[a.textContent,a.getAttribute('href')])).toEqual([
  ['Challenge rules',references[0].value],['MSA',references[1].value],['Native results',references[2].value],['Primary paper','https://doi.org/10.1038/example'],
 ]);
 expect(links.every(a=>a.target==='_blank' && a.rel==='noopener noreferrer')).toBe(true);
 expect(host.textContent).toContain('Target · P43220');
 expect(JSON.stringify(references)).toBe(original);
});

test('reference metadata does not turn unsafe schemes or arbitrary note URLs into links', async () => {
 const references=['javascript:alert(1)','data:text/html,test','//foreign.example/path','https://user:secret@example.org','https://example.org/ bad','https://','/unknown/path','https://example.org/\\\\evil'].map((value,i)=>({kind:'url',label:`Reference ${i}`,value}));
 const summary={tree:{nodes:[]},selection:{node_key:'project:p',node_type:'project',title:'Review',available_actions:[],canonical_identity:{},reconciliation:{state:'current'},summary:{external_references:references,description:'[Plain remote note](https://example.org)'},scientific_context:{}},source_receipt_ids:[],adapter_versions:[]} as unknown as ProjectManagerReadModel;
 const noop=vi.fn();const host=await mount(<ProjectInspector summary={summary} onOpenCanonical={noop} onAddExisting={noop} onCreateDomain={noop} onEdit={noop} onArchive={noop} onRestore={noop} onRecord={noop}/>);
 expect(host.querySelectorAll('a')).toHaveLength(0);
 expect(host.textContent).toContain('Reference 0 · javascript:alert(1)');
 expect(host.textContent).toContain('[Plain remote note](https://example.org)');
});
