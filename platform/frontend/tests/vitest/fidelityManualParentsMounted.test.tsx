// Focused receiving journeys ported from the read-only management audit.
// Transport is inert; serialized cold reopen is not a live database assertion.
import React,{act} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,beforeEach,it,expect,vi} from 'vitest';
import {writeFileSync} from 'node:fs';
import {execFileSync} from 'node:child_process';
import {api} from '../../src/lib/api';
import {JobSubmission} from '../../src/components/JobSubmission';
const E=process.env.BMS_FRONTEND_EVIDENCE!;
const catalogs=JSON.parse(execFileSync(process.env.BMS_TEST_PYTHON || 'python3', ['-c', "import json,yaml; from pathlib import Path; p=Path('../api/config/models'); print(json.dumps([yaml.safe_load((p/(x+'.yaml')).read_text()) for x in ['fampnn','fampnn_child','proteinmpnn','protenix','boltz2','esmfold2','caliby_binder']]))"], { encoding: 'utf8' }));
vi.mock('../../src/components/MolstarViewerImpl',()=>({default:()=>null}));
let root:Root|undefined,client:QueryClient,saved:any[],sent:any[];
beforeEach(()=>{
 saved=[];sent=[];
 vi.spyOn(api,'get').mockImplementation(async(url:string)=>{
 if(url==='/api/models')return {data:catalogs};
 if(url.startsWith('/api/models/'))return {data:catalogs.find((x:any)=>url===`/api/models/${x.id}`)??{workflows:{}}};
 return {data:[]};
 });
 vi.spyOn(api,'post').mockImplementation(async(url:string,body:any)=>{
 if(url==='/api/jobs'){sent.push(structuredClone(body));return {data:{id:'inert-only'}};}
 if(url.includes('user-templates')){saved.push(structuredClone(body));return {data:body};}
 throw Error('Blocked '+url);
 });
 vi.stubGlobal('fetch',vi.fn(async()=>({ok:false,status:404})));
});
async function settle(){await act(async()=>{await new Promise(r=>setTimeout(r,35))});}
async function mount(model:string,params:any){localStorage.setItem('clonedJobData',JSON.stringify({name:'audit',model_id:model,mode:model==='fampnn_child'?'sequence_design':'design',params}));client=new QueryClient({defaultOptions:{queries:{retry:false}}});const host=document.createElement('div');document.body.append(host);root=createRoot(host);await act(async()=>root!.render(<QueryClientProvider client={client}><MemoryRouter><JobSubmission/></MemoryRouter></QueryClientProvider>));await settle();await settle();}
async function close(){if(root)await act(async()=>root!.unmount());root=undefined;client?.clear();document.body.replaceChildren();localStorage.clear();}
afterEach(async()=>{await close();vi.restoreAllMocks();vi.unstubAllGlobals()});
async function click(text:string){const n=[...document.querySelectorAll('button')].find(x=>x.textContent?.trim()===text);expect(n,text).toBeTruthy();await act(async()=>n!.click());await settle();}
async function save(name:string){await click('Template Manager');const n=document.querySelector<HTMLInputElement>('input[placeholder="e.g., My Boltz Config"]')!;expect(n).toBeTruthy();await act(async()=>{Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value')!.set!.call(n,name);n.dispatchEvent(new Event('input',{bubbles:true}));});await click('Save Template');return saved.at(-1);}
const fixtures:any={fampnn:{input_pdb:'inputs/nonfirst.pdb',design_chain:'a',target_chain:'1',fixed_positions:'a:10B,1:5,A:20-30',fampnn_seed:19,seqs_per_design:3,fampnn_temperature:0.23,fampnn_psce_threshold:0,fampnn_mutation_min_log_odds_delta:0,fampnn_fix_target_sidechains:true,fampnn_exclude_cys:false,fampnn_analysis_overrides:{summary:[],mutation:[{chain_id:'a',author_number:10,insertion_code:'B'}]}},proteinmpnn:{input_pdb:'inputs/nonfirst.pdb',design_chain:'a',target_chain:'1',binder_chains:'a',target_chains:'1',fixed_positions:'a:10B,1:5,A:20-30',seqs_per_design:7,mpnn_temperature:0.31,mpnn_relax_convergence_score:0,mpnn_backbone_noise:0,mpnn_omitAAs:'CX'},fampnn_child:{pdb_paths:['inputs/nonfirst.pdb'],fampnn_seed:19,fampnn_temperature:0.23,seqs_per_design:3,fampnn_analysis_overrides:{mutation:[]}}};
it.each(Object.keys(fixtures))('audit manual %s clone actual manager saves complete scientific params and cold reopens',async model=>{
 const original=fixtures[model];await mount(model,original);const first=await save(model+' first');if(E)writeFileSync(E+'/manual-'+model+'.json',JSON.stringify({original,saved:first},null,2));expect(first).toBeTruthy();
 for(const [key,value] of Object.entries(original))expect(first.params[key],key).toEqual(value);
 await close();const reopened=JSON.parse(JSON.stringify(first));await mount(model,reopened.params);const second=await save(model+' second');expect(second.params).toEqual(first.params);
 if(E)writeFileSync(E+'/manual-'+model+'-roundtrip.json',JSON.stringify({first,second,serialized_reopen:true},null,2));
});
