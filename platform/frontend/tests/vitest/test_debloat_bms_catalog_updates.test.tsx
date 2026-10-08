import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { applyBioXpCatalogUpdate, composeBioXpCatalog, useBioXpOperatorControlCatalog } from '../../src/lib/bioxpClient';

const path = process.env.BIOXP_CATALOG_UPDATE_EXPORT;
const actual = path ? JSON.parse(readFileSync(path, 'utf8')) : null;

it.skipIf(!actual).each(['idle', 'changing', 'arrivals25_idle', 'multiple_clients:0', 'multiple_clients:1', 'multiple_drafts:0', 'multiple_drafts:1'])('receives exact native %s updates with promoted baselines', async scenario => {
    const [mode, clientIndex] = scenario.split(':');
    const clientId = Number(clientIndex ?? 0);
    vi.useFakeTimers();
    const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    const original = api.defaults.adapter;
    let count = 0, metadataCount = 0, bytes = 0;
    let observed: ReturnType<typeof useBioXpOperatorControlCatalog>;
    const window = actual.windows[mode];
    const events = window.events.filter((e: {client:number}) => e.client === clientId);
    const cold = window.colds[clientId];
    let draft = mode.startsWith('multiple_') ? clientId * 500 : undefined;
    api.defaults.adapter = async config => {
        expect(config.url).toBe('/api/bioxp/operator-controls/catalog');
        let body;
        if (config.params.view === 'metadata') { metadataCount++; body = actual.metadata; }
        else {
            expect(config.params.assessment_base).toBe(count === 0 ? '' : events[count-1].request[0]);
            expect(config.params.canonical_assessment_base).toBe(count === 0 ? '' : events[count-1].request[1]);
            expect(config.params.z_target_steps).toBe(draft);
            body = count === 0 ? cold : events[count - 1].response;
            if (count > 0) bytes += new TextEncoder().encode(JSON.stringify(body)).length;
            count++;
        }
        return {data:structuredClone(body), status:200, statusText:'OK', headers:{}, config};
    };
    function Consumer() {
        observed = useBioXpOperatorControlCatalog(7, true, 'ready', draft);
        return <div>{observed.status}{observed.error?.message}{observed.data?.actions[0]?.enabled ? 'enabled' : 'disabled'}</div>;
    }
    const tick = async (ms:number) => act(async()=>{await vi.advanceTimersByTimeAsync(ms);});
    try {
        await act(async()=>root.render(<QueryClientProvider client={client}><Consumer/></QueryClientProvider>));
        await tick(20);
        expect(observed!.isSuccess).toBe(true);
        expect(count).toBe(1);
        for (let i=0; i<events.length; i++) {
            if (mode === 'multiple_drafts') {
                // Draft changes dispose the former query key. The shared
                // QueryClient snapshot must survive without a full resync.
                await tick(4900);
                draft = events[i].draft;
                await act(async()=>root.render(<QueryClientProvider client={client}><Consumer/></QueryClientProvider>));
                await tick(20);
            } else await tick(5000);
            expect(observed!.error).toBeNull();
            expect(observed!.data).toMatchObject(composeBioXpCatalog(actual.metadata, events[i].expected));
            expect(observed!.data?.dashboard).toEqual(events[i].expected.dashboard);
            expect(observed!.data?.canonical.dashboard).toEqual(events[i].expected.canonical.dashboard);
        }
        expect(count).toBe(events.length + 1);
        expect(metadataCount).toBe(1);
        const size = (body:unknown) => new TextEncoder().encode(JSON.stringify(body)).length;
        expect(bytes).toBe(events.reduce((sum:number,e:{response:unknown})=>sum+size(e.response),0));
        expect(bytes).toBeLessThanOrEqual(events.reduce((sum:number,e:{expected:unknown})=>sum+size(e.expected),0) * .05);
    } finally {
        await act(async()=>root.unmount()); client.clear(); host.remove(); api.defaults.adapter=original; vi.useRealTimers();
    }
});

it('preserves false/null/omission and uses immutable baseline references for array shifts', () => {
    const base = {revision:'r', body:{truth:true, absent:null, rows:[{command_id:'a', ok:false}, {command_id:'b', ok:null}]}};
    const result=applyBioXpCatalogUpdate({catalog_view:'assessment', assessment_revision:'r', assessment_changes:[
        [['truth'],null], [['absent']], [['rows',0],{command_id:'new',ok:false}],
        [['rows',1],null,['rows',0]], [['rows',2],null,['rows',1]],
    ]}, base);
    expect(result.body).toEqual({truth:null, rows:[{command_id:'new',ok:false},{command_id:'a',ok:false},{command_id:'b',ok:null}]});
    expect(base.body.rows).toHaveLength(2);
    expect(base.body.absent).toBeNull();
    expect(()=>applyBioXpCatalogUpdate({catalog_view:'assessment',assessment_revision:'other',assessment_changes:[]},base)).toThrow('baseline missing');
});

it('promotes the reconstructed immutable result, not another client\'s draft overlay', () => {
    const previous = {revision:'old', body:{time:1, draft:null, rows:[{command_id:'a', ok:false}, {command_id:'b', ok:null}]}};
    const next = applyBioXpCatalogUpdate({catalog_view:'assessment', assessment_source_revision:'old', assessment_revision:'new',
        assessment_changes: [[['rows',0],null,['rows',1]], [['rows',1],null,['rows',0]]],
        assessment_overlay: [[['time'],2], [['draft'],false]],
    }, previous);
    expect(next.base.revision).toBe('new');
    expect(next.base.body).toEqual({time:1,draft:null,rows:[{command_id:'b',ok:null},{command_id:'a',ok:false}]});
    expect(next.body).toEqual({...next.base.body,time:2,draft:false});
    const idle = applyBioXpCatalogUpdate({catalog_view:'assessment', assessment_source_revision:'new', assessment_revision:'new',
        assessment_changes:[], assessment_overlay:[[['time'],3], [['draft']]]}, next.base);
    expect(idle.base).toEqual(next.base);
    expect(idle.body).toEqual({time:3,rows:next.base.body.rows});
    expect(previous.body.rows[0].command_id).toBe('a');
});
