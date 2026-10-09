import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';

const frontendRoot = process.cwd();
const readSource = (relativePath: string) => readFileSync(`${frontendRoot}/${relativePath}`, 'utf8');

const toolkitSource = readSource('src/components/MolBioToolkit/MolBioToolkitV2.tsx');

const viewerSource = readSource('src/components/MolBioToolkit/SequenceViewer.tsx');
const gcTrackSource = readSource('src/components/MolBioToolkit/GCContentTrack.tsx');
const primerPanelSource = readSource('src/components/MolBioToolkit/panels/PrimerPanel.tsx');
const alignmentPanelSource = readSource('src/components/MolBioToolkit/panels/AlignmentPanel.tsx');
const editPanelSource = readSource('src/components/MolBioToolkit/panels/EditPanel.tsx');
const featurePanelSource = readSource('src/components/MolBioToolkit/panels/FeaturePanel.tsx');

test('selection actions are configuration-gated rather than immediate default creation', () => {
    assert.match(toolkitSource, /openSelectionAction\('forward_primer'\)/);
    assert.match(toolkitSource, /openSelectionAction\('reverse_primer'\)/);
    assert.match(toolkitSource, /openSelectionAction\('feature'\)/);
    assert.match(toolkitSource, /createSelectionSnapshot\(selection, sequenceData\.sequence, sequenceData\.circular\)/);
    assert.doesNotMatch(toolkitSource, /Feature_\$\{snapshot\.coordinateKey\}/);
    assert.doesNotMatch(toolkitSource, /Fwd_\$\{snapshot\.coordinateKey\}/);
    assert.doesNotMatch(toolkitSource, /Rev_\$\{snapshot\.coordinateKey\}/);
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

test('primer panel delegates single and all-primer highlights to authoritative split sites', () => {
    const highlightPrimerSource = primerPanelSource.match(
        /const highlightPrimer = [\s\S]*?(?=\n\s*const addLibraryPrimerToSequence)/,
    )?.[0];
    assert.ok(highlightPrimerSource);
    assert.match(highlightPrimerSource, /getPrimerHighlightRegions/);
    assert.match(highlightPrimerSource, /primersToHighlight\.flatMap/);
    assert.doesNotMatch(highlightPrimerSource, /start:\s*existingPrimer\.start/);
    assert.doesNotMatch(highlightPrimerSource, /start:\s*primer\.start/);
});

test('all selection-consuming panels preserve or explicitly reject origin-spanning geometry', () => {
    assert.match(primerPanelSource, /createSelectionSnapshot/);
    assert.match(primerPanelSource, /buildSelectionPrimer/);
    assert.match(primerPanelSource, /Pinned to selected locus/);
    assert.match(primerPanelSource, /Rotate Origin First/);
    assert.doesNotMatch(primerPanelSource, /Math\.min\(selection\.start/);

    assert.match(alignmentPanelSource, /createSelectionSnapshot/);
    assert.match(alignmentPanelSource, /wrappingSelection/);
    assert.match(alignmentPanelSource, /scalar-offset selection alignment/);
    assert.doesNotMatch(alignmentPanelSource, /Math\.min\(selection\.start/);

    assert.match(editPanelSource, /createSelectionSnapshot/);
    assert.match(editPanelSource, /disabled=\{!contiguousSelection\}/);
    assert.match(editPanelSource, /Origin-spanning delete, replace, reverse, and complement edits are disabled/);
    assert.doesNotMatch(editPanelSource, /Math\.min\(selection\.start/);

    assert.match(featurePanelSource, /selectionSnapshot\?\.coordinateLabel/);
    assert.doesNotMatch(featurePanelSource, /Selection \{Math\.min\(selection\.start/);
});
