import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const api = vi.hoisted(() => ({ fetchNucleotideSequences: vi.fn(), fetchPrimers: vi.fn() }));
vi.mock('../../src/lib/api', () => api);
import { MolecularInputModal } from '../../src/components/MolBioToolkit/MolecularInputModal';
import { useSequenceHistory } from '../../src/components/MolBioToolkit/hooks/useSequenceHistory';
import { EMPTY_SEQUENCE } from '../../src/components/MolBioToolkit/sequenceViewerConstants';
let root: Root;
let host: HTMLDivElement;
function deferred<T>() { let resolve!: (value: T) => void; let reject!: (error: Error) => void; const promise = new Promise<T>((r, j) => { resolve=r; reject=j; }); return {promise,resolve,reject}; }
beforeEach(() => { vi.useFakeTimers(); host=document.createElement('div'); document.body.append(host); root=createRoot(host); api.fetchNucleotideSequences.mockReset().mockResolvedValue({data:[]}); api.fetchPrimers.mockReset().mockResolvedValue({data:[]}); });
afterEach(async () => { await act(async () => root.unmount()); host.remove(); vi.useRealTimers(); });
const props = {onClose:vi.fn(),onSelectSequence:vi.fn(),onImportFile:vi.fn(),onCreateSequence:vi.fn(),onLoadDemo:vi.fn(),onAddPrimerToCurrentSequence:vi.fn(),onOpenPrimerAsConstruct:vi.fn(),hasOpenSequence:false,demos:[]};
async function render(open=true, onDemoIntent=vi.fn()) { await act(async () => root.render(<MolecularInputModal {...props} isOpen={open} onDemoIntent={onDemoIntent}/>)); }
async function button(text:string) { const target=[...host.querySelectorAll('button')].find(b=>b.textContent?.includes(text)); expect(target).toBeTruthy(); await act(async()=>target!.click()); }
async function timers() { await act(async()=> { await vi.advanceTimersByTimeAsync(300); }); }
it('closed/reopened library discards older completion and retains current rows',async()=>{
 const old=deferred<{data:unknown[]}>(); const current=deferred<{data:unknown[]}>();
 api.fetchNucleotideSequences.mockImplementationOnce(()=>old.promise).mockImplementationOnce(()=>current.promise);
 await render(); await timers(); await render(false); await render(); await timers();
 await act(async()=>current.resolve({data:[{id:'new',name:'New current construct',length:4,gc_percent:50,feature_count:0,sequence_type:'dna',is_circular:false}]}));
 expect(host.textContent).toContain('New current construct');
 await act(async()=>old.reject(new Error('stale failure')));
 expect(host.textContent).toContain('New current construct'); expect(host.textContent).not.toContain('stale failure');
});
it('primer library pages bounded records and demos load only on selected intent',async()=>{
 const onDemo=vi.fn(); const rows=Array.from({length:50},(_,i)=>({id:String(i),name:`Primer ${i}`,sequence:'ACGT',gc_percent:50,length:4,is_favorite:false}));
 api.fetchPrimers.mockResolvedValueOnce({data:rows}).mockResolvedValueOnce({data:[{...rows[0],id:'last',name:'Last primer'}]});
 await render(true,onDemo); await timers(); expect(onDemo).not.toHaveBeenCalled();
 await button('Primers / Oligos'); await timers();
 expect(api.fetchPrimers.mock.calls[0][0]).toMatchObject({limit:50,offset:0});
 await button('Load more primers'); await timers();
 expect(api.fetchPrimers.mock.calls[1][0]).toMatchObject({limit:50,offset:50});
 expect(host.textContent).toContain('Primer 0'); expect(host.textContent).toContain('Last primer');
 expect(host.textContent).not.toContain('Load more primers');
 await button('Demo Plasmids'); expect(onDemo).toHaveBeenCalledTimes(1);
});
it('real hook composes same-act updates and a batch is undone atomically',async()=>{
 let history!:ReturnType<typeof useSequenceHistory>;
 function Harness(){history=useSequenceHistory(EMPTY_SEQUENCE);return <div>{history.sequenceData.name}</div>}
 await act(async()=>root.render(<Harness/>));
 await act(async()=>{history.set(current=>({...current,name:'one'}));history.set(current=>({...current,name:current.name+' two'}));});
 expect(history.sequenceData.name).toBe('one two');
 await act(async()=>history.set(current=>({...current,features:['a','b'].map(id=>({id,name:id,type:'misc_feature',start:0,end:1,strand:1}))})));
 expect(history.sequenceData.features).toHaveLength(2);
 await act(async()=>history.undo()); expect(history.sequenceData.features).toHaveLength(0);expect(history.sequenceData.name).toBe('one two');
});
