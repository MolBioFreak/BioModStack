import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

import {
    displayStrandForMoleculeOrientation,
    inferNucleotideMoleculeMetadataFromParsedRecord,
    inferSequenceTypeFromParsedRecord,
    normalizeSequenceForType,
    parseSequenceInput,
    sequenceForDisplayStrand,
    transformRangeForDisplayStrand,
} from '../src/components/MolBioToolkit/utils/nucleotides.js';
import { findOpenReadingFrames } from '../src/components/MolBioToolkit/utils/orfs.js';

const TOOLKIT_PATH = resolve(process.cwd(), 'src/components/MolBioToolkit/MolBioToolkitV2.tsx');

test('GenBank RNA parser metadata resolves to RNA even when parsed.type is absent', () => {
    assert.equal(
        inferSequenceTypeFromParsedRecord({
            sequence: 'AUGGCCUUUAA',
            isRna: true,
            sequenceTypeFromLocus: 'RNA',
        }),
        'rna',
    );

    assert.equal(
        inferSequenceTypeFromParsedRecord({
            sequence: 'AUGGCCUUUAA',
            type: 'RNA',
        }),
        'rna',
    );

    assert.equal(
        inferSequenceTypeFromParsedRecord({
            sequence: 'ATGGCCTTTAA',
            sequenceTypeFromLocus: 'DNA',
            isDNA: true,
        }),
        'dna',
    );
});

test('explicit locus DNA metadata wins over RNA-virus-looking names', () => {
    const parsed = {
        name: 'ANDV/Switzerland/Hu-3337/2026_L',
        sequence: 'AGTAGTAGACTCCGGGATAG',
        type: 'DNA',
        sequenceTypeFromLocus: 'DNA',
        isDNA: true,
    };
    const inferred = inferSequenceTypeFromParsedRecord(parsed);
    const metadata = inferNucleotideMoleculeMetadataFromParsedRecord(parsed);

    assert.equal(inferred, 'dna');
    assert.equal(metadata.sequenceType, 'dna');
    assert.equal(metadata.moleculeLabel, 'dsDNA');
    assert.equal(normalizeSequenceForType(parsed.sequence, inferred), 'AGTAGTAGACTCCGGGATAG');
});

test('ANDV FASTA-style T-coded segment imports as negative-sense ssRNA', () => {
    const parsed = {
        name: 'ANDV/Switzerland/Hu-3337/2026_L',
        sequence: 'AGTAGTAGACTCCGGGATAG',
        type: 'DNA',
    };
    const metadata = inferNucleotideMoleculeMetadataFromParsedRecord(parsed);

    assert.equal(metadata.sequenceType, 'rna');
    assert.equal(metadata.moleculeStrandedness, 'single');
    assert.equal(metadata.moleculeOrientation, 'negative');
    assert.equal(metadata.moleculeLabel, '(-)ssRNA');
    assert.equal(normalizeSequenceForType(parsed.sequence, metadata.sequenceType), 'AGUAGUAGACUCCGGGAUAG');
});

test('negative-source display strand keeps source view by default and reverse-complements plus view', () => {
    assert.equal(displayStrandForMoleculeOrientation('negative'), 'minus');
    assert.equal(displayStrandForMoleculeOrientation('positive'), 'plus');
    assert.equal(displayStrandForMoleculeOrientation('not_applicable'), 'plus');

    const sourceRna = 'AGUAC';
    assert.equal(sequenceForDisplayStrand(sourceRna, 'rna', 'minus', 'minus'), 'AGUAC');
    assert.equal(sequenceForDisplayStrand(sourceRna, 'rna', 'minus', 'plus'), 'GUACU');
    assert.deepEqual(
        transformRangeForDisplayStrand(2, 5, 10, 'minus', 'plus'),
        { start: 5, end: 8 },
    );
    assert.deepEqual(
        transformRangeForDisplayStrand(5, 8, 10, 'plus', 'minus'),
        { start: 2, end: 5 },
    );
});

test('explicit dsDNA, ssDNA, dsRNA, and ssRNA orientation metadata are normalized', () => {
    assert.deepEqual(
        inferNucleotideMoleculeMetadataFromParsedRecord({
            sequence: 'ATGCGT',
            moleculeType: 'double-stranded DNA',
        }),
        {
            sequenceType: 'dna',
            moleculeStrandedness: 'double',
            moleculeOrientation: 'not_applicable',
            moleculeLabel: 'dsDNA',
        },
    );

    assert.deepEqual(
        inferNucleotideMoleculeMetadataFromParsedRecord({
            sequence: 'ATGCGT',
            moleculeType: 'single strand DNA',
            sense: 'positive strand',
        }),
        {
            sequenceType: 'dna',
            moleculeStrandedness: 'single',
            moleculeOrientation: 'positive',
            moleculeLabel: '(+)ssDNA',
        },
    );

    assert.deepEqual(
        inferNucleotideMoleculeMetadataFromParsedRecord({
            sequence: 'AUGCGU',
            moleculeType: 'dsRNA',
        }),
        {
            sequenceType: 'rna',
            moleculeStrandedness: 'double',
            moleculeOrientation: 'not_applicable',
            moleculeLabel: 'dsRNA',
        },
    );

    assert.deepEqual(
        inferNucleotideMoleculeMetadataFromParsedRecord({
            sequence: 'AUGCGU',
            moleculeType: 'ssRNA',
            orientation: 'minus strand',
        }),
        {
            sequenceType: 'rna',
            moleculeStrandedness: 'single',
            moleculeOrientation: 'negative',
            moleculeLabel: '(-)ssRNA',
        },
    );
});

