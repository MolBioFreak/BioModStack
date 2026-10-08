import React, { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => Object.fromEntries(['getProject','getGlobalExperiment','getDomainExperiment','getProjectSummary','listDomainCapabilities','listDomainWorkflowPlans','getDomainWorkflowPlan','listDomainWorkflowPlanRevisions','getDomainRunGroup','cloneDomainRunIntent','launchDomainRunGroup','resubmitDomainRunGroup','listDomainResearchRecords','prepareDomainWorkflowPlanRevision','issuePreparedLaunchContext'].map(key => [key, vi.fn()])));
vi.mock('../../src/lib/projectManager', async (original) => ({ ...await original<Record<string, unknown>>(), ...api }));
import { ProteinProjectWorkspace } from '../../src/components/project-manager/protein/ProteinProjectWorkspace';
let root: Root, container: HTMLDivElement;
const scope = ['p', 'g', 'd'];
const summary = () => ({tasks: [], result_previews: [], warnings: [], source_receipt_ids: [], selection: {title: 'Protein'}, runs: {items: [], next_cursor: null}, pagination: {lineage: {items: [], next_cursor: null}, results: {items: [], next_cursor: null}, activity: {items: [], next_cursor: null}}});
beforeEach(() => {
 vi.resetAllMocks(); container = document.createElement('div'); document.body.append(container); root = createRoot(container);
 api.getProject.mockResolvedValue({id:'p', name:'Project'}); api.getGlobalExperiment.mockResolvedValue({id:'g', parent_id:'p', name:'Experiment'});
 api.getDomainExperiment.mockResolvedValue({id:'d', parent_id:'g', current_revision_id:'rev-d', payload:{domain_kind:'protein_in_silico',domain_payload:{schema:'bms.protein-in-silico-experiment.v3',experiment_mode:'exploration',targets:[{target_id:'target',label:'Canonical target',role:'target',source_receipt_ids:['source'],dataset_member_refs:[]}]}}});
 api.getProjectSummary.mockResolvedValue(summary()); api.listDomainCapabilities.mockResolvedValue({items:[]}); api.listDomainWorkflowPlans.mockResolvedValue({items:[]}); api.getDomainWorkflowPlan.mockResolvedValue(null); api.listDomainWorkflowPlanRevisions.mockResolvedValue({items:[]}); api.listDomainResearchRecords.mockResolvedValue({items:[],next_cursor:null});
});
afterEach(async () => { await act(async () => root.unmount()); container.remove(); });
async function flush() { await act(async () => {await new Promise(resolve => setTimeout(resolve, 5));}); }
async function wait(check: () => void) { for(let i=0;i<30;i++){try {check();return;}catch{await flush();}}check(); }
async function render(section: string, extra = '') { await act(async () => root.render(<QueryClientProvider client={new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}})}><MemoryRouter initialEntries={[`/?section=${section}${extra}`]}><ProteinProjectWorkspace projectId="p" globalExperimentId="g" domainExperimentId="d"/></MemoryRouter></QueryClientProvider>)); }
async function click(text:string) { const button=Array.from(container.querySelectorAll('button')).find(b=>b.textContent===text); expect(button).toBeDefined(); await act(async()=>button!.click()); }
it('keeps exact targets available when optional summary fails', async()=>{api.getProjectSummary.mockRejectedValue(new Error('projection down'));await render('targets');await wait(()=>expect(container.textContent).toContain('Canonical target'));expect(container.textContent).toContain('Project projection unavailable');expect(container.textContent).not.toContain('Protein workspace unavailable');});
it('retains strict hierarchy authority failure', async()=>{api.getDomainExperiment.mockResolvedValue({id:'d',parent_id:'foreign'});await render('targets');await wait(()=>expect(container.textContent).toContain('not an exact Protein Domain'));expect(container.textContent).not.toContain('Canonical target');});
it('shows one exact group operation in ordinary Workflows without launching on navigation',async()=>{api.getDomainRunGroup.mockResolvedValue({run_group_id:'group',generation:7,runs:[{run_id:'run',attempts:[{attempt_id:'attempt'}]}]});await render('plans','&run_group_id=group&run_group_action=clone&source_run_id=run&source_attempt_id=attempt');await wait(()=>expect(container.textContent).toContain('Clone to editable Plan'));expect(api.getDomainRunGroup).toHaveBeenCalledWith(...scope,'group',expect.any(AbortSignal));expect(api.cloneDomainRunIntent).not.toHaveBeenCalled();expect(api.launchDomainRunGroup).not.toHaveBeenCalled();expect(container.textContent).toContain('Workflow Plan');});
it('rejects a foreign source attempt instead of dropping the requested operation',async()=>{api.getDomainRunGroup.mockResolvedValue({run_group_id:'group',generation:7,runs:[{run_id:'run',attempts:[{attempt_id:'other'}]}]});await render('plans','&run_group_id=group&run_group_action=clone&source_run_id=run&source_attempt_id=attempt');await wait(()=>expect(container.textContent).toContain('no verified exact source'));expect(Array.from(container.querySelectorAll('button')).find(b=>b.textContent==='Clone to editable Plan')?.disabled).toBe(true);});
it('pages exact Domain history and replaces rows',async()=>{api.getProjectSummary.mockImplementation((_id,options)=>{const value=summary();value.pagination.activity={items:[{id:options.activityCursor?'e2':'e1',event_type:options.activityCursor?'second_event':'first_event',created_at:'2026-09-11T00:00:00Z',resource_id:'d'}] as never[],next_cursor:options.activityCursor?null:'cursor' as never};return Promise.resolve(value);});await render('history');await wait(()=>expect(container.textContent).toContain('first event'));await click('Next history page');await wait(()=>expect(container.textContent).toContain('second event'));expect(container.textContent).not.toContain('first event');expect(api.getProjectSummary).toHaveBeenLastCalledWith('p',expect.objectContaining({selectedNodeKey:'virtual_folder:d:activity',activityCursor:'cursor'}));await click('First page');await wait(()=>expect(container.textContent).toContain('first event'));});
it('pages Domain research records independently of unavailable lineage',async()=>{api.getProjectSummary.mockRejectedValue(new Error('lineage down'));api.listDomainResearchRecords.mockImplementation((_p,_g,_d,_signal,cursor)=>Promise.resolve({items:[{id:cursor?'r2':'r1',body:cursor?'Second note':'First note',record_kind:'note',created_at:'2026-09-11T00:00:00Z',source_receipt_ids:[]}],next_cursor:cursor?null:'next-record'}));await render('evidence');await wait(()=>expect(container.textContent).toContain('First note'));await click('Next research records page');await wait(()=>expect(container.textContent).toContain('Second note'));expect(container.textContent).not.toContain('First note');expect(api.listDomainResearchRecords).toHaveBeenLastCalledWith(...scope,expect.any(AbortSignal),'next-record');});

