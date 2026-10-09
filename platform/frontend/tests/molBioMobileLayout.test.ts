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
