import React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { afterEach, expect, it } from 'vitest';
import { api } from '../../src/lib/api';
import { MsaProviderReadiness } from '../../src/components/MsaProviderReadiness';

// Matches the live /api/msa/providers shape. The bare /msa/providers URL is
// Vite's HTML fallback, not an API whose baseURL magically supplies /api.
const setup = {
    default_provider: 'colabfold_api', local_search_enabled: false,
    submission_location: 'controller', cache_identity: 'provider-query-effective-settings',
    providers: {
        colabfold_api: { configured: false, credential_configured: null, authentication: 'not_required',
            live_acceptance: 'not_checked_by_setup', blockers: ['Configure controller settings'] },
        neurosnap_api: { configured: true, credential_configured: true, authentication: 'not_checked',
            live_acceptance: 'not_checked_by_setup', blockers: [] },
    },
};
const originalAdapter = api.defaults.adapter;
let renderer: ReactTestRenderer | undefined;
let paths: string[];
async function mount(data: unknown, fail = false) {
    paths = [];
    api.defaults.adapter = async config => {
        paths.push(config.url!);
        if (fail) throw new Error('offline readiness unavailable');
        return { data: config.url === '/api/msa/providers' ? data : '<!doctype html><div id="root"></div>',
            status: 200, statusText: 'OK', headers: {}, config };
    };
    await act(async () => { renderer = create(<MsaProviderReadiness provider="neurosnap_api" />); });
    await act(async () => { await new Promise(r => setTimeout(r, 0)); });
}
const output = () => JSON.stringify(renderer!.toJSON());
afterEach(async () => { if (renderer) await act(async () => renderer!.unmount()); renderer = undefined; api.defaults.adapter = originalAdapter; });

it('uses the real API path and renders the actual provider response without claiming authenticated science', async () => {
    await mount(setup);
    expect(paths).toEqual(['/api/msa/providers']);
    expect(output()).toContain('configured (not proof of live acceptance)');
    expect(output()).toContain('not_checked');
    expect(output()).toContain('Server credential: ');
    expect(output()).not.toContain('missing');
});
it.each(['<!doctype html><html></html>', {}, null, { providers: {} }])('invalid readiness %j remains an unavailable status, not a crashed form', async data => {
    await mount(data);
    expect(output()).toContain('Provider setup could not be read');
    expect(output()).toContain('Saved settings are unchanged');
});
it('missing optional observations do not invent missing credentials or crash on absent blockers', async () => {
    await mount({ providers: { neurosnap_api: { configured: true } } });
    expect(output()).toContain('configured (not proof of live acceptance)');
    expect(output()).toContain('not reported');
    expect(output()).not.toContain('Server credential');
});
it('one absent provider does not hide another provider and selection needs no new request', async () => {
    await mount({ providers: { neurosnap_api: setup.providers.neurosnap_api } });
    expect(output()).toContain('configured (not proof of live acceptance)');
    await act(async () => renderer!.update(<MsaProviderReadiness provider="colabfold_api" />));
    expect(output()).toContain('Provider setup could not be read');
    expect(paths).toHaveLength(1);
});
it('transport failure leaves admission authority on the server and does not retry or submit', async () => {
    await mount(null, true);
    expect(output()).toContain('admission will check configuration');
    expect(paths).toEqual(['/api/msa/providers']);
});
