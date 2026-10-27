import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { describe, expect, it, vi } from 'vitest';
import AlignmentEditor from '../../src/components/alignments/AlignmentEditor';
const mocked = vi.hoisted(() => ({save:vi.fn(), start:vi.fn(), setup:{project_id:'p',setup_context_id:'s',draft:{editor_state:{molecule:'protein',engine:'muscle',fasta:'>one\nAA\n>two\nBB',drafts:{muscle:{engine:'muscle',threads:3,mode:'super5',permutation:'none',perturb:0,ensemble:'none',replicates:null}}}}}}));
vi.mock('../../src/components/project-manager/ProjectWorkflowSetup',()=>({useProjectWorkflowSetup:()=>({active:true,setup:mocked.setup,saveDraft:mocked.save,startRun:mocked.start})}));
describe('Project alignment editor',()=>{
 it('reopens saved state and passes the exact request to Project save and launch',async()=>{
  const div=document.createElement('div'), root=createRoot(div);
  mocked.save.mockResolvedValue({}); mocked.start.mockResolvedValue({});
  await act(async()=>root.render(<AlignmentEditor model="family_msa_protein"/>));
  expect((div.querySelector('[aria-label="Engine"]') as HTMLSelectElement).value).toBe('muscle');
  expect((div.querySelector('[aria-label="Threads"]') as HTMLInputElement).value).toBe('3');
  const buttons=[...div.querySelectorAll('button')];
  await act(async()=>{buttons.find(x=>x.textContent==='Save Project draft')!.click()});
  await act(async()=>{buttons.find(x=>x.textContent==='Start run')!.click()});
  expect(mocked.save).toHaveBeenCalledWith(expect.objectContaining({native_job_request:expect.objectContaining({params:expect.objectContaining({settings:expect.objectContaining({engine:'muscle',mode:'super5',threads:3})})})}));
  expect(mocked.start).toHaveBeenCalledWith(mocked.save.mock.calls[0][0]);
  await act(async()=>root.unmount());
 });
});