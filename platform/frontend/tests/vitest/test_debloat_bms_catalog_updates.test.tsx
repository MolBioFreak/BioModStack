import React, { act } from 'react';
import { readFileSync } from 'node:fs';
import { createRoot } from 'react-dom/client';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { expect, it, vi } from 'vitest';
import { api } from '../../src/lib/api';
import { applyBioXpCatalogUpdate, composeBioXpCatalog, useBioXpOperatorControlCatalog } from '../../src/lib/bioxpClient';

const path = process.env.BIOXP_CATALOG_UPDATE_EXPORT;
const actual = path ? JSON.parse(readFileSync(path, 'utf8')) : null;

it.skipIf(!actual).each(['idle', 'changing'])('receives exact native %s updates for a full mounted minute at five seconds', async mode => {
    vi.useFakeTimers();
    const client = new QueryClient({defaultOptions:{queries:{retry:false}}});
    const host = document.createElement('div'); document.body.append(host);
    const root = createRoot(host);
    const original = api.defaults.adapter;
    let count = 0, metadataCount = 0, bytes = 0;
    let observed: ReturnType<typeof useBioXpOperatorControlCatalog>;
    const window = actual.windows[mode];
    api.defaults.adapter = async config => {
        expect(config.url).toBe('/api/bioxp/operator-controls/catalog');
        let body;
        if (config.params.view === 'metadata') { metadataCount++; body = actual.metadata; }
        else {
            expect(config.params.assessment_base).toBe(count === 0 ? '' : actual.cold.assessment_revision);
            expect(config.params.canonical_assessment_base).toBe(count === 0 ? '' : actual.cold.canonical.assessment_revision);
            body = count === 0 ? actual.cold : window.responses[count - 1];
            if (count > 0) bytes += new TextEncoder().encode(JSON.stringify(body)).length;
            count++;
        }
        return {data:structuredClone(body), status:200, statusText:'OK', headers:{}, config};
    };
    function Consumer() {
        observed = useBioXpOperatorControlCatalog(7, true, 'ready', 65000);
        return <div>{observed.status}{observed.error?.message}{observed.data?.actions[0]?.enabled ? 'enabled' : 'disabled'}</div>;
    }
    const tick = async (ms:number) => act(async()=>{await vi.advanceTimersByTimeAsync(ms);});
    try {
        await act(async()=>root.render(<QueryClientProvider client={client}><Consumer/></QueryClientProvider>));
        await tick(20);
        expect(observed!.isSuccess).toBe(true);
        expect(count).toBe(1);
        for (let i=0; i<12; i++) {
            await tick(5000);
            expect(observed!.error).toBeNull();
            expect(observed!.data).toMatchObject(composeBioXpCatalog(actual.metadata, window.expected[i]));
            expect(observed!.data?.dashboard).toEqual(window.expected[i].dashboard);
            expect(observed!.data?.canonical.dashboard).toEqual(window.expected[i].canonical.dashboard);
        }
        expect(count).toBe(13);
        expect(metadataCount).toBe(1);
        expect(bytes).toBe(window.new_bytes);
        expect(bytes).toBeLessThanOrEqual(window.old_bytes * .05);
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
