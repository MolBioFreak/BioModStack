import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const layoutSource = () => readFileSync(join(process.cwd(), 'src', 'components', 'Layout.tsx'), 'utf8');
const appSource = () => readFileSync(join(process.cwd(), 'src', 'App.tsx'), 'utf8');
const dashboardTelemetrySource = () => readFileSync(join(process.cwd(), 'src', 'components', 'dashboard', 'DashboardTelemetry.tsx'), 'utf8');

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

test('Dashboard owns local, Vast, and combined analytics without a separate navigation tab', () => {
    const layout = layoutSource();
    const app = appSource();
    const telemetry = dashboardTelemetrySource();

    assert.ok(!layout.includes('to="/infra"'), 'primary navigation must not expose a separate System Analytics tab');
    assert.ok(!layout.includes('System Analytics'), 'obsolete System Analytics navigation copy must be removed');
    assert.ok(app.includes('<Route path="/infra" element={<Navigate replace to="/" />} />'), 'historical analytics URLs must redirect to Dashboard');
    assert.ok(!app.includes('InfraMonitorPage'), 'the separate analytics page must not remain in the application bundle');
    assert.ok(telemetry.includes('aria-label="Telemetry source"'), 'Dashboard analytics must expose source tabs');
    assert.ok(telemetry.includes('Vast · {activeVastLabel}'), 'Dashboard analytics must identify the active Vast instance');
    assert.ok(telemetry.includes('Combined'), 'Dashboard analytics must expose a combined active-source view');
    assert.ok(telemetry.includes('data-bms-telemetry-combined="true"'), 'combined view must mount local and remote telemetry together');
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
