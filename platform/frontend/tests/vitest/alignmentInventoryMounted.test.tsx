import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { readFileSync, writeFileSync } from 'node:fs';
import { describe, expect, it, vi } from 'vitest';
import AlignmentEditor, { settingsInventory } from '../../src/components/alignments/AlignmentEditor';
const mocks = vi.hoisted(() => ({get:vi.fn(),submit:vi.fn()}));
vi.mock('../../src/components/project-manager/ProjectWorkflowSetup',()=>({useProjectWorkflowSetup:()=>({active:false})}));
vi.mock('../../src/lib/api',()=>({submitJob:mocks.submit,api:{get:mocks.get}}));
vi.mock('../../src/lib/projectManager',()=>({getLaunchContext:vi.fn()}));

describe('native alignment inventory',()=>{
 it('preserves numeric enum types and zero defaults from schema metadata',()=>{
  const schema = {$defs:{TwilightSettings:{properties:{engine:{type:'string'},acc_mode:{type:'integer',enum:[0,1,2,3],default:0}, future_numeric:{type:'integer',enum:[0,2],default:0}}}}};
  const result = settingsInventory(schema, 'twilight');
  expect(result.defaults.acc_mode).toBe(0);
  expect(result.fields.find(field=>field.key === 'future_numeric')?.options).toEqual([0,2]);
 });
 it.skipIf(!process.env.BMS_ALIGNMENT_SCHEMA)('renders request-owned schema and sends typed nondefaults',async()=>{
  const schema=JSON.parse(readFileSync(process.env.BMS_ALIGNMENT_SCHEMA!, 'utf8'));
  mocks.get.mockResolvedValue({data:schema}); mocks.submit.mockResolvedValue({data:{id:'job'}});
  const div=document.createElement('div'), root=createRoot(div);
  await act(async()=>root.render(<MemoryRouter><AlignmentEditor model="family_msa_protein"/></MemoryRouter>));
  const select=div.querySelector('[aria-label="Engine"]') as HTMLSelectElement;
  await act(async()=>{select.value='famsa';select.dispatchEvent(new Event('change',{bubbles:true}));});
  const control=(label:string)=>[...div.querySelectorAll('label')].find(x=>x.textContent?.trim().startsWith(label))!.querySelector('input,select') as HTMLInputElement | HTMLSelectElement;
  const matrix=control('sm') as HTMLSelectElement;
  await act(async()=>{matrix.value='MIQS';matrix.dispatchEvent(new Event('change',{bubbles:true}));});
  const gap=control('go') as HTMLInputElement;
  await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(gap,'-14000');gap.dispatchEvent(new Event('input',{bubbles:true}));});
  const disabled=control('dgr') as HTMLInputElement;
  await act(async()=>disabled.click());
  await act(async()=>[...div.querySelectorAll('button')].find(x=>x.textContent==='Start run')!.click());
  expect(mocks.submit).toHaveBeenCalledWith(expect.objectContaining({params:expect.objectContaining({settings:expect.objectContaining({engine:'famsa',sm:'MIQS',go:-14000,dgr:true})})}));
  if (process.env.BMS_ALIGNMENT_PAYLOAD) writeFileSync(process.env.BMS_ALIGNMENT_PAYLOAD,JSON.stringify(mocks.submit.mock.calls[0][0]));
  await act(async()=>root.unmount());
 });
});
