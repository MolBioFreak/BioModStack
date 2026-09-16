import React, {act} from 'react';
import {createRoot} from 'react-dom/client';
import {afterEach, expect, test, vi} from 'vitest';
import {webcrypto} from 'node:crypto';
import {readFileSync} from 'node:fs';
const wirePath=process.env.BMS_STRUCTURE_BYTES_WIRE;
if(!wirePath)throw new Error('BMS_STRUCTURE_BYTES_WIRE actual ASGI download required');
const wire=JSON.parse(readFileSync(wirePath,'utf8'));
import MolstarViewer from '../../src/components/MolstarViewerImpl';
const parsed = vi.hoisted(()=>[] as string[]);
const transport = vi.hoisted(()=>({served:''}));
const ownerStats = vi.hoisted(() => ({ initialized: 0, disposed: 0, live: 0, maximumLive: 0, representationPresets: [] as string[] }));
vi.mock('../../src/structureViewer/runtime/createDirectMolstarEngineOwner', () => ({
    createDirectMolstarEngineOwner: () => {
        let disposed = false;
        return {
            initialize: async () => {
                ownerStats.initialized += 1;
                ownerStats.live += 1;
                ownerStats.maximumLive = Math.max(ownerStats.maximumLive, ownerStats.live);
                return {
                    status: 'ok', generation: ownerStats.initialized,
                    plugin: {
                        commands: { dispatch: async () => undefined },
                        canvas3d: { setProps: () => undefined, camera: { setState: () => undefined }, requestCameraReset: () => undefined },
                        managers: {
                            interactivity: { setProps: () => undefined },
                            structure: { hierarchy: { current: { structures: [] } } },
                        },
                        behaviors: { interaction: { click: { subscribe: () => ({ unsubscribe: () => undefined }) } } },
                        builders: {
                            data: { download: async () => { parsed.push(transport.served); return {}; }, rawData: async (value: {data:string}) => { parsed.push(value.data); return {}; } },
                            structure: {
                                parseTrajectory: async () => ({}),
                                hierarchy: {
                                    applyPreset: async (_trajectory: unknown, _preset: string, params: { representationPreset?: string }) => {
                                        if (params.representationPreset) ownerStats.representationPresets.push(params.representationPreset);
                                    },
                                },
                            },
                        },
                        clear: async () => undefined,
                    },
                };
            },
            dispose: () => {
                if (!disposed) {
                    disposed = true;
                    ownerStats.disposed += 1;
                    ownerStats.live -= 1;
                }
            },
        };
    },
}));


let root:ReturnType<typeof createRoot>;
afterEach(async()=>{await act(async()=>root?.unmount());document.body.replaceChildren();vi.unstubAllGlobals();parsed.length=0;});
test('mounted real controller and loader reject changed bytes with cached scientific presentation',async()=>{
    vi.stubGlobal('crypto',webcrypto);
    const bytes=wire.structure;
    const hash=wire.metric.document.contentSha256;
    transport.served=bytes;
    vi.stubGlobal('fetch',vi.fn(async()=>({ok:true,arrayBuffer:async()=>new TextEncoder().encode(transport.served).buffer})));
    const host=document.createElement('div');document.body.append(host);root=createRoot(host);
    const states:string[]=[];
    const loaded=(state:string)=>states.push(state);
    const render=async(url:string)=>{await act(async()=>{root.render(<MolstarViewer structureUrl={url} structureDocumentId="primary" structureContentSha256={hash} scenePresentation={{colorQueries:[{documentId:"primary",authAsymId:"H",startAuthSeqId:100,endAuthSeqId:100,color:0xff0000}]}} onLoadStateChange={loaded}/>);});
        await act(async()=>{await new Promise(resolve=>setTimeout(resolve,60));});};
    await render('/candidate/pdb');
    expect(parsed).toEqual([bytes]);expect(states).toContain('loaded');
    transport.served='changed coordinates';
    await render('/candidate/pdb?reload=1');
    expect(parsed).toEqual([bytes]);expect(states.at(-1)).toBe('failed');
    expect(host.textContent).toContain('content hash mismatch');
});
