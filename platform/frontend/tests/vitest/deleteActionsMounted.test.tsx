import React,{act} from 'react';
import {createRoot,type Root} from 'react-dom/client';
import {QueryClient,QueryClientProvider} from '@tanstack/react-query';
import {MemoryRouter} from 'react-router-dom';
import {afterEach,beforeEach,expect,it,vi} from 'vitest';
const mocks=vi.hoisted(()=>({remove:vi.fn(),search:vi.fn(),project:vi.fn(),archive:vi.fn()}));
vi.mock('../../src/lib/api',async original=>({...await original<any>(),deleteNucleotideSequence:mocks.remove}));
vi.mock('../../src/lib/projectManager',async original=>({...await original<any>(),searchProjects:mocks.search,getProject:mocks.project,archiveProject:mocks.archive}));
import {DeleteSequenceAction} from '../../src/components/MolBioToolkit/DeleteSequenceAction';
import {ProjectManager} from '../../src/pages/ProjectManager';
let host:HTMLDivElement,root:Root,client:QueryClient;
const done=vi.fn();
async function mount(component:React.ReactNode){await act(async()=>root.render(<QueryClientProvider client={client}><MemoryRouter initialEntries={['/projects']}>{component}</MemoryRouter></QueryClientProvider>));}
const button=(text:string)=>Array.from(host.querySelectorAll<HTMLButtonElement>('button')).find(node=>node.textContent===text)!;
async function click(text:string){await act(async()=>button(text).click());await act(async()=>{await new Promise(resolve=>setTimeout(resolve,0));});}
beforeEach(()=>{vi.resetAllMocks();host=document.createElement('div');document.body.append(host);root=createRoot(host);client=new QueryClient({defaultOptions:{queries:{retry:false},mutations:{retry:false}}});});
afterEach(()=>{act(()=>root.unmount());host.remove();client.clear();vi.restoreAllMocks();});
it('requires an explicit confirmation to delete a sequence and refreshes only after success',async()=>{
    mocks.remove.mockResolvedValue({data:{status:'deleted'}});await mount(<DeleteSequenceAction sequenceId="seq" name="QA" onDeleted={done}/>);
    await click('Delete sequence');expect(mocks.remove).not.toHaveBeenCalled();expect(host.querySelector('[role="alertdialog"]')).not.toBeNull();await click('Cancel');expect(mocks.remove).not.toHaveBeenCalled();
    await click('Delete sequence');await click('Confirm delete sequence');await vi.waitFor(()=>expect(done).toHaveBeenCalledTimes(1));expect(mocks.remove).toHaveBeenCalledWith('seq');expect(host.querySelector('[role="alertdialog"]')).toBeNull();
});
it('shows dependency conflicts without clearing the sequence or claiming deletion',async()=>{
    mocks.remove.mockRejectedValue({response:{data:{detail:'Sequence is targeted by active primers'}}});await mount(<DeleteSequenceAction sequenceId="seq" name="QA" onDeleted={done}/>);await click('Delete sequence');await click('Confirm delete sequence');
    await vi.waitFor(()=>expect(host.querySelector('[role="alert"]')?.textContent).toContain('active primers'));expect(done).not.toHaveBeenCalled();expect(host.querySelector('[role="alertdialog"]')).not.toBeNull();
});
it('deletes a Project from its list card with the current generation and recoverable archival',async()=>{
    const project={id:'p',name:'QA Project',status:'active',lifecycle_state:'active',head_generation:1,payload:{},updated_at:'2026-01-01'};
    mocks.search.mockImplementation(async()=>({items:mocks.archive.mock.calls.length?[]:[project],next_cursor:null}));mocks.project.mockResolvedValue({...project,head_generation:9});mocks.archive.mockResolvedValue({});
    const confirm=vi.spyOn(window,'confirm').mockReturnValue(false);await mount(<ProjectManager/>);
    await vi.waitFor(()=>expect(host.querySelector('[aria-label="Delete Project QA Project"]')).not.toBeNull());await click('Delete Project');expect(mocks.archive).not.toHaveBeenCalled();
    confirm.mockReturnValue(true);await click('Delete Project');await vi.waitFor(()=>expect(mocks.archive).toHaveBeenCalledWith('p',9));expect(confirm.mock.calls.at(-1)?.[0]).toContain('archived');
});
