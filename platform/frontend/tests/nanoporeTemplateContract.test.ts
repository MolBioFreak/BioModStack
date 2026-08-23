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

test('Nanopore reference workflows are gated on an exact saved MolBio revision', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');
    const chooser = readSource('src/components/ngs/NanoporeWorkflowChooser.tsx');

    assert.match(template, /const requiresReference = selectedWorkflow === 'clone'/u);
    assert.match(template, /selectedMolbioSequenceId/u);
    assert.match(template, /selectedMolbioRevisionId/u);
    assert.match(template, /selectedWorkflow === 'clone' \|\| selectedWorkflow === 'plasmidQc' \|\| selectedWorkflow === 'constructScreening' \|\| selectedWorkflow === 'fastqQc'/u);
    assert.match(template, /Shared Experiment reference/u);
    assert.match(chooser, /saved MolBio revision/u);
    assert.doesNotMatch(template, /reference_fasta:\s*effectiveReferencePath/u);
    assert.match(template, /function coerceIntegerInput/u);
    assert.match(template, /FASTQ_MAX_IGV_REPORT_MAX_SITES/u);
    assert.match(template, /max=\{FASTQ_MAX_IGV_REPORT_MAX_SITES\}/u);
});

test('NGS exposes named workflow choices with input and output expectations before the detailed form', () => {
    const chooser = readSource('src/components/ngs/NanoporeWorkflowChooser.tsx');

    for (const label of [
        'Choose what you want to do',
        'Validate a known plasmid / clone',
        'QC plasmid reads',
        'Screen a construct',
        'ONT FASTQ QC',
        'Basecall DNA simplex',
        'Basecall RNA',
        'Basecall DNA duplex',
        'Call modified bases',
        'Classify and demultiplex RBK114',
        'Analyze aligned plasmid BAM',
        'How to use this page:',
    ]) assert.match(chooser, new RegExp(label.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'), 'u'));
    assert.match(chooser, /aria-pressed=\{selected\}/u);
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
    assert.match(source, /max-w-\[1480px\]/u);
    assert.match(source, /xl:grid-cols-2/u);
    assert.match(source, /data-testid="ngs-review-bar"/u);
    assert.match(source, /GPU assignment/u);
});

test('Nanopore control surface does not expose raw Nextflow arguments', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.doesNotMatch(template, /CLI parameter preview/u);
    assert.doesNotMatch(template, /nextflow run workflows\/ngs\/ont_fastq_qc\.nf/u);
});

test('Nanopore submit success navigates to the context-preserving NGS inspector', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /navigate\(contextHref\('\/ngs', \{ section: 'analyses', job_id: submittedJobId \}\)\)/u);
    assert.doesNotMatch(template, /navigate\(`\/jobs\/\$\{[^}`]+\}`\)/u);
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
    const payload = readSource('src/lib/nanoporeLaunchPayload.ts');

    assert.match(template, /selectedWorkflow === 'clone'\s*\? 'wf_clone_validation'/u);
    assert.match(template, /selectedWorkflow === 'plasmidQc' \|\| selectedWorkflow === 'bamQc'/u);
    assert.match(template, /selectedWorkflow === 'constructScreening'\s*\? 'ont_construct_screening'/u);
    assert.match(template, /selectedWorkflow === 'fastqQc'\s*\? 'ont_fastq_qc'/u);
    assert.match(template, /setBarcodeKit\(''\)/u);
    assert.match(payload, /if \(selectedWorkflow === 'clone'\) params\.run_assembly = true/u);
    assert.match(payload, /if \(selectedWorkflow === 'constructScreening'\) params\.run_assembly = runAssembly/u);
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

test('NGS runs polling covers every exact canonical NGS model with bounded pagination', () => {
    const ngsToolkit = readSource('src/components/NGSToolkit.tsx');
    const api = readSource('src/lib/api.ts');

    assert.match(api, /model_id\?: string/u);
    assert.match(ngsToolkit, /\['nanopore', 'ont_fastq_qc', 'ont_plasmid_qc', 'ont_construct_screening', 'wf_clone_validation'\]/u);
    assert.match(ngsToolkit, /model_id,/u);
    assert.match(ngsToolkit, /limit: 500/u);
    assert.match(ngsToolkit, /offset/u);
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

test('Nanopore Validate and Submit share one authoritative blocker function', () => {
    const template = readSource('src/components/NanoporeTemplate.tsx');

    assert.match(template, /const getSubmissionBlockers = \(\): string\[\] =>/u);
    assert.match(template, /const handleValidate = \(\) => \{[\s\S]*?getSubmissionBlockers\(\)/u);
    assert.match(template, /const handleSubmit = \(\) => \{[\s\S]*?getSubmissionBlockers\(\)/u);
    assert.doesNotMatch(template, /const handleSubmit = \(\) => \{[\s\S]*?if \(!jobName\.trim\(\)\)/u);
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
    assert.match(ngsToolkit, /Instrument intent/u);
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

test('NGS instrument panel renders only safe device truth and an intent status', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');
    const api = readSource('src/lib/api.ts');
    const ontApi = api.slice(
        api.indexOf('// ONT INSTRUMENT CONTROL API'),
        api.indexOf('// ONT SIGNAL WORKBENCH API'),
    );

    assert.match(panel, /Instrument positions/u);
    assert.match(panel, /Flow cell: \{device\.flow_cell\.present \? 'present' : 'absent'\}/u);
    assert.match(panel, /No protocol option is currently available/u);
    assert.match(panel, /Preflight blockers/u);
    assert.match(panel, /Intent \{lastRun\.id\} · \{lastRun\.status\}/u);
    assert.match(ontApi, /interface OntFlowCellInfo \{\s+present: boolean;/u);
    assert.match(ontApi, /output_summary: Record<'fastq' \| 'pod5' \| 'bam', number>/u);
    assert.doesNotMatch(ontApi, /fake_or_demo_device\?: boolean|is_ctc\?: boolean|channel_count\?: number|output_director(?:y|ies)|rpc_ports|connection_error/u);
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

test('NGS instrument panel exposes indexed-BLOW5 waveform inspection', () => {
    const panel = readSource('src/components/ngs/OntInstrumentPanel.tsx');

    assert.match(panel, /Indexed BLOW5 waveform inspection/u);
    assert.match(panel, /requestOntRawSignalWaveform/u);
    assert.match(panel, /fetchOntRawSignalWaveform/u);
    assert.match(panel, /aria-label="Raw electrical signal waveform"/u);
    assert.match(panel, /aria-label="Raw-signal publication receipt"/u);
    assert.match(panel, /Published artifacts/u);
    assert.match(panel, /Adjacent indexes/u);
    assert.match(panel, /Parent manifest/u);
});
