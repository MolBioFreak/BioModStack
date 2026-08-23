import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { parseMmCIF } from '../src/utils/pdbUtils';

const source = (relativePath: string): string => readFileSync(join(process.cwd(), 'src', relativePath), 'utf8');

test('mmCIF source conversion preserves protein, ssDNA, and ligand records as PDB lines', () => {
    const atomHeaders = [
        'group_PDB', 'type_symbol', 'label_atom_id', 'label_alt_id', 'label_comp_id',
        'label_asym_id', 'label_entity_id', 'label_seq_id', 'pdbx_PDB_ins_code',
        'auth_seq_id', 'auth_comp_id', 'auth_asym_id', 'auth_atom_id', 'B_iso_or_equiv',
        'Cartn_x', 'Cartn_y', 'Cartn_z', 'pdbx_PDB_model_num', 'id', 'occupancy',
    ];
    const cif = [
        'data_rfd3_complex',
        'loop_',
        ...atomHeaders.map((header) => `_atom_site.${header}`),
        'ATOM C CA . ALA A 1 1 . 1 ALA A CA 90.0 1.0 2.0 3.0 1 1 1.0',
        'ATOM P P . DA B 2 1 . 1 DA B P 80.0 4.0 5.0 6.0 1 2 1.0',
        'HETATM C C1 . LIG C 3 101 . 101 LIG C C1 70.0 7.0 8.0 9.0 1 3 1.0',
        '#',
    ].join('\n');

    const parsed = parseMmCIF(cif);
    const content = parsed.models[0]?.content || '';
    const bodyLines = content.split(/\r?\n/).filter((line) => line.startsWith('ATOM') || line.startsWith('HETATM'));

    assert.equal(bodyLines.length, 3);
    assert.match(content, /^ATOM.* DA B/m);
    assert.match(content, /^HETATM.*LIG C/m);
});

test('RFD3 exposes one source-to-submit iteration workbench', () => {
    const template = source('components/ProteinLocalRedesignTemplate.tsx');
    const modes = source('components/proteinModificationModes.ts');

    assert.match(template, /data-bms-rfd3-iteration-workbench="unified"/);
    assert.match(template, /Execution Depth/);
    assert.match(template, /Native RFD3 only/);
    assert.match(template, /Continue through sequence design and validation/);
    assert.match(modes, /RFD3 Iteration Workbench/);
    assert.doesNotMatch(modes, /RFD3 Native Local Edit/);
    assert.doesNotMatch(modes, /Validated Region Redesign/);
});

test('RFD3 workbench assigns canonical residue roles from the linked visual selector', () => {
    const template = source('components/ProteinLocalRedesignTemplate.tsx');

    assert.match(template, /Hold structure and amino acid/);
    assert.match(template, /Remodel coordinates, hold amino acid/);
    assert.match(template, /Remodel coordinates and recall amino acid with RFD3/);
    assert.match(template, /Remodel coordinates and redesign amino acid downstream/);
    assert.match(template, /disabled=\{isNativeLocalRedesign && nativeRedesignMode === 'minimal_insertion'\}/);
    assert.match(template, /select_unfixed_sequence: rfd3SequenceRecallRanges \|\| undefined/);
    assert.match(template, /nativeRedesignMode === 'minimal_insertion'\s*\?\s*'insert_only'/);
    assert.match(template, /Native RFD3 hold \/ recall roles/);
    assert.match(template, /Native RFD3 hold \/ recall/);
    assert.doesNotMatch(template, /Sequence design is not requested or run by the native RFD3 lane/);
    assert.match(template, /sequence_redesign_ranges: rfd3SequenceRecallRanges/);
    assert.match(template, /Validated depth requires at least one sequence-redesign residue/);
    assert.match(template, /const effectiveSeqMethod: SequenceMethod = isNativeLocalRedesign \? 'skip' : seqMethod/);
    assert.match(template, /setManualRangesText\(selectedEditableResidues\.size > 0 \? derivedManualRanges : ''\)/);
    assert.doesNotMatch(template, /select_unfixed_sequence stays empty/);
    assert.doesNotMatch(template, /Fixed-sequence partial diffusion/);
});

test('RFD3 uses its bounded run and exact-context RCSB source picker', () => {
    const template = source('components/ProteinLocalRedesignTemplate.tsx');

    assert.match(template, /Rfd3SourceSelector/);
    const picker = source('components/Rfd3SourceSelector.tsx');
    assert.match(picker, /\/api\/designs\/reusable-structures/);
    assert.match(picker, /Your Runs/);
    assert.match(picker, /Paste PDB or mmCIF text/);
    assert.match(picker, /RCSB/);
    assert.match(picker, /Model/);
    assert.match(picker, /Sample/);
    assert.match(picker, /Chain/);
    assert.match(picker, /Entity/);
    assert.match(picker, /searchCmRcsb\(rcsbQuery, 'full_structure_context'\)/);
    assert.match(picker, /fetch\(`\/api\/rcsb\/\$\{selectedEntry\.accession\}`\)/);
    assert.doesNotMatch(picker, /registerCmRcsbSelection/);
    assert.match(template, /selectedTarget\.modelNumber/);
    assert.match(template, /selectedTarget\.designChainId/);
    assert.match(template, /new File\(\[activeModel\.content\], canonicalStructureSourceName\(selectedTarget, sourceDigest\)/);
    assert.match(template, /uploadImmutableFile\('inputs\/protein_local_redesign', sourceFile, sourceDigest\)/);
    assert.match(template, /crypto\.subtle\.digest\('SHA-256'/);
    assert.match(template, /const boundedStem = stem\.slice\(0, 160\)/);
    assert.match(template, /sourceDigest \? `-\$\{sourceDigest\}`/);
    assert.match(template, /sourcePath\?\.toLowerCase\(\)\.endsWith\('\.pdb'\) && !selectedTarget/);
    assert.match(template, /pendingRoleHydration/);
    assert.match(template, /initialValues\.select_unfixed_sequence/);
    assert.match(template, /initialValues\.sequence_redesign_ranges/);
    assert.match(template, /selectResidueKeysFromRanges/);
    assert.match(template, /setSelectedSequenceRecallResidues\(recalled\)/);
    assert.match(template, /setContextChains\(chainSummaries/);
    assert.match(template, /The selected source must load successfully before submission/);
    assert.match(template, /setDesignChain\(''\)/);
    assert.match(template, /setContextChains\(\[\]\)/);
    assert.doesNotMatch(template, /TargetAntigenSelector/);
});
