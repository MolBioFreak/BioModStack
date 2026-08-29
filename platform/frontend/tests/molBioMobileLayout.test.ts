import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';

import {
    activateMobileMolBioSequence,
    detectMolBioCordovaShell,
    detectMolBioPrimaryCoarsePointer,
    resolveMolBioMobileBackAction,
    resolveMolBioMobileSequenceIntent,
    shouldUseMolBioMobileLayout,
} from '../src/components/MolBioToolkit/utils/mobileLayout.js';

const TOOLKIT_SOURCE = readFileSync(
    new URL('../src/components/MolBioToolkit/MolBioToolkitV2.tsx', import.meta.url),
    'utf8',
);
const INDEX_CSS = readFileSync(new URL('../src/index.css', import.meta.url), 'utf8');

test('Cordova uses the mobile MolBio layout in wide landscape', () => {
    assert.equal(shouldUseMolBioMobileLayout({
        cordovaShell: true,
        coarsePointer: true,
        viewportWidth: 2400,
        viewportHeight: 1080,
    }), true);
});

test('touch phones use the mobile MolBio layout in portrait and landscape', () => {
    assert.equal(shouldUseMolBioMobileLayout({
        cordovaShell: false,
        coarsePointer: true,
        viewportWidth: 390,
        viewportHeight: 844,
    }), true);
    assert.equal(shouldUseMolBioMobileLayout({
        cordovaShell: false,
        coarsePointer: true,
        viewportWidth: 844,
        viewportHeight: 390,
    }), true);
});

test('desktop and large touch workstations retain the desktop MolBio workbench', () => {
    assert.equal(shouldUseMolBioMobileLayout({
        cordovaShell: false,
        coarsePointer: false,
        viewportWidth: 1440,
        viewportHeight: 900,
    }), false);
    assert.equal(shouldUseMolBioMobileLayout({
        cordovaShell: false,
        coarsePointer: true,
        viewportWidth: 1366,
        viewportHeight: 1024,
    }), false);
});

test('touch capability does not replace a fine primary pointer on a workstation', () => {
    const coarsePointer = detectMolBioPrimaryCoarsePointer({
        matchMedia: () => ({ matches: false }),
        navigator: { maxTouchPoints: 10 },
    });
    assert.equal(coarsePointer, false);
    assert.equal(detectMolBioPrimaryCoarsePointer({
        matchMedia: () => ({ matches: true }),
        navigator: { maxTouchPoints: 0 },
    }), true);
    assert.equal(shouldUseMolBioMobileLayout({
        cordovaShell: false,
        coarsePointer,
        viewportWidth: 1280,
        viewportHeight: 720,
    }), false);
});

test('Cordova shell detection accepts the native bridge or the shell ready hook', () => {
    assert.equal(detectMolBioCordovaShell({ cordova: {} }), true);
    assert.equal(detectMolBioCordovaShell({ __BMS_CORDOVA_CONFIRM_READY__: () => undefined }), true);
    assert.equal(detectMolBioCordovaShell({}), false);
    assert.equal(detectMolBioCordovaShell(null), false);
});

test('Android Back closes mobile overlays before leaving the MolBio route', () => {
    assert.equal(resolveMolBioMobileBackAction({
        constructPickerOpen: true,
        hasSequence: true,
        surface: 'digest',
    }), 'close-constructs');
    assert.equal(resolveMolBioMobileBackAction({
        constructPickerOpen: false,
        hasSequence: true,
        surface: 'digest',
    }), 'show-map');
    assert.equal(resolveMolBioMobileBackAction({
        constructPickerOpen: false,
        hasSequence: true,
        surface: 'map',
    }), 'history');
    assert.equal(resolveMolBioMobileBackAction({
        constructPickerOpen: true,
        hasSequence: false,
        surface: 'map',
    }), 'history');
});

test('mobile selection intent blocks only the URL it superseded', () => {
    const intent = { sequenceId: 'B', supersededSequenceId: 'A', supersededRevisionId: null };
    assert.deepEqual(resolveMolBioMobileSequenceIntent(intent, 'A', null), { allow: false, clearIntent: false });
    assert.deepEqual(resolveMolBioMobileSequenceIntent(intent, 'B', null), { allow: true, clearIntent: true });
    assert.deepEqual(resolveMolBioMobileSequenceIntent(intent, 'C', null), { allow: true, clearIntent: true });
    assert.deepEqual(resolveMolBioMobileSequenceIntent(intent, 'A', 'R'), { allow: true, clearIntent: true });
});

test('failed mobile sequence activation keeps the picker and current surface', async () => {
    let activations = 0;
    const loaded = await activateMobileMolBioSequence({
        sequenceId: 'missing-sequence',
        loadSequence: async () => false,
        onActivated: () => { activations += 1; },
    });
    assert.equal(loaded, false);
    assert.equal(activations, 0);

    const loadStart = TOOLKIT_SOURCE.indexOf('const loadSequence = useCallback');
    const existingCheck = TOOLKIT_SOURCE.indexOf('const existing = workspaceTabs.find', loadStart);
    assert.ok(loadStart >= 0 && existingCheck > loadStart);
    assert.doesNotMatch(TOOLKIT_SOURCE.slice(loadStart, existingCheck), /updateQueryParams/u);
});

