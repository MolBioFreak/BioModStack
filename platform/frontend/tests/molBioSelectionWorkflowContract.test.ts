import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const frontendRoot = process.cwd();
const readSource = (relativePath: string) => readFileSync(`${frontendRoot}/${relativePath}`, 'utf8');

const dialogSource = readSource('src/components/MolBioToolkit/SelectionActionDialog.tsx');
const toolkitSource = readSource('src/components/MolBioToolkit/MolBioToolkitV2.tsx');
const viewerSource = readSource('src/components/MolBioToolkit/SequenceViewer.tsx');
const gcTrackSource = readSource('src/components/MolBioToolkit/GCContentTrack.tsx');

test('selection actions are configuration-gated rather than immediate default creation', () => {
    assert.match(toolkitSource, /openSelectionAction\('forward_primer'\)/);
    assert.match(toolkitSource, /openSelectionAction\('reverse_primer'\)/);
    assert.match(toolkitSource, /openSelectionAction\('feature'\)/);
    assert.match(toolkitSource, /createSelectionSnapshot\(selection, sequenceData\.sequence, sequenceData\.circular\)/);
    assert.doesNotMatch(toolkitSource, /Feature_\$\{snapshot\.coordinateKey\}/);
    assert.doesNotMatch(toolkitSource, /Fwd_\$\{snapshot\.coordinateKey\}/);
    assert.doesNotMatch(toolkitSource, /Rev_\$\{snapshot\.coordinateKey\}/);
});

test('dialog requires an explicit name and exposes feature and primer identity metadata', () => {
    assert.match(dialogSource, /role="dialog"/);
    assert.match(dialogSource, /required/);
    assert.match(dialogSource, /Feature type/);
    assert.match(dialogSource, /Direction/);
    assert.match(dialogSource, /Primer type/);
    assert.match(dialogSource, /Locked selected span/);
    assert.match(dialogSource, /const trimmedName = name\.trim\(\)/);
    assert.match(dialogSource, /if \(!trimmedName \|\| busy\)/);
    assert.match(dialogSource, /disabled=\{!name\.trim\(\) \|\| busy\}/);
    assert.match(dialogSource, /dialogPanelRef/);
    assert.match(dialogSource, /event\.key !== 'Tab'/);
    assert.match(dialogSource, /previouslyFocusedRef/);
});

test('selection actions expose keyboard menu invocation and navigation', () => {
    assert.match(viewerSource, /event\.shiftKey && event\.key === 'F10'/);
    assert.match(viewerSource, /event\.key === 'ContextMenu'/);
    assert.match(toolkitSource, /role="menu"/);
    assert.match(toolkitSource, /role="menuitem"/);
    assert.match(toolkitSource, /handleQuickAddMenuKeyDown/);
});

test('reverse primer dialog previews the reverse-complemented selected sequence', () => {
    assert.match(dialogSource, /const primerSequence = primerStrand === 1/);
    assert.match(dialogSource, /reverseComplementSequence\(snapshot\.sequence/);
    assert.match(dialogSource, /value=\{primerSequence\}/);
    assert.match(dialogSource, /name: trimmedName/);
});

test('viewer and Plotly track retain a durable range without controlled SeqViz feedback or zoom hijacking', () => {
    assert.match(viewerSource, /mapSeqVizSelectionToSource\(/);
    assert.match(viewerSource, /selectionPointerButtonRef/);
    assert.match(viewerSource, /durableSelectionHighlights/);
    assert.match(viewerSource, /selectionResetVersion/);
    assert.doesNotMatch(viewerSource, /selection=\{seqVizSelection\}/);
    assert.match(gcTrackSource, /dragmode: 'select'/);
    assert.match(gcTrackSource, /onSelected=\{handleSelected\}/);
    assert.match(gcTrackSource, /scrollZoom: false/);
    assert.match(gcTrackSource, /selectionSnapshot\.ranges\.forEach/);
});