test('explicit RNA parser metadata canonicalizes T-coded RNA input to U', () => {
    const parsed = {
        name: 'ssRNA segment',
        sequence: 'AGTAGTAGACTCCGGGATAG',
        moleculeType: 'RNA',
    };
    const inferred = inferSequenceTypeFromParsedRecord(parsed);

    assert.equal(inferred, 'rna');
    assert.equal(normalizeSequenceForType(parsed.sequence, inferred), 'AGUAGUAGACUCCGGGAUAG');
});

test('sequence normalization preserves RNA length by canonicalizing thymine to uracil', () => {
    assert.equal(normalizeSequenceForType('ATGGCCTTTAA', 'rna'), 'AUGGCCUUUAA');
    assert.equal(normalizeSequenceForType('AUGGCCUUUAA', 'dna'), 'ATGGCCTTTAA');
});

test('explicit RNA paste/build accepts T-coded input without dropping bases', () => {
    const parsed = parseSequenceInput('>ssRNA\nATG TAA\n', 'rna');

    assert.equal(parsed.name, 'ssRNA');
    assert.equal(parsed.sequenceType, 'rna');
    assert.equal(parsed.sequence, 'AUGUAA');
    assert.deepEqual(parsed.invalidCharacters, []);
});

test('RNA ORF discovery treats U codons as coding-sequence T equivalents', () => {
    const codingRna = `CCCAUG${'GCC'.repeat(33)}UAA`;
    const orfs = findOpenReadingFrames(codingRna, 90);

    assert.ok(orfs.length >= 1);
    assert.equal(orfs[0].start, 3);
    assert.equal(orfs[0].end, codingRna.length);
    assert.equal(orfs[0].strand, 1);
    assert.equal(orfs[0].frame, 1);
});

test('MolBio import path uses parser metadata, canonical normalization, and persistent create', () => {
    const source = readFileSync(TOOLKIT_PATH, 'utf8');

    assert.match(source, /inferNucleotideMoleculeMetadataFromParsedRecord\(parsed\)/);
    assert.match(source, /moleculeStrandedness: moleculeMetadata\.moleculeStrandedness/);
    assert.match(source, /moleculeOrientation: moleculeMetadata\.moleculeOrientation/);
    assert.match(source, /molecule_label/);
    assert.match(source, /molecule_strandedness: sequenceData\.moleculeStrandedness/);
    assert.match(source, /normalizeSequenceForType\(parsed\.sequence \|\| '', inferredType\)/);
    assert.match(source, /createSequence\(sequencePayloadFromData\(sequenceData\)\)/);
    assert.match(source, /sequenceId: savedImport\.id/);
    assert.match(source, /dirty: false/);
    assert.match(source, /sourceDisplayStrandForSequenceData\(nextSequence\)/);
    assert.match(source, /activeDisplayStrand=\{activeDisplayStrand\}/);
    assert.match(source, /onDisplayStrandChange=\{handleDisplayStrandChange\}/);
    assert.match(source, /findOpenReadingFrames\([\s\S]*sequenceData\.sequence,[\s\S]*100,[\s\S]*sequenceData\.circular,[\s\S]*\)/);
});

test('viewer labels expose molecule labels and SeqViz receives polymer type, not molecule label', () => {
    const headerSource = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/SequenceHeader.tsx'), 'utf8');
    const modalSource = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/MolecularInputModal.tsx'), 'utf8');
    const viewerSource = readFileSync(resolve(process.cwd(), 'src/components/MolBioToolkit/SequenceViewer.tsx'), 'utf8');

    assert.match(headerSource, /const moleculeLabel = sequenceData\.moleculeLabel \|\| sequenceData\.sequenceType\.toUpperCase\(\)/);
    assert.match(headerSource, /sequenceData\.circular \? 'Circular' : 'Linear'/);
    assert.match(headerSource, /onDisplayStrandChange/);
    assert.match(headerSource, /displayStrandSymbol\(strand\)/);
    assert.match(modalSource, /label=\{sequence\.molecule_label\}/);
    assert.match(viewerSource, /const seqVizSeqType = sequenceData\.sequenceType === 'protein' \? 'aa' : sequenceData\.sequenceType/);
    assert.match(viewerSource, /sequenceForDisplayStrand\(/);
    assert.match(viewerSource, /const sourceSelection = mapSeqVizSelectionToSource\(/);
    assert.match(viewerSource, /shouldReverseComplementForDisplay\(sourceDisplayStrand, resolvedDisplayStrand\)/);
    assert.match(viewerSource, /const mergedHighlightedRegions = useMemo\(/);
    assert.match(viewerSource, /highlights=\{mergedHighlightedRegions\}/);
    assert.match(viewerSource, /seqType=\{seqVizSeqType\}/);
});
