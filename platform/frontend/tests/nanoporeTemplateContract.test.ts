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

test('Nanopore FASTQ launch is gated on reference input, finite numeric bounds, and a selected QC or clone workflow', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /const hasFastqReferenceInput = useMemo/u);
    assert.match(template, /selectedWorkflow === 'clone' \|\| selectedWorkflow === 'plasmidQc' \|\| selectedWorkflow === 'constructScreening' \|\| selectedWorkflow === 'fastqQc'/u);
    assert.match(template, /&& \(hasFastqReferenceInput \|\| Boolean\(molbioSequenceId\)\)\s+&& hasValidFastqNumericControls/u);
    assert.match(template, /This workflow requires a reference FASTA path or a pasted\/created FASTA sequence/u);
    assert.match(template, /function coerceIntegerInput/u);
    assert.match(template, /FASTQ_MAX_IGV_REPORT_MAX_SITES/u);
    assert.match(template, /max=\{FASTQ_MAX_IGV_REPORT_MAX_SITES\}/u);
});

test('NGS exposes named workflow choices with input and output expectations before the detailed form', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /Choose what you want to do/u);
    assert.match(template, /Validate a known plasmid \/ clone/u);
    assert.match(template, /QC plasmid reads/u);
    assert.match(template, /Screen a construct/u);
    assert.match(template, /ONT FASTQ QC/u);
    assert.match(template, /Basecall DNA simplex/u);
    assert.match(template, /Basecall RNA/u);
    assert.match(template, /Basecall DNA duplex/u);
    assert.match(template, /Call modified bases/u);
    assert.match(template, /Classify and demultiplex RBK114/u);
    assert.match(template, /Analyze aligned plasmid BAM/u);
    assert.match(template, /aria-pressed=\{selectedWorkflow === workflow\.key\}/u);
    assert.match(template, /How to use this page:/u);
});

test('Clone-validation tuning controls serialize bounded vendor-supported settings without unsupported switches', () => {
    const source = readSource('src/components/NanoporeTemplate.tsx');
    for (const setting of [
        'wf_clone_flye_quality',
        'wf_clone_non_uniform_coverage',
        'wf_clone_canu_fast',
        'wf_clone_cutsite_mismatch',
        'wf_clone_primer_mismatch',
        'wf_clone_expected_coverage',
        'wf_clone_expected_identity',
    ]) {
        assert.match(source, new RegExp(setting));
    }
    assert.doesNotMatch(source, /wf_clone_analyse_unclassified/u);
    assert.match(source, /enable_rotating_reference_frames/u);
    assert.match(source, /single_ref_split_min_mapq/u);
    assert.match(source, /max-w-\[1440px\]/);
    assert.match(source, /xl:grid-cols-12/);
});

test('Nanopore control surface does not expose raw Nextflow arguments', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.doesNotMatch(template, /CLI parameter preview/u);
    assert.doesNotMatch(template, /nextflow run workflows\/ngs\/ont_fastq_qc\.nf/u);
});

