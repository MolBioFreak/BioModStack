import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';

function readSource(relativePath: string): string {
    return readFileSync(join(process.cwd(), relativePath), 'utf8');
}

test('Nanopore FASTQ launch defaults stay compatible with bundled minimap2', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');
    const cloneState = readSource('src/lib/nanoporeCloneState.ts');

    assert.match(template, /const FASTQ_DEFAULT_MINIMAP_PRESET: MinimapPreset = 'map-ont'/u);
    assert.match(template, /map-ont \(ONT reads\)/u);
    assert.doesNotMatch(template, /lr:hq/u, 'frontend must not expose the minimap2 lr:hq preset until the bundled runtime supports it');
    assert.doesNotMatch(ngsToolkit, /lr:hq/u, 'reusing an old NGS job must not silently seed lr:hq');
    assert.match(cloneState, /fastqMinimap2Preset: p\.fastq_minimap2_preset \?\? 'map-ont'/u);
});

test('NGS instrument control uses only opaque intent handles and has no browser raw-start client', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');
    const api = readSource('src/lib/api.ts');
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const ontApi = api.slice(
        api.indexOf('// ONT INSTRUMENT CONTROL API'),
        api.indexOf('// ONT SIGNAL WORKBENCH API'),
    );

    assert.match(ngsToolkit, /type ToolkitView = NgsToolkitView/u);
    assert.match(ngsToolkit, /Data Analysis/u);
    assert.match(ngsToolkit, /Instrument setup/u);
    assert.doesNotMatch(ngsToolkit, /Analyze existing data/u);
    assert.doesNotMatch(ngsToolkit, /style=\{view === '(?:launch|instrument|runs)' \? \{ backgroundColor: 'var\(--accent-secondary\)' \} : undefined\}/u);
    assert.match(ngsToolkit, /bg-\[var\(--accent-primary\)\]\/20 ring-1 ring-\[var\(--accent-primary\)\]\/50 text-\[var\(--accent-primary\)\]/u);
    assert.match(panel, /Data Analysis/u);
    assert.doesNotMatch(ngsToolkit, /Start instrument run/u);
    assert.match(ngsToolkit, /<OntInstrumentPanel/u);
    assert.match(api, /fetchOntDeviceStatus/u);
    assert.match(api, /createOntRunIntent/u);
    assert.match(api, /startOntRunIntent/u);
    assert.match(api, /option_id: string/u);
    assert.match(api, /option_receipt_id: string/u);
    assert.match(api, /intent_generation: number/u);
    assert.doesNotMatch(api, /startOntInstrumentRun|stopOntInstrumentRun|beginOntHardwareCheck|refreshOntPosition|restartOntPosition/u);
    assert.doesNotMatch(ontApi, /flow_cell_id|protocol_id|model_id|output_director(?:y|ies)|minknow_payload|output_files|hardware_check_run_id/u);
    assert.match(panel, /submit its opaque protocol intent/u);
    assert.match(panel, /Protocol and output policy are server-issued opaque handles/u);
    assert.match(panel, /Validate run intent/u);
    assert.match(panel, /physical MinKNOW start remains disabled/u);
    assert.match(panel, /createOntRunIntent/u);
    assert.match(panel, /startOntRunIntent/u);
    assert.match(api, /requestMk1dReconnect/u);
    assert.match(api, /confirm_reconnect: true/u);
    assert.match(panel, /Reconnect Mk1D \(local host\)/u);
    assert.match(panel, /not available through Tailnet/u);
    assert.doesNotMatch(panel, /startOntInstrumentRun|Start fake test run|TEST-MK1D|Run hardware check|output_director(?:y|ies)|flow_cell_id|protocol_id|model_id/u);
    assert.doesNotMatch(panel, /\.filter\(\(device\) => device\.device_type === 'mk1d'\)/u);
});

test('NGS instrument panel registers one governed existing POD5 candidate before BLOW5 preparation', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(panel, /Register existing POD5/u);
    assert.match(panel, /fetchOntExternalPod5Candidates/u);
    assert.match(panel, /registerOntExternalPod5Candidate/u);
    assert.match(panel, /exactDomainExperimentId/u);
    assert.doesNotMatch(panel, /BMS_ONT_EXTERNAL_POD5_ROOT|\/mnt\/BioModStack/u);
    assert.match(api, /\/api\/ont\/raw-signal\/external-pod5-candidates/u);
    assert.match(api, /candidate_id: candidateId/u);
    assert.match(api, /experiment_group: experimentGroup/u);
});
