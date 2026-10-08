import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const appSource = readFileSync(resolve('src/App.tsx'), 'utf8');
const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');

function sourceBetween(source: string, start: string, end: string): string {
    const startIndex = source.indexOf(start);
    assert.notEqual(startIndex, -1, `missing start marker: ${start}`);
    const endIndex = source.indexOf(end, startIndex + start.length);
    assert.notEqual(endIndex, -1, `missing end marker: ${end}`);
    return source.slice(startIndex, endIndex);
}

test('BioXP frontend route is always declared and redirects fail-closed until the feature resolves', () => {
    assert.match(appSource, /useResolvedBmsFeatures/);
    assert.match(appSource, /resolved:\s*bmsFeaturesResolved/);
    assert.match(appSource, /<Route\s+path="\/bioxp"/);
    assert.match(appSource, /!bmsFeaturesResolved[\s\S]*<RouteLoadingFallback\s*\/>[\s\S]*bmsFeatures\.bioxp[\s\S]*<BioXpCockpit\s*\/>[\s\S]*<Navigate/);
    assert.doesNotMatch(appSource, /bmsFeatures\.bioxp\s*&&\s*\(\s*<Route path="\/bioxp"/);
});

test('topbar BioXP controls render only when enabled and dev tools are visible', () => {
    const utilityBlock = sourceBetween(layoutSource, 'function TopbarUtilityControls', 'function MobileTopbarTools');
    assert.match(utilityBlock, /useBmsFeatureState/);
    assert.match(utilityBlock, /isBmsFeatureVisible/);
    assert.match(utilityBlock, /showBioXpDevFeature && <BioXpInterlinkMenu \/>/);

    const navBlock = sourceBetween(layoutSource, 'to="/ngs"', '</DragScrollRail>');
    assert.match(navBlock, /showBioXpDevFeature\s*&&\s*\(/);
    assert.match(navBlock, /to="\/bioxp"/);
    assert.match(navBlock, /BioXP Handler/);
});

test('diagnostics menu exposes the BioXP install add-on toggle', () => {
    const debugBlock = sourceBetween(layoutSource, 'function DebugMenu', 'interface HardwarePowerLimits');
    for (const marker of [
        'Install add-ons',
        'Show dev tools',
        'Reveals BioXP development controls',
        'Remove BioXP',
        'Add BioXP',
        "setBmsFeature('bioxp'",
        'restart required',
    ]) {
        assert.match(debugBlock, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});
