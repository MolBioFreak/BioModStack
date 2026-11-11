import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import { MsaHubView, type MsaHubViewProps, type MsaLocalServerSectionProps } from '../src/components/MsaHubView';
import {
    DEFAULT_MSA_LOCAL_SERVER_SETTINGS,
    msaHubIndicatorClass,
    msaProviderKeyStatus,
    normalizeMsaHubProviders,
} from '../src/components/msaHubTypes';

const SECRET = 'ns-test-key-0123456789abcdef';

// A response that wrongly carried a key must still not reach the markup.
const providers = normalizeMsaHubProviders({
    items: [
        { id: 'colabfold_api', label: 'ColabFold API', needs_key: false, configured: false, source: null,
          endpoint_configurable: false, ready: true, message: 'Ready' },
        { id: 'neurosnap_api', label: 'Neurosnap API', needs_key: true, configured: true, source: 'saved',
          endpoint_configurable: false, ready: true, message: 'Ready', api_key: SECRET, key: SECRET },
    ],
});

const noop = () => undefined;

const local: MsaLocalServerSectionProps = {
    settings: { ...DEFAULT_MSA_LOCAL_SERVER_SETTINGS },
    loading: null,
    availableGpus: [{ index: 0, name: 'Fixture GPU', memory_total_mb: 24576 }],
    summary: 'Running: no',
    onRefresh: noop,
    onStart: noop,
    onStop: noop,
    onSaveSettings: noop,
    onIdleMinutesChange: noop,
};

const base: MsaHubViewProps = {
    isOpen: true,
    onToggle: noop,
    onClose: noop,
    indicatorClass: 'bg-blue-400',
    providers,
    providersLoaded: true,
    busyProvider: null,
    error: null,
    onSaveCredential: noop,
    onClearCredential: noop,
    local: null,
};

const render = (props: Partial<MsaHubViewProps> = {}) => renderToStaticMarkup(<MsaHubView {...base} {...props} />);

test('remote-only hub lists providers and shows no local controls', () => {
    const markup = render();
    assert.match(markup, />\s*MSA\s*<\/button>/);
    assert.ok(!markup.includes('MSA SERVER'));
    assert.ok(markup.includes('Remote providers'));
    assert.ok(markup.includes('ColabFold API'));
    assert.ok(markup.includes('No key needed'));
    assert.ok(markup.includes('Neurosnap API'));
    assert.ok(markup.includes('Key saved'));
    assert.equal((markup.match(/type="password"/g) ?? []).length, 1);
    assert.ok(markup.includes('>Save</button>'));
    assert.ok(markup.includes('>Clear</button>'));
    for (const absent of ['Local MSA server', 'Start Server', 'Stop Server', 'Pinned GPU', '<textarea']) {
        assert.ok(!markup.includes(absent), `unexpected local control: ${absent}`);
    }
});

test('remote + local hub adds the existing local server controls', () => {
    const markup = render({ local });
    assert.ok(markup.includes('Remote providers'));
    for (const present of ['Local MSA server', 'Start Server', 'Stop Server', 'Pinned GPU', 'Fixture GPU',
        'Auto-stop when idle', 'Start EnvDB server with UniRef', 'Running: no']) {
        assert.ok(markup.includes(present), `missing local control: ${present}`);
    }
});

test('no key value appears in the markup and the key input is never pre-filled', () => {
    for (const markup of [render(), render({ local })]) {
        assert.ok(!markup.includes(SECRET));
        const input = markup.match(/<input[^>]*type="password"[^>]*>/)?.[0] ?? '';
        assert.ok(input.length > 0);
        assert.ok(!/\svalue=/.test(input));
    }
    assert.ok(!JSON.stringify(providers).includes(SECRET));
});

test('environment keys are shown as such and cannot be replaced or cleared here', () => {
    const fromEnvironment = providers.map((provider) => provider.id === 'neurosnap_api'
        ? { ...provider, source: 'environment' as const } : provider);
    const markup = render({ providers: fromEnvironment });
    assert.ok(markup.includes('Set by environment'));
    assert.ok(!markup.includes('Key saved'));
    assert.equal((markup.match(/disabled=""/g) ?? []).length, 3);
});

test('closed hub renders only the button; an unconfigured provider shows its message', () => {
    assert.ok(!render({ isOpen: false }).includes('Remote providers'));
    const missing = normalizeMsaHubProviders({ items: [{ id: 'neurosnap_api', label: 'Neurosnap API', needs_key: true,
        configured: false, source: null, ready: false, message: 'Save a Neurosnap API key in the MSA hub' }] });
    assert.equal(msaProviderKeyStatus(missing[0]), 'No key');
    assert.ok(render({ providers: missing }).includes('Save a Neurosnap API key in the MSA hub'));
    assert.deepEqual(normalizeMsaHubProviders(null), []);
    assert.equal(msaHubIndicatorClass(missing, null), 'bg-slate-600');
    assert.equal(msaHubIndicatorClass(providers, null), 'bg-blue-400');
    assert.equal(msaHubIndicatorClass(providers, { running: true, allRunning: false }), 'bg-amber-400');
});
