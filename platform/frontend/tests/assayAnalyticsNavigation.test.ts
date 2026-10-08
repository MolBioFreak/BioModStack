import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const appSource = readFileSync(resolve(process.cwd(), 'src/App.tsx'), 'utf8');
const layoutSource = readFileSync(resolve(process.cwd(), 'src/components/Layout.tsx'), 'utf8');
const assayPagePath = resolve(process.cwd(), 'src/components/AssayAnalytics.tsx');

test('BioModStack exposes Stats Toolkit as a first-class route and nav tab', () => {
    assert.match(appSource, /AssayAnalytics/, 'App should import the BMS stats toolkit page component');
    assert.match(appSource, /path=["']\/assay["']/, 'App should preserve /assay route compatibility for the toolkit page');
    assert.match(layoutSource, /to=["']\/assay["']/, 'Layout should include the existing /assay nav link');
    assert.match(layoutSource, /Stats Toolkit/, 'Layout should label the tab Stats Toolkit');
    assert.doesNotMatch(layoutSource, /Assay Analytics/, 'Layout should not expose the old Assay Analytics label');
});

test('Stats Toolkit page carries qPCR, chromatography, DOE/statistics, and debug surfaces', () => {
    assert.equal(existsSync(assayPagePath), true, 'AssayAnalytics.tsx should exist');
    const source = readFileSync(assayPagePath, 'utf8');
    for (const marker of [
        'qPCR',
        'QuantStudio',
        'StepOnePlus',
        'Chromatography',
        'Waters',
        'Empower',
        'isoform',
        'DOE',
        'stats-tools',
        'runtime',
        'Runtime',
        'Lifecycle',
    ]) {
        assert.match(source, new RegExp(marker, 'i'), `AssayAnalytics should expose ${marker}`);
    }
});
