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
    assert.match(panel, /No instrument run button is enabled without a real available position/u);
    assert.match(panel, /Analyze existing data/u);
    assert.match(panel, /Start instrument run/u);
    assert.doesNotMatch(panel, /fake device/iu);
});