test('Nanopore submit success navigates to the live job-detail route', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');
    const app = readSource('src/App.tsx');

    assert.match(app, /<Route path="\/jobs\/:jobId" element=\{<JobDetailPage \/>\}/u);
    assert.match(template, /navigate\(`\/jobs\/\$\{[^}`]+\}`\)/u);
    assert.doesNotMatch(template, /navigate\(`\/results\/\$\{response\.data\.job_id\}`\)/u);
});

test('Nanopore P4 controls serialize only locked molecule, quality, duplex, and barcode choices', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /type DoradoMolecule = 'dna' \| 'rna'/u);
    assert.match(template, /type DoradoMode = 'simplex' \| 'duplex'/u);
    assert.match(template, /useState<ModifiedBases>\([^\n]*\|\| 'none'\)/u);
    assert.match(template, /dorado_quality_mode: doradoModel/u);
    assert.match(template, /dorado_basecall_mode: doradoMode/u);
    assert.match(template, /duplex_pairs: doradoMode === 'duplex'/u);
    assert.match(template, /barcode_kit: barcodeKit \|\| undefined/u);
    assert.match(template, /sample_sheet: barcodeKit/u);
    assert.match(template, /ont_basecall_rna/u);
    assert.match(template, /ont_basecall_dna/u);
    assert.doesNotMatch(template, /dna_r10\.4\.1_e8\.2_400bps_sup@v5\.2\.0/u);
    assert.doesNotMatch(template, /rna004_130bps_sup@v5\.2\.0/u);
    assert.doesNotMatch(template, /dorado_model: doradoModel/u);
});

test('Nanopore workflow state routes clone and BAM QC to their supported workflows', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /selectedWorkflow === 'clone'\s*\? 'wf_clone_validation'/u);
    assert.match(template, /selectedWorkflow === 'plasmidQc' \|\| selectedWorkflow === 'bamQc'/u);
    assert.match(template, /selectedWorkflow === 'constructScreening'\s*\? 'ont_construct_screening'/u);
    assert.match(template, /selectedWorkflow === 'fastqQc'\s*\? 'ont_fastq_qc'/u);
    assert.match(template, /setBarcodeKit\(''\)/u);
    assert.match(template, /run_assembly: selectedWorkflow === 'clone'/u);
    assert.match(template, /if \(value\) \{ setModifiedBases\('none'\); setRunModkit\(false\); setRunAssembly\(false\); \}/u);
    assert.match(template, /type="range"\s+min=\{0\}/u);
});

test('Nanopore selected workflows keep source controls contextual and require references for clone, BAM, and modified routes', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /selectedWorkflow === 'clone' \|\| selectedWorkflow === 'plasmidQc'/u);
    assert.match(template, /selectedWorkflow === 'bamQc'/u);
    assert.match(template, /selectedWorkflow === 'modified'/u);
    assert.match(template, /Existing BAM with MM\/ML tags/u);
    assert.match(template, /selectedWorkflow === 'constructScreening'/u);
    assert.match(template, /selectedWorkflow === 'fastqQc'/u);
    assert.match(template, /const requiresReference = selectedWorkflow === 'clone'/u);
});

test('NGS runs polling is scoped to Nanopore jobs instead of pulling the whole job table', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(api, /model_id\?: string/u);
    assert.match(ngsToolkit, /fetchJobs\(\{ include_children: true, model_id: 'nanopore', limit: 100, summary: true \}\)/u);
    assert.doesNotMatch(ngsToolkit, /fetchJobs\(\{ include_children: true \}\)/u);
    assert.doesNotMatch(ngsToolkit, /refetchInterval: 5000/u);
    assert.match(ngsToolkit, /function ontWorkflowDisplayName/u);
    assert.match(ngsToolkit, /ONT Construct Screening/u);
    assert.match(ngsToolkit, /job\.params\?\.ont_workflow_id/u);
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

test('NGS instrument panel exposes no fake or demo Mk1D mode', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(panel, /devices = liveDevices\.filter\(\(device\) => device\.device_type === 'mk1d' && !device\.fake_or_demo_device\)/u);
    assert.doesNotMatch(panel, /TEST_MODE_MK1D_DEVICE|TEST-MK1D|FAKE TEST CONNECTION|Start fake test run|test mode Mk1D|mirrored by fake starts/u);
    assert.doesNotMatch(api, /fake_or_demo_device\?: boolean/u);
    assert.match(panel, /Instrument positions/u);
    assert.match(panel, /Run setup/u);
    assert.match(panel, /Start packet/u);
    assert.match(panel, /POD5 raw signal/u);
    assert.match(panel, /Basecaller/u);
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

test('NGS instrument panel has an explicit bounded Mk1D recovery action with observed-status truthfulness', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(api, /requestMk1dReconnect/u);
    assert.match(api, /\/api\/ont\/devices\/reconnect/u);
    assert.match(panel, /Reconnect Mk1D/u);
    assert.match(panel, /reconnectMk1d\.isPending/u);
    assert.match(panel, /Recovery receipt/u);
    assert.match(panel, /not confirmed connected until a post-recovery device status is observed/u);
    assert.doesNotMatch(panel, /restartOntPosition\(.*Reconnect Mk1D/us);
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
