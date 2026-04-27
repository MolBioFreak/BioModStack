import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const appSource = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8');
const layoutSource = readFileSync(resolve(process.cwd(), 'src/components/Layout.tsx'), 'utf8');
const assayPagePath = resolve(process.cwd(), 'src/components/AssayAnalytics.tsx');

test('BioModStack exposes assay analytics as a first-class route and nav tab', () => {
    assert.match(appSource, /AssayAnalytics/, 'App should import the BMS assay analytics page');
    assert.match(appSource, /path=["']\/assay["']/, 'App should route /assay to the analytics page');
    assert.match(layoutSource, /to=["']\/assay["']/, 'Layout should include an /assay nav link');
    assert.match(layoutSource, /Assay Analytics/, 'Layout should label the new tab Assay Analytics');
});

test('assay analytics page carries qPCR, chromatography, and DOE/statistics surfaces', () => {
    assert.equal(existsSync(assayPagePath), true, 'AssayAnalytics.tsx should exist');
    const source = readFileSync(assayPagePath, 'utf8');
    for (const marker of [
        'qPCR',
        'QuantStudio',
        'StepOnePlus',
        'Chromatography',
        'Waters',
        'Empower',
        'plasmid isoform',
        'DOE',
        'JMP-like',
        'Plotly',
    ]) {
        assert.match(source, new RegExp(marker, 'i'), `AssayAnalytics should expose ${marker}`);
    }
});