it('clones the explicitly selected source through the shared exact API', async () => {
 api.getDomainRunGroup.mockResolvedValue({run_group_id:'group',generation:7,runs:[{run_id:'run',attempts:[{attempt_id:'attempt'}]}]});
 api.cloneDomainRunIntent.mockResolvedValue({project_id:'p',global_experiment_id:'g',domain_experiment_id:'d',source_run_group_id:'group',source_run_id:'run',source_attempt_id:'attempt',new_workflow_plan_id:'new-plan'});
 await render('plans','&run_group_id=group&run_group_action=clone&source_run_id=run&source_attempt_id=attempt');
 await wait(()=>expect(api.getDomainRunGroup).toHaveBeenCalled());
 const operator=container.querySelector('[aria-label="Requested run operation"]')!;
 const inputs=operator.querySelectorAll('input');
 await act(async()=>{for(const [i,input] of Array.from(inputs).entries()){Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(input,i===0?'Reviewed clone':'Preserve source intent');input.dispatchEvent(new Event('input',{bubbles:true}));}});
 await click('Clone to editable Plan');
 await wait(()=>expect(api.cloneDomainRunIntent).toHaveBeenCalledWith('p','g','d','group',{expected_run_group_generation:7,source_run_id:'run',source_attempt_id:'attempt',new_workflow_name:'Reviewed clone',change_summary:'Preserve source intent',expected_domain_revision_id:'rev-d'}));
 expect(api.launchDomainRunGroup).not.toHaveBeenCalled();
});

