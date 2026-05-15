import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

const readSource = (...parts: string[]) => readFileSync(join(process.cwd(), ...parts), 'utf8');

const requireSnippet = (source: string, snippet: string) => {
    assert.ok(source.includes(snippet), `missing compact copy: ${snippet}`);
};

const rejectSnippet = (source: string, snippet: string) => {
    assert.ok(!source.includes(snippet), `stale explainer copy still present: ${snippet}`);
};

test('Data Viewer landing keeps import/open surfaces concise', () => {
    const source = readSource('src', 'components', 'DataViewerLanding.tsx');

    for (const snippet of [
        'Structure metrics map into existing design analytics fields.',
        'Columns auto-detected; sequence / pLDDT / structure hints are shown when present.',
        'Recent jobs and imported datasets appear here.',
    ]) {
        requireSnippet(source, snippet);
    }

    for (const stale of [
        'Boltz-2 / ESMFold metrics such as pLDDT, iPSAE, iPTM, pTM, and complex confidence values flow into existing design analytics fields.',
        'Tabular columns were auto-detected so the viewer can expose sequence / pLDDT / structure mapping hints instead of dropping you into a blank state.',
        'The viewer still uses the existing job/design pipeline. Recent jobs appear here so you can jump in without hunting through the header dropdown.',
    ]) {
        rejectSnippet(source, stale);
    }
});

test('structure-prediction batch/target copy stays short and operator-focused', () => {
    const source = readSource('src', 'components', 'StructurePredictionTemplate.tsx');

    for (const snippet of [
        'Paste FASTA or one sequence per line; target imports become shared binder screens.',
        'Shared target:',
        'Imported target is staged for conditioned/frozen complex prediction; keep the primary chain sequence unchanged.',
        'Physics/FK steering; use batching for high sample counts.',
        'Target conditioning needs a shared target or complex component.',
        'Shards EnvDB for balanced/maximum runs. Fast screens quickly; Off is rollback/debug.',
    ]) {
        requireSnippet(source, snippet);
    }

    for (const stale of [
        'Paste FASTA or one sequence per line to run a named batch. Runtime outputs are auto-numbered with the chosen prefix so the result set stays traceable.',
        'Shared-target screen active:',
        'Imported target structure is available for conditioned or frozen complex prediction.',
        'Enable physics/FK steering potentials. Can improve geometry, but high sample counts multiply internal particles and should use memory-safe batching.',
        'Target conditioning currently applies to complex predictions. Add a shared target source or additional complex component before launching.',
        'Keeps the total MSA CPU budget fixed while splitting EnvDB target search for high-quality balanced/maximum runs.',
    ]) {
        rejectSnippet(source, stale);
    }
});

test('protein-local-redesign source/review surfaces do not carry explainer paragraphs', () => {
    const source = readSource('src', 'components', 'ProteinLocalRedesignTemplate.tsx');

    for (const snippet of [
        'Optional source-complex simulation; promote completed PDB-backed outputs into redesign.',
        'Creates a source-structure job; promote completed PDB-backed designs below.',
        'Upload, reuse a run, choose a preset, or fetch RCSB—no manual path typing.',
        'Chain ${designChain ||',
        'Pause at checkpoints, filter in Results, then continue that subset.',
    ]) {
        requireSnippet(source, snippet);
    }

    for (const stale of [
        'Optional upstream step. If you want to simulate a protein with chosen DNA, RNA, ions, or ligands before local editing',
        'This does not bypass visual redesign. It creates a source-structure job, waits for designs',
        'Use the existing structure-source system rather than typing file paths by hand.',
        'The workflow will derive editable residues on chain',
        'Match the RFA interaction pattern: pause at a chosen checkpoint',
    ]) {
        rejectSnippet(source, stale);
    }
});
