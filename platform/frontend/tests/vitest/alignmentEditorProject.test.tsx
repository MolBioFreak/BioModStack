import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it, vi } from 'vitest';
import AlignmentEditor from '../../src/components/alignments/AlignmentEditor';
const mocked = vi.hoisted(() => ({save:vi.fn(), start:vi.fn(), context:vi.fn(), submit:vi.fn(), post:vi.fn(), setup:{project_id:'p',setup_context_id:'s',draft:{molecule:'protein',engine:'muscle',fasta:'>one\nAA\n>two\nBB',drafts:{muscle:{engine:'muscle',threads:3,mode:'super5',permutation:'none',perturb:0,ensemble:'none',replicates:null}}}}}));
vi.mock('../../src/components/project-manager/ProjectWorkflowSetup',()=>({useProjectWorkflowSetup:()=>({active:true,setup:mocked.setup,saveDraft:mocked.save,startRun:mocked.start})}));
vi.mock('../../src/lib/api',()=>({submitJob:mocked.submit,api:{post:mocked.post}}));
vi.mock('../../src/lib/projectManager',()=>({getLaunchContext:mocked.context}));
describe('Project alignment editor',()=>{
 it('reopens flattened state and submits the pinned Project scheduler',async()=>{
  const div=document.createElement('div'), root=createRoot(div);
  mocked.save.mockResolvedValue({}); mocked.start.mockResolvedValue({launch_context_id:'ctx'});
  const pinned={name:'Pinned',model_id:'family_msa_protein',mode:'family_msa',params:{marker:'server'}};
  mocked.context.mockResolvedValue({pinned_scheduler:pinned}); mocked.submit.mockResolvedValue({data:{id:'job'}}); mocked.post.mockResolvedValue({data:{return_uri:'/projects/p'}});
  await act(async()=>root.render(<MemoryRouter><AlignmentEditor model="family_msa_protein"/></MemoryRouter>));
  expect((div.querySelector('[aria-label="Engine"]') as HTMLSelectElement).value).toBe('muscle');
  expect((div.querySelector('[aria-label="Threads"]') as HTMLInputElement).value).toBe('3');
  expect((div.querySelector('[aria-label="FASTA roster"]') as HTMLTextAreaElement).value).toBe(mocked.setup.draft.fasta);
  const buttons=[...div.querySelectorAll('button')];
  await act(async()=>{buttons.find(x=>x.textContent==='Save Project draft')!.click()});
  await act(async()=>{buttons.find(x=>x.textContent==='Start run')!.click()});
  expect(mocked.save).toHaveBeenCalledWith(expect.objectContaining({native_job_request:expect.objectContaining({params:expect.objectContaining({settings:expect.objectContaining({engine:'muscle',mode:'super5',threads:3})})})}));
  expect(mocked.start).toHaveBeenCalledWith(mocked.save.mock.calls[0][0],{stayInEditor:true});
  expect(mocked.submit).toHaveBeenCalledWith({...pinned,launch_context_id:'ctx'},{launchContext:true});
  expect(mocked.post).toHaveBeenCalledWith('/api/launch-contexts/ctx/bind',{job_id:'job'});
  await act(async()=>root.unmount());
 });
});