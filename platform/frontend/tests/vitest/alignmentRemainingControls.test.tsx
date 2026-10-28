import React, { act } from 'react';
import { createRoot } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { expect, it, vi } from 'vitest';
import AlignmentEditor, { settingsInventory } from '../../src/components/alignments/AlignmentEditor';
import schema from '../../src/components/alignments/alignment-request.schema.json';
const mocks = vi.hoisted(() => ({get:vi.fn(),submit:vi.fn()}));
vi.mock('../../src/components/project-manager/ProjectWorkflowSetup',()=>({useProjectWorkflowSetup:()=>({active:false})}));
vi.mock('../../src/lib/api',()=>({submitJob:mocks.submit,api:{get:mocks.get}}));
vi.mock('../../src/lib/projectManager',()=>({getLaunchContext:vi.fn()}));

it('renders every new scalar/file control and submits exact aliases, file text, zero and false', async()=>{
    mocks.get.mockResolvedValue({data:schema}); mocks.submit.mockResolvedValue({data:{id:'job'}});
    const div=document.createElement('div'), root=createRoot(div);
    await act(async()=>root.render(<MemoryRouter><AlignmentEditor model="family_msa_protein"/></MemoryRouter>));
    const label=(name:string)=>[...div.querySelectorAll('label')].find(x=>x.childNodes[0]?.textContent?.trim()===name)!;
    const change=async(el:HTMLInputElement|HTMLSelectElement|HTMLTextAreaElement,value:string)=>{
        await act(async()=>{
            const proto=el.tagName==='TEXTAREA'?HTMLTextAreaElement.prototype:el.tagName==='SELECT'?HTMLSelectElement.prototype:HTMLInputElement.prototype;
            Object.getOwnPropertyDescriptor(proto,'value')!.set!.call(el,value);
            el.dispatchEvent(new Event(el.tagName==='SELECT'?'change':'input',{bubbles:true}));
        });
    };
    for(const field of settingsInventory(schema as Parameters<typeof settingsInventory>[0],'mafft').fields) expect(label(field.label)).toBeTruthy();
    await change(label('pair method').querySelector('select')!,'oldgenafpair');
    await change(label('tree method').querySelector('select')!,'large');
    await change(label('refinement policy').querySelector('select')!,'simplehillclimbing');
    await change(label('gop').querySelector('input')!,'0');
    await change(label('focus rows').querySelector('textarea')!,'row_00000001\nrow_00000002');
    const anchors='# exact anchor text\n1 2 1 4 1 4 100\n';
    await change(div.querySelector('[aria-label="anchors contents"]')!,anchors);
    await act(async()=>[...div.querySelectorAll('button')].find(x=>x.textContent==='Start run')!.click());
    expect(mocks.submit.mock.lastCall![0].params.settings).toMatchObject({pair_method:'oldgenafpair',tree_method:'large',refinement_policy:'simplehillclimbing',gop:0,anchors,focus:false,focus_rows:'row_00000001\nrow_00000002'});
    await change(div.querySelector('[aria-label="Engine"]')!,'muscle');
    for(const field of settingsInventory(schema as Parameters<typeof settingsInventory>[0],'muscle').fields) expect(label(field.label)).toBeTruthy();
    await change(label('randseed').querySelector('input')!,'0');
    await change(div.querySelector('[aria-label="hmmin contents"]')!,'temporary exact text\n');
    await change(div.querySelector('[aria-label="hmmin contents"]')!,'');
    await act(async()=>{label('native log').querySelector<HTMLInputElement>('input')!.click();});
    await act(async()=>[...div.querySelectorAll('button')].find(x=>x.textContent==='Start run')!.click());
    expect(mocks.submit.mock.lastCall![0].params.settings).toMatchObject({hmmin:'',randseed:0,native_log:true,guidetreeout:false,randomchaintree:false});
    expect(div.textContent).toContain('BEFORE producing an alignment');
    await act(async()=>root.unmount());
});
