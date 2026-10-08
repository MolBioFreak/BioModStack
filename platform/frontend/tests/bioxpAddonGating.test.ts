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

test('BioXP frontend route is gated by resolved install feature flags', () => {
    assert.match(appSource, /useBmsFeatures/);
    assert.match(appSource, /const bmsFeatures = useBmsFeatures\(\)/);
    assert.match(appSource, /bmsFeatures\.bioxp\s*&&\s*\(/);
    assert.match(appSource, /<Route path="\/bioxp" element=\{<BioXpCockpit \/>\} \/>/);
});

test('topbar add-on controls render only when their install feature is enabled and dev tools are visible', () => {
    assert.match(layoutSource, /useBmsFeatureState/);
    assert.match(layoutSource, /isBmsFeatureVisible/);

    const utilityBlock = sourceBetween(layoutSource, 'function TopbarUtilityControls', 'function MobileTopbarTools');
    assert.match(utilityBlock, /showBioXpDevFeature && <BioXpInterlinkMenu \/>/);
    assert.match(utilityBlock, /showSystemMenus && showAssayDbDevFeature && <DbServiceMenu \/>/);
    assert.match(utilityBlock, /showSystemMenus && showStatsToolsDevFeature && <StatsToolsMenu \/>/);

    const navBlock = sourceBetween(layoutSource, 'to="/assay"', '</DragScrollRail>');
    assert.match(navBlock, /showBioXpDevFeature\s*&&\s*\(/);
    assert.match(navBlock, /to="\/bioxp"/);
    assert.match(navBlock, /BioXP Handler/);
});


test('diagnostics menu exposes button-first install add-on toggles', () => {
    const debugBlock = sourceBetween(layoutSource, 'function DebugMenu', 'interface HardwarePowerLimits');

    for (const marker of [
        'Install add-ons',
        'Show dev tools',
        'Reveals BioXP, BMS DB, and Stats Tools menus',
        'Remove BioXP',
        'Add BioXP',
        'Remove Stats Tools',
        'Add Stats Tools',
        'Remove BMS DB',
        'Add BMS DB',
        "setBmsFeature('bioxp'",
        "setBmsFeature('stats_tools'",
        "setBmsFeature('assay_db'",
        'restart required',
    ]) {
        assert.match(debugBlock, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});