it('continues workflow setup tasks without accumulating the previous page',async()=>{
 api.getProjectSummary.mockImplementation((_id,options)=>Promise.resolve({...summary(),tasks:[{setup_context_id:options.taskCursor?'setup2':'setup1',global_experiment_id:'g',setup_state:'open',workflow_label:options.taskCursor?'Second workflow':'First workflow',experiment_name:'Experiment',reopen_route:'/submit',allowed_actions:[]}],pagination:{...summary().pagination,task_next_cursor:options.taskCursor?null:'task-next'}}));
 await render('overview');await wait(()=>expect(container.textContent).toContain('First workflow'));await click('Next overview page');await wait(()=>expect(container.textContent).toContain('Second workflow'));expect(container.textContent).not.toContain('First workflow');expect(api.getProjectSummary).toHaveBeenLastCalledWith('p',expect.objectContaining({taskCursor:'task-next',taskLimit:25}));
});

it('resubmits through the existing native handoff with a valid exact Project return route', async () => {
 api.getDomainRunGroup.mockResolvedValue({run_group_id:'group',generation:7,runs:[{run_id:'run',attempts:[{attempt_id:'attempt'}]}]});
 api.listDomainWorkflowPlans.mockResolvedValue({items:[{plan_id:'plan',name:'Replacement',capability_id:'protein.test'}]});
 api.getDomainWorkflowPlan.mockResolvedValue({plan_id:'plan',current_revision_id:'plan-rev',domain_revision_id:'rev-d',updated_at:'fixed',capability_contract:{capability:{launch_mode:'typed_launcher_handoff',canonical_source_destination:'/submit'},parameter_schema:{type:'object',additionalProperties:false,properties:{}}}});
 api.listDomainWorkflowPlanRevisions.mockResolvedValue({items:[{revision_id:'plan-rev',revision_number:1}]});
 api.prepareDomainWorkflowPlanRevision.mockResolvedValue({preparation_id:'prep',status:'valid',normalized_request_sha256:'request'});
 api.issuePreparedLaunchContext.mockImplementation(async (_p,_g,_d,_prep,uri) => {
   const returned = new URL(uri, 'http://test');
   expect(returned.pathname).toBe('/projects/p');
   expect(Object.fromEntries(returned.searchParams)).toEqual({focus:'g',selected:'virtual_folder:d:runs'});
   return {project_id:'p',global_experiment_id:'g',domain_experiment_id:'d',workflow_id:'plan',workflow_revision_id:'plan-rev',preparation_id:'prep',normalized_request_sha256:'request',launch_context_id:'ctx'};
 });
 api.resubmitDomainRunGroup.mockResolvedValue({runs:[{preparation_id:'prep',attempts:[{attempt_id:'new-attempt',state:'pending',launch_context:{launch_context_id:'ctx'}}]}]});
 await render('plans','&run_group_id=group&run_group_action=resubmit&source_run_id=run&source_attempt_id=attempt');
 await wait(()=>expect(container.textContent).toContain('Replacement'));
 const select=Array.from(container.querySelectorAll('select')).find(item=>Array.from(item.options).some(option=>option.value==='plan'))!;
 await act(async()=>{Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype,'value')!.set!.call(select,'plan');select.dispatchEvent(new Event('change',{bubbles:true}));});
 await wait(()=>expect(Array.from(container.querySelectorAll('button')).find(item=>item.textContent==='Prepare selected revision')?.disabled).toBe(false));
 expect(api.resubmitDomainRunGroup).not.toHaveBeenCalled();
 await click('Prepare selected revision');
 await wait(()=>expect(container.textContent).toContain('Preparation valid'));
 await click('Resubmit group and open native Protein setup');
 await wait(()=>expect(api.resubmitDomainRunGroup).toHaveBeenCalledWith('p','g','d','group',7,[{preparation_id:'prep',launch_context_id:'ctx'}]));
 expect(api.launchDomainRunGroup).not.toHaveBeenCalled();
});

