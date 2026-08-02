import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const layout = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const panel = readFileSync(resolve('src/components/BioXpInterlinkControlPanel.tsx'), 'utf8');
const client = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');

test('BioXP topbar status menu remains install-feature gated', () => {
    assert.match(layout, /showBioXpDevFeature && <BioXpInterlinkMenu \/>/);
    assert.match(panel, /data-bms-bioxp-interlink-menu="true"/);
    assert.match(panel, /Status/);
    assert.match(panel, /Profile/);
    assert.match(panel, /Connect/);
    assert.match(panel, /Disconnect/);
    assert.match(panel, /Probe/);
    assert.doesNotMatch(panel, /operatorToken|type="password"/);
    assert.match(panel, /connect\.mutate\(undefined\)/);
});

test('frontend client contains only compact and typed lifecycle BioXP endpoints', () => {
    const allowed = [
        '/api/bioxp/status',
        '/api/bioxp/profile',
        '/api/bioxp/connection/connect',
        '/api/bioxp/connection/disconnect',
        '/api/bioxp/connection/probe',
        '/api/bioxp/logs',
        '/api/bioxp/protocols/compile',
        '/api/bioxp/protocols/submit',
        '/api/bioxp/jobs',
        '/api/bioxp/commands',
        '/api/bioxp/emergency-stop',
        '/api/bioxp/oem-full-lifecycle',
    ];
    const actual = [...client.matchAll(/['`]\/api\/bioxp\/[^'`${}]*['`]/g)]
        .map((match) => match[0].slice(1, -1));
    assert.ok(actual.length > 0);
    for (const endpoint of actual) {
        assert.ok(allowed.some((prefix) => endpoint === prefix || endpoint.startsWith(`${prefix}/`)), endpoint);
    }
    for (const forbidden of ['/motion/', '/liquid/', '/camera/', '/vision/', '/diagnostics/', '/oem/', '/runtime/']) {
        assert.doesNotMatch(client, new RegExp(forbidden.replaceAll('/', '\\/')));
    }
});

test('frontend types and hooks honor compact backend response envelopes', () => {
    for (const marker of [
        'BioXpJobListResponse',
        'BioXpProtocolSubmissionResponse',
        'compiled_hash',
        'validation_status',
        'blockers',
        'response.data.jobs',
    ]) {
        assert.ok(client.includes(marker), marker);
    }
    assert.doesNotMatch(client, /\bdigest:\s*string/);
    assert.doesNotMatch(client, /\bstatus:\s*'validated_offline'/);
    assert.doesNotMatch(client, /\bconstraints:\s*string\[\]/);
});
