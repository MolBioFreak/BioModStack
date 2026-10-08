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

    assert.match(template, /const FASTQ_DEFAULT_MINIMAP_PRESET: MinimapPreset = 'map-ont'/u);
    assert.match(template, /map-ont \(ONT reads\)/u);
    assert.doesNotMatch(template, /lr:hq/u, 'frontend must not expose the minimap2 lr:hq preset until the bundled runtime supports it');
    assert.doesNotMatch(ngsToolkit, /lr:hq/u, 'reusing an old NGS job must not silently seed lr:hq');
    assert.match(ngsToolkit, /fastqMinimap2Preset: p\.fastq_minimap2_preset \?\? 'map-ont'/u);
});

test('Nanopore FASTQ launch is gated on reference input and finite numeric bounds', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /const hasFastqReferenceInput = useMemo/u);
    assert.match(template, /return fastqPath\.trim\(\) !== '' && runFastqQc && hasFastqReferenceInput && hasValidFastqNumericControls/u);
    assert.match(template, /FASTQ plasmid QC requires a reference FASTA path or a pasted\/created FASTA sequence/u);
    assert.match(template, /function coerceIntegerInput/u);
    assert.match(template, /FASTQ_MAX_IGV_REPORT_MAX_SITES/u);
    assert.match(template, /max=\{FASTQ_MAX_IGV_REPORT_MAX_SITES\}/u);
});

test('Nanopore FASTQ CLI preview points at the NGS entrypoint', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /nextflow run ngs\.nf -profile nanopore_methylation/u);
    assert.doesNotMatch(template, /nextflow run main\.nf/u);
});

test('Nanopore submit success navigates to the live job-detail route', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');
    const app = readSource('src/App.tsx');

    assert.match(app, /<Route path="\/jobs\/:jobId" element=\{<JobDetailPage \/>\}/u);
    assert.match(template, /navigate\(`\/jobs\/\$\{[^}`]+\}`\)/u);
    assert.doesNotMatch(template, /navigate\(`\/results\/\$\{response\.data\.job_id\}`\)/u);
});

test('NGS runs polling is scoped to Nanopore jobs instead of pulling the whole job table', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(api, /model_id\?: string/u);
    assert.match(ngsToolkit, /fetchJobs\(\{ include_children: true, model_id: 'nanopore', limit: 100 \}\)/u);
    assert.doesNotMatch(ngsToolkit, /fetchJobs\(\{ include_children: true \}\)/u);
});

test('NGS modkit summary label matches the rendered preview limit', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');

    assert.match(ngsToolkit, /modkit summary \(first 20 rows\)/u);
    assert.match(ngsToolkit, /methylationReport\.summary\.rows\.slice\(0, 20\)/u);
    assert.doesNotMatch(ngsToolkit, /modkit summary \(first 100 rows\)/u);
});

test('Nanopore surfaces avoid always-on explainer copy', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');

    assert.doesNotMatch(template, /Pipeline Overview/u);
    assert.doesNotMatch(template, /Optional low-level parameters/u);
    assert.doesNotMatch(template, /Outputs include `fastq_qc_summary/u);
    assert.doesNotMatch(ngsToolkit, /Walled garden/u);
    assert.doesNotMatch(ngsToolkit, /Control note:/u);
    assert.doesNotMatch(ngsToolkit, /Hidden by default to avoid confusion/u);
    assert.doesNotMatch(ngsToolkit, /Raw modkit loci preview \(debug/u);
});

test('Nanopore surfaces expose external documentation linkouts in a compact box', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');

    assert.match(ngsToolkit, /Documentation/u);
    assert.match(ngsToolkit, /const NANOPORE_DOC_LINKS = \[/u);
    for (const url of [
        'https://dorado-docs.readthedocs.io/en/latest/',
        'https://github.com/nanoporetech/dorado',
        'https://github.com/nanoporetech/modkit',
        'https://github.com/epi2me-labs/wf-clone-validation',
        'https://github.com/lh3/minimap2',
        'https://igv.org/doc/igvjs/',
        'https://www.nextflow.io/docs/latest/index.html',
    ]) {
        assert.ok(ngsToolkit.includes(url), `missing docs link ${url}`);
    }
    assert.match(ngsToolkit, /target="_blank"/u);
    assert.match(ngsToolkit, /rel="noreferrer"/u);
});


test('NGS instrument mode is separated from file-analysis launch', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');
    const api = readSource('src/lib/api.ts');
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');

    assert.match(ngsToolkit, /type ToolkitView = 'launch' \| 'instrument' \| 'runs'/u);
    assert.match(ngsToolkit, /Start instrument run/u);
    assert.match(ngsToolkit, /<OntInstrumentPanel/u);
    assert.match(api, /fetchOntDeviceStatus/u);
    assert.match(api, /startOntInstrumentRun/u);
    assert.match(api, /stopOntInstrumentRun/u);
    assert.match(panel, /Real starts remain disabled until a real available position is present/u);
    assert.match(panel, /Analyze existing data/u);
    assert.match(panel, /Start instrument run/u);
});

test('NGS instrument panel exposes an explicit fake Mk1D test mode without claiming real MinKNOW connectivity', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(panel, /TEST_MODE_MK1D_DEVICE/u);
    assert.match(panel, /position: 'TEST-MK1D'/u);
    assert.match(panel, /fake_or_demo_device: true/u);
    assert.match(panel, /Test mode: fake Mk1D/u);
    assert.match(panel, /FAKE TEST CONNECTION/u);
    assert.match(panel, /does not prove MinKNOW connectivity or start a real instrument run/u);
    assert.match(panel, /Start fake test run/u);
    assert.match(panel, /fake_or_demo_devices: true/u);
    assert.match(panel, /Instrument positions/u);
    assert.match(panel, /Run setup/u);
    assert.match(panel, /Start packet/u);
    assert.match(panel, /POD5 raw signal/u);
    assert.match(panel, /Basecaller/u);
    assert.match(api, /fake_or_demo_device\?: boolean/u);
});


test('NGS instrument panel exposes live flow-cell scrutiny and safe reconnect controls', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(panel, /Selected position truth/u);
    assert.match(panel, /Configuration test cell flag/u);
    assert.match(panel, /Preflight blockers/u);
    assert.match(panel, /Refresh\/reconnect position/u);
    assert.match(panel, /Restart instrument unavailable/u);
    assert.match(panel, /Real start disabled until MinKNOW reports a present sequencing flow cell/u);
    assert.match(api, /refreshOntPosition/u);
    assert.match(api, /restartOntPosition/u);
    assert.match(api, /is_ctc\?: boolean/u);
    assert.match(api, /channel_count\?: number/u);
    assert.match(api, /output_directories\?: Record<string, string>/u);
});


test('NGS instrument panel exposes guarded MinKNOW hardware check controls', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(panel, /Run hardware check/u);
    assert.match(panel, /Hardware check requires MinKNOW to report a present flow cell\/test cell/u);
    assert.match(panel, /window\.confirm\('Start a MinKNOW hardware check/u);
    assert.match(panel, /Hardware checks in API history/u);
    assert.match(panel, /Protocol runs in API history/u);
    assert.match(api, /beginOntHardwareCheck/u);
    assert.match(api, /confirm_hardware_check: true/u);
    assert.match(api, /hardware_check_run_id\?: string/u);
});
