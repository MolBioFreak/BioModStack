import React,{act,useState} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({uploadFile:vi.fn()}));vi.mock('../../src/lib/api',()=>mocks);
import {BarcodeSampleSheet} from '../../src/components/ngs/BarcodeSampleSheet';
let host:HTMLDivElement,root:Root,client:QueryClient;const blocker=vi.fn();
function Harness(){const [path,setPath]=useState('');return <BarcodeSampleSheet path={path} kit="SQK-RBK114-96" onChange={setPath} onBlocker={blocker}/>;}
const button=(text:string)=>Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find(node=>node.textContent===text)!;
async function settle(){await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});}
async function click(text:string){await act(async()=>button(text).click());await settle();}
async function input(label:string,value:string){const node=host.querySelector<HTMLInputElement|HTMLTextAreaElement>(`[aria-label="${label}"]`)!;await act(async()=>{Object.getOwnPropertyDescriptor(node instanceof HTMLTextAreaElement?HTMLTextAreaElement.prototype:HTMLInputElement.prototype,'value')!.set!.call(node,value);node.dispatchEvent(new Event('input',{bubbles:true}));});await settle();}
beforeEach(async()=>{vi.resetAllMocks();mocks.uploadFile.mockImplementation(async(path:string,file:File)=>({data:{path:`${path}/${file.name}`}}));host=document.createElement('div');document.body.append(host);root=createRoot(host);client=new QueryClient();await act(async()=>root.render(<QueryClientProvider client={client}><Harness/></QueryClientProvider>));});
afterEach(()=>{act(()=>root.unmount());host.remove();client.clear();});
it('creates the pinned Dorado CSV from pasted barcode/sample rows and clears stale saved sheets after edits',async()=>{
    await click('Enter barcode samples');await input('POD5 experiment name','runA');await input('Flow cell ID','FC123');await input('Paste barcode sample rows','barcode01\tplasmid_A\nbarcode02\tplasmid_B');await click('Apply pasted rows');
    expect(blocker).toHaveBeenLastCalledWith(expect.stringContaining('Save or discard'));await click('Save sample sheet');
    await vi.waitFor(async()=>{await settle();expect(mocks.uploadFile).toHaveBeenCalledTimes(1);expect(blocker).toHaveBeenLastCalledWith('');});
    const file=mocks.uploadFile.mock.calls[0][1] as File;const text=await new Promise<string>((resolve,reject)=>{const reader=new FileReader();reader.onload=()=>resolve(String(reader.result));reader.onerror=reject;reader.readAsText(file);});
    expect(text).toBe('experiment_id,kit,flow_cell_id,position_id,barcode,alias\nrunA,SQK-RBK114-96,FC123,,barcode01,plasmid_A\nrunA,SQK-RBK114-96,FC123,,barcode02,plasmid_B\n');
    await input('Sample name 1','changed');expect(host.querySelector<HTMLInputElement>('[aria-label="Sample sheet"]')!.value).toBe('');expect(blocker).toHaveBeenLastCalledWith(expect.stringContaining('Save or discard'));
    await click('Continue with barcode numbers only');expect(blocker).toHaveBeenLastCalledWith('');
});
it('rejects duplicate barcode/sample rows without uploading a sheet',async()=>{
    await click('Enter barcode samples');await input('POD5 experiment name','runA');await input('Flow cell ID','FC123');await input('Paste barcode sample rows','barcode01,A\nbarcode01,B');await click('Apply pasted rows');await click('Save sample sheet');
    expect(host.querySelector('[role="alert"]')!.textContent).toContain('different barcode');expect(mocks.uploadFile).not.toHaveBeenCalled();
});
