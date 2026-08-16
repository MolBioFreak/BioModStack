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
        'Choose file → preview fields → import completed dataset, or open existing jobs.',
        'Drop a dataset file to auto-detect ProteinBase, JSONL, or CSV/TSV.',
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
        'Start from a real import surface instead of the tiny job dropdown',
        'Drop in a dataset file and this page will auto-detect whether it looks like a ProteinBase bundle',
    ]) {
        rejectSnippet(source, stale);
    }
});

test('analytics, BioXP USB, and quality panels keep copy terse', () => {
    const source = readSource('src', 'components', 'AnalyticsDashboard.tsx')
        + readSource('src', 'components', 'BioXpCockpit.tsx')
        + readSource('src', 'components', 'QualitySettingsPanel.tsx');

    for (const snippet of [
        'Backbone screen: target contacts, hotspot coverage, RFA quality.',
        'Generator-native confidence, affinity priors, batch shape.',
        'PAE matrix; chain bands when available.',
        'Direct OEM operator controls',
        'Stage presets tune start_t, samples, ranking, and anchor strictness.',
    ]) {
        requireSnippet(source, snippet);
    }

    for (const stale of [
        'Orientation metrics will surface here automatically once they are persisted.',
        'This view emphasizes generator-native confidence, affinity priors, and batch-shape signals.',
        'RFA review should lead with contact geometry, hotspot coverage',
        'This lens now drives the top-of-tab posture.',
        'BMS/workstation side is staged now; handler-local endpoints own actual usbmon capture when powered.',
        'What the handler-side implementation must return before we trust a run.',
        'Stage-optimized mode follows the repo PPIFlow guidance for the selected stage.',
        'Core partial-flow controls below are currently managed by the selected stage strategy.',
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
        'physics/FK steering potentials; use batching for high sample counts.',
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

test('Results, structure viewer, and quality settings avoid explainer paragraphs', () => {
    const source = readSource('src', 'components', 'ResultsViewer.tsx')
        + readSource('src', 'components', 'StructureViewerPane.tsx')
        + readSource('src', 'components', 'QualitySettingsPanel.tsx');

    for (const snippet of [
        'Added ${result.uniqueCount.toLocaleString()} / ${result.totalCount.toLocaleString()} BoltzGen reps',
        'Filter/promote outputs, then continue the paused PLR workflow.',
        'RF lens: ${rfMetricLabels.short}. Toggle loop/whole-antibody in review.',
        'Any-Target: whole target. Epitope: selected residues.',
        'BoltzGen candidates: triage by conf_score, affinity priors, size.',
        'Uniform scalar pLDDT ${formatMetricValue(conforNetsScalarPlddt, 1)}; no residue tensor.',
        'Pre-sequence: anchor scoring on; zero-anchor rejection off.',
        'MSA controls for Protenix validation. Provider/runtime only.',
    ]) {
        requireSnippet(source, snippet);
    }

    for (const stale of [
        'RF headline distances here are using',
        'The RF review cards below are showing',
        'nearest binder CA to the full target surface',
        'BoltzGen generator cohorts should be read as de novo candidates first',
        'ConforNets scalar pLDDT fallback is active',
        'Mol* residue coloring uses the displayed scalar pLDDT',
        'Stage-optimized pre-sequence refinement still scores anchors',
        'Shared MSA controls for the Protenix validator path',
        'filtered designs using ${clusterLabel} clustering',
        'Filter the paused review table, promote the outputs you want to keep',
    ]) {
        rejectSnippet(source, stale);
    }
});

test('BioXP handler cockpit uses terse operator copy, not explainer paragraphs', () => {
    const source = readSource('src', 'components', 'BioXpCockpit.tsx');

    for (const snippet of [
        'Connection',
        'Claim USB Transport',
        'Non-homing Recovery',
        'X Axis',
        'Camera',
        'Physical Emergency Abort Unavailable',
    ]) {
        requireSnippet(source, snippet);
    }

    for (const stale of [
        'Status-first operator surface',
        'UNKNOWN or STALE evidence never authorizes controls.',
        'Normal Commands',
        'No normal OEM commands are available.',
        'Offline Protocol Validation',
        'Local Jobs',
        'Profile',
        'Probe',
        'runtime_ready',
        'hardware_ready',
        'OEM/liquid-handler-first BMS proxy for the robot-local BioXP runtime',
        'Operator-readable gantry/grabber controls: arm first',
        'X/Y/Z plus Grabber are live axis controls. Speed/acc inputs are per card.',
        'Payloads are clamped before send; robot API still applies its own normalization.',
        'Switch Home requires capture_bundle=true and operator_note evidence',
        'Runtime lifecycle actions are governed by the top-right BIOXP LINK panel.',
        'Default live testing uses Governed Interlink, Protocol Operator, and Handler Controls.',
        'Runtime link, motion, jobs, recipes, camera.',
        'Live axes: X/Y/Z + grabber. Zero ≠ switch-home.',
        'Switch Home disabled here; use supervised OEM recipe.',
    ]) {
        rejectSnippet(source, stale);
    }
});
