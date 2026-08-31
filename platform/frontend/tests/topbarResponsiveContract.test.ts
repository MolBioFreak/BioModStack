import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const layoutSource = () => readFileSync(join(process.cwd(), 'src', 'components', 'Layout.tsx'), 'utf8');

test('mobile top bar collapses utility controls behind a Tools menu', () => {
    const source = layoutSource();

    for (const marker of [
        "const TOPBAR_MOBILE_QUERY = '(max-width: 767px)'",
        'function readIsCompactCordovaShell(): boolean',
        "classList.contains('bms-cordova-compact')",
        'function useIsMobileTopbar(): boolean',
        'function MobileTopbarTools',
        'data-bms-mobile-topbar-tools="true"',
        'data-bms-mobile-topbar-tools-panel="true"',
        'Open top-bar controls',
        'TOOLS',
        'fixed right-2 top-14 z-[80]',
        'max-h-[calc(100vh-4rem)]',
        'w-[min(94vw,22rem)]',
        '{!isMobileTopbar && (',
        'data-bms-topbar-utilities="true"',
    ]) {
        assert.ok(source.includes(marker), `missing mobile topbar marker: ${marker}`);
    }
});

test('Cordova compact shell uses the mobile header independent of scaled CSS viewport width', () => {
    const source = layoutSource();

    assert.ok(source.includes("classList.contains('bms-cordova-compact')"), 'Cordova compact shell should force mobile topbar mode');
    assert.ok(!source.includes('className="relative md:hidden"'), 'Tools button must not be hidden by Tailwind md rules when APK scale makes CSS width wider than 768px');
    assert.ok(source.includes("? 'order-1 flex w-full items-center justify-between gap-2'"), 'mobile identity row should stay full-width even when md breakpoints would otherwise apply');
    assert.ok(source.includes("? 'order-2 min-w-0 w-full overflow-x-auto"), 'mobile nav rail should keep full-width horizontal scrolling without md ordering overrides');
});

test('NGS primary navigation clears unrelated page query state', () => {
    const source = layoutSource();
    const ngsLabelIndex = source.indexOf('NGS Toolkit');
    const ngsLinkStart = source.lastIndexOf('<Link', ngsLabelIndex);
    const ngsLinkEnd = source.indexOf('</Link>', ngsLabelIndex);
    const ngsLink = source.slice(ngsLinkStart, ngsLinkEnd);

    assert.ok(ngsLink.includes('to="/ngs"'), 'NGS primary navigation must open the clean launcher route');
    assert.ok(!ngsLink.includes('location.search'), 'NGS primary navigation must not inherit stale job or result query state');
});

test('System Analytics remains visible in primary navigation without a debug toggle', () => {
    const source = layoutSource();
    const linkIndex = source.indexOf('to="/infra"');
    const linkStart = source.lastIndexOf('<Link', linkIndex);
    const linkEnd = source.indexOf('</Link>', linkIndex);
    const linkContext = source.slice(Math.max(0, linkStart - 120), linkEnd);
    const debugStart = source.indexOf('function DebugMenu');
    const debugEnd = source.indexOf('interface HardwarePowerLimits', debugStart);
    const debugBlock = source.slice(debugStart, debugEnd);

    assert.ok(linkIndex > 0 && linkStart > 0 && linkEnd > linkStart, 'System Analytics primary navigation link must exist');
    assert.ok(!linkContext.includes('showSystemAnalyticsTab &&'), 'System Analytics navigation must not depend on a local debug preference');
    assert.ok(!debugBlock.includes('Show System Analytics tab'), 'Debug menu must not hide an operator telemetry surface');
});

test('primary navigation rail is separate from utility menus so dropdowns are not clipped', () => {
    const source = layoutSource();
    const railStart = source.indexOf('<DragScrollRail');
    const railEnd = source.indexOf('</DragScrollRail>', railStart);
    const utilityIndex = source.indexOf('data-bms-topbar-utilities="true"');
    const mobileToolsIndex = source.indexOf('data-bms-mobile-topbar-tools="true"');

    assert.ok(railStart > 0, 'primary nav DragScrollRail should exist');
    assert.ok(railEnd > railStart, 'primary nav DragScrollRail should close');
    assert.ok(utilityIndex > railEnd, 'desktop utility controls should render outside the primary scroll rail');
    assert.ok(mobileToolsIndex > 0 && mobileToolsIndex < railStart, 'mobile Tools button should sit in the identity row before the nav rail');

    const railBlock = source.slice(railStart, railEnd);
    assert.ok(!railBlock.includes('data-bms-topbar-utilities="true"'), 'utility controls must not be nested inside the overflow-x nav rail');
    assert.ok(!railBlock.includes('ThemeSelector'), 'theme/debug/service menus must stay out of the nav scroll rail');
});

test('top-bar utility menus stay compact and action-first', () => {
    const source = layoutSource();

    for (const marker of [
        'Surface + API',
        'Local runtime switch + start',
        'CoolerControl active.',
        'Per-GPU channel control.',
        'Default GPU for MSA controls.',
        'POWER LIMITS',
        'MSA SERVER',
    ]) {
        assert.ok(source.includes(marker), `missing compact topbar copy marker: ${marker}`);
    }

    for (const stale of [
        'CoolerControl device backend active.',
        'Fan mode and target changes apply per GPU through CoolerControl channel settings.',
        'Sets the default GPU for MSA server status, start, and stop actions. Leave on auto to use scheduler preference.',
        'Open diagnostics to collect surface details and explain the current app/runtime configuration.',
    ]) {
        assert.ok(!source.includes(stale), `stale explainer text still present: ${stale}`);
    }
});
