import assert from 'node:assert/strict';
import test from 'node:test';

import {
    getCurrentAppPath,
    getRouterBasename,
    isAppPath,
    joinBrowserUrl,
} from '../src/runtime/navigation.js';

test('router basename resolution prefers injected values and normalizes slash shape', () => {
    assert.equal(getRouterBasename({ injectedBasename: 'bms', envBaseUrl: '/ignored/' }), '/bms/');
    assert.equal(getRouterBasename({ envBaseUrl: '/bms/' }), '/bms/');
    assert.equal(getRouterBasename({ envBaseUrl: '' }), '/');
});

test('current app path normalizes dev and container routes to the same app-relative path', () => {
    assert.equal(getCurrentAppPath('/designer', '/'), '/designer');
    assert.equal(getCurrentAppPath('/bms/designer', '/bms/'), '/designer');
    assert.equal(getCurrentAppPath('/bms', '/bms/'), '/');
    assert.equal(getCurrentAppPath('/bms/', '/bms/'), '/');
});

test('app-path detection identifies designer routes correctly under both root and /bms/', () => {
    assert.equal(isAppPath('/designer', '/designer', '/'), true);
    assert.equal(isAppPath('/designer/oligos', '/designer', '/'), true);
    assert.equal(isAppPath('/bms/designer', '/designer', '/bms/'), true);
    assert.equal(isAppPath('/bms/designer/oligos', '/designer', '/bms/'), true);
    assert.equal(isAppPath('/bms/results', '/designer', '/bms/'), false);
});

test('browser url joining preserves current app routes for dev and container frontends', () => {
    assert.equal(joinBrowserUrl('http://127.0.0.1:5173/', '/designer'), 'http://127.0.0.1:5173/designer');
    assert.equal(joinBrowserUrl('http://127.0.0.1:18080/bms/', '/results'), 'http://127.0.0.1:18080/bms/results');
    assert.equal(joinBrowserUrl('http://127.0.0.1:18080/bms/', '/'), 'http://127.0.0.1:18080/bms/');
});