test('successful mobile sequence activation closes the picker once', async () => {
    let activations = 0;
    const loaded = await activateMobileMolBioSequence({
        sequenceId: 'pl931',
        loadSequence: async () => true,
        onActivated: () => { activations += 1; },
    });
    assert.equal(loaded, true);
    assert.equal(activations, 1);

    const handlerStart = TOOLKIT_SOURCE.indexOf('const handleMobileSelectSequence = useCallback');
    const handlerEnd = TOOLKIT_SOURCE.indexOf('\n    const handleMobileLoadDemo', handlerStart);
    assert.ok(handlerStart >= 0 && handlerEnd > handlerStart);
    const handlerSource = TOOLKIT_SOURCE.slice(handlerStart, handlerEnd);
    const intentIndex = handlerSource.indexOf('mobileSequenceIntentRef.current = {');
    assert.ok(intentIndex >= 0, 'a retry must own sequence authority before activation');
    assert.match(
        handlerSource,
        /sequenceId,\s*supersededSequenceId: requestedMolecularSequenceId,\s*supersededRevisionId: requestedMolecularRevisionId/u,
    );
    assert.ok(
        intentIndex < handlerSource.indexOf('activateMobileMolBioSequence('),
        'mobile sequence intent must precede activation',
    );
    assert.match(
        TOOLKIT_SOURCE,
        /resolveMolBioMobileSequenceIntent\(\s*mobileSequenceIntentRef\.current,\s*requestedMolecularSequenceId,\s*requestedMolecularRevisionId,?\s*\)/u,
        'URL reconciliation must block only the superseded authority',
    );
    assert.equal(
        (TOOLKIT_SOURCE.match(/resolveMolBioMobileSequenceIntent\(/gu) || []).length,
        2,
        'current and exact URL effects must both reconcile mobile selection intent',
    );
});

test('sequence loading is newest-wins before workspace and URL publication', () => {
    const loadStart = TOOLKIT_SOURCE.indexOf('const loadSequence = useCallback');
    const loadEnd = TOOLKIT_SOURCE.indexOf('\n    useEffect(() => {', loadStart);
    assert.ok(loadStart >= 0 && loadEnd > loadStart);
    const loadSource = TOOLKIT_SOURCE.slice(loadStart, loadEnd);
    const invalidateIndex = loadSource.indexOf('invalidateGetSequence()');
    assert.ok(invalidateIndex >= 0, 'each sequence load must invalidate an older request');
    assert.ok(
        invalidateIndex < loadSource.indexOf('const existing = workspaceTabs.find'),
        'every selection must invalidate an older fetch before an existing workspace can publish',
    );
    assert.match(TOOLKIT_SOURCE, /sequenceLoadControllerRef\s*=\s*useRef\(createLatestAsyncResourceController\(\)\)/u);
    assert.match(loadSource, /const loadToken = sequenceLoadControllerRef\.current\.begin\(\)/u);
    assert.equal(
        (loadSource.match(/sequenceLoadControllerRef\.current\.isCurrent\(loadToken\)/gu) || []).length,
        2,
    );
    assert.ok(
        loadSource.indexOf('isCurrent(loadToken)') < loadSource.indexOf('activateWorkspace(existing.id)'),
        'existing-workspace publication must be current-request guarded',
    );
    assert.ok(
        loadSource.lastIndexOf('isCurrent(loadToken)') < loadSource.indexOf('openWorkspace(converted'),
        'fetched workspace and URL publication must be current-request guarded',
    );
    assert.match(TOOLKIT_SOURCE, /sequenceLoadControllerRef\.current\.dispose\(\)/u);

    const mobileDemoStart = TOOLKIT_SOURCE.indexOf('const handleMobileLoadDemo = useCallback');
    const mobileDemoEnd = TOOLKIT_SOURCE.indexOf('\n    if (isMobileMolBio)', mobileDemoStart);
    assert.ok(mobileDemoStart >= 0 && mobileDemoEnd > mobileDemoStart);
    const mobileDemoSource = TOOLKIT_SOURCE.slice(mobileDemoStart, mobileDemoEnd);
    assert.match(mobileDemoSource, /sequenceLoadControllerRef\.current\.begin\(\)/u);
    assert.match(mobileDemoSource, /invalidateGetSequence\(\)/u);
    assert.ok(
        mobileDemoSource.lastIndexOf('sequenceLoadControllerRef.current.begin()')
            < mobileDemoSource.indexOf('loadDemo(pendingDemo)'),
        'deferred demo activation must invalidate an older saved-construct load before publishing',
    );
});

test('MolBio URL aliases share immutable authority and demos clear saved URL ownership', () => {
    assert.match(TOOLKIT_SOURCE, /requestedCanonicalMolecularSequenceId/u);
    assert.match(TOOLKIT_SOURCE, /requestedLegacyMolecularSequenceId/u);
    assert.match(
        TOOLKIT_SOURCE,
        /requestedMolecularSequenceId = requestedCanonicalMolecularSequenceId \?\? requestedLegacyMolecularSequenceId/u,
    );
    const deepLinkStart = TOOLKIT_SOURCE.indexOf('const openDeepLink = async');
    const deepLinkEnd = TOOLKIT_SOURCE.indexOf('\n        void openDeepLink()', deepLinkStart);
    assert.ok(deepLinkStart >= 0 && deepLinkEnd > deepLinkStart);
    const deepLinkSource = TOOLKIT_SOURCE.slice(deepLinkStart, deepLinkEnd);
    assert.match(deepLinkSource, /if \(deepLinkRevisionId\) return/u);
    assert.doesNotMatch(deepLinkSource, /openWorkspace\(converted/u);

    const demoStart = TOOLKIT_SOURCE.indexOf('const handleMobileLoadDemo = useCallback');
    const demoEnd = TOOLKIT_SOURCE.indexOf('\n    if (isMobileMolBio)', demoStart);
    const demoSource = TOOLKIT_SOURCE.slice(demoStart, demoEnd);
    for (const key of ['molbio_sequence_id', 'molbio_revision_id', 'sequence_id', 'revision_id']) {
        assert.match(demoSource, new RegExp(`${key}: null`, 'u'));
    }
    assert.match(demoSource, /pendingMobileDemoRef\.current = demo/u);
    assert.doesNotMatch(demoSource.slice(0, demoSource.indexOf('useEffect')), /loadDemo\(demo\)/u);
    const pendingDemoStart = TOOLKIT_SOURCE.indexOf('const pendingDemo = pendingMobileDemoRef.current');
    const pendingDemoEnd = TOOLKIT_SOURCE.indexOf('\n    }, [', pendingDemoStart);
    assert.ok(pendingDemoStart >= 0 && pendingDemoEnd > pendingDemoStart);
    const pendingDemoSource = TOOLKIT_SOURCE.slice(pendingDemoStart, pendingDemoEnd);
    for (const key of [
        'requestedCanonicalMolecularSequenceId',
        'requestedCanonicalMolecularRevisionId',
        'requestedLegacyMolecularSequenceId',
        'requestedLegacyMolecularRevisionId',
    ]) {
        assert.match(pendingDemoSource, new RegExp(key, 'u'));
    }
    assert.match(pendingDemoSource, /invalidateGetSequence\(\)/u);
    assert.match(pendingDemoSource, /loadDemo\(pendingDemo\)/u);
});

test('MolBioToolkit wires the Cordova mobile projection and native Back policy', () => {
    assert.match(TOOLKIT_SOURCE, /shouldUseMolBioMobileLayout\(\{/u);
    assert.match(TOOLKIT_SOURCE, /resolveMolBioMobileBackAction\(\{/u);
    assert.match(TOOLKIT_SOURCE, /document\.addEventListener\('backbutton'/u);
    assert.match(TOOLKIT_SOURCE, /<MobileMolBioWorkspace/u);
    assert.match(TOOLKIT_SOURCE, /!isMobileMolBio/u);
});

test('focused view keeps a safe-area-aware 48 px exit and exits on Android Back', () => {
    assert.match(TOOLKIT_SOURCE, /data-molbio-focus-exit="true"/u);
    assert.ok(TOOLKIT_SOURCE.includes("calc(env(safe-area-inset-top) + 0.75rem)"));
    assert.match(TOOLKIT_SOURCE, /min-h-12/u);
    assert.match(TOOLKIT_SOURCE, /setIsViewerFullscreen\(false\)/u);
});

test('mobile MolBio suppresses the Cordova settings toggle only while active', () => {
    assert.match(
        INDEX_CSS,
        /html\.bms-molbio-mobile-active\s+#bms-cordova-preflight-toggle\s*\{[^}]*display:\s*none\s*!important;/su,
    );
});

test('Cordova mobile MolBio reserves a status-bar fallback when landscape reports zero inset', () => {
    assert.match(
        INDEX_CSS,
        /html\.bms-cordova-shell\.bms-molbio-mobile-active\s+\[data-molbio-mobile-toolbar="true"\]\s*\{[^}]*padding-top:\s*calc\(max\(env\(safe-area-inset-top\),\s*1\.5rem\)\s*\+\s*0\.75rem\)\s*!important;/su,
    );
});

test('the mobile production branch mounts the bounded SequenceLibrary variant', () => {
    assert.match(TOOLKIT_SOURCE, /<SequenceLibrary\s+mobile/u);
    assert.match(TOOLKIT_SOURCE, /className="flex h-full min-h-0 overflow-hidden"/u);
});
