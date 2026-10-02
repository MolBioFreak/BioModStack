import assert from 'node:assert/strict';
import test from 'node:test';
import React from 'react';
import { readFileSync } from 'node:fs';
import { bc2Page } from '../src/lib/bindcraft2Results';
import { numericMetricKeys } from '../src/lib/cohortAnalytics';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import { describeCohortMetric, splitCohortMetric } from '../src/lib/cohortMetricPresentation';
import { CohortMetricPicker } from '../src/components/CohortMetricPicker';

// Exact 102-key numeric inventory from the 100-trajectory live page (2026-10-01).
// Compact fixture records keys only: native measurements/values remain adapter-owned.
const measurements = ['binder_coldspot', 'binder_contacts', 'binder_helicity', 'binder_intra_coldspot', 'binder_pae', 'compactness', 'interface_contacts', 'interface_pae', 'iptm', 'iptm_loss', 'plddt_loss', 'ptm'];
const key = (measurement: string, phase = 'refine', reading = 'last recorded', target = 'human_EGFR') => `${phase} · ${target}.${measurement} · ${reading}`;
const fixture = [...['screen', 'refine', 'anneal', 'harden'].flatMap(phase => measurements.flatMap(m => ['last recorded', 'peak recorded'].map(r => key(m, phase, r)))), ...['iptm', 'ptm'].flatMap(m => ['last recorded', 'peak recorded'].map(r => key(m, 'mutate', r))), 'duration_seconds', 'seq_length'];

test('real inventory collapses to 14 measurements without losing any of 102 identities', () => {
    assert.equal(fixture.length, 102);
    if (process.env.BMS_BC2_METRIC_PAGE) {
        const page = bc2Page(JSON.parse(readFileSync(process.env.BMS_BC2_METRIC_PAGE, 'utf8')));
        const actual = numericMetricKeys(page.records.map((r, i) => ({ id: String(i), label: String(i), values: r.metrics })));
        assert.deepEqual([...actual].sort(), [...fixture].sort(), 'fixture equals real adapter numeric inventory');
    }
    assert.equal(describeCohortMetric('human_EGFR.iptm').shortLabel, 'Interface confidence (iPTM)');
    assert.equal(describeCohortMetric('human_EGFR.iptm_loss').shortLabel, 'iPTM loss objective');
    assert.equal(describeCohortMetric('human_EGFR.interface_contacts').shortLabel, 'Interface contacts objective');
    assert.equal(new Set(fixture.map(k => splitCohortMetric(k).measurement)).size, 14);
    assert.equal(new Set(fixture.map(k => describeCohortMetric(k).label)).size, 102);
    for (const k of fixture) {
        const d = describeCohortMetric(k, fixture);
        assert.equal(d.nativeKey, k);
        assert.ok(!d.description.includes('Native key:'));
        assert.ok(d.description.length > 0);
        assert.ok(!d.shortLabel.includes(' · '));
    }
    assert.equal(describeCohortMetric(key('iptm')).shortLabel, 'Interface confidence (iPTM)');
    assert.equal(describeCohortMetric(key('ptm')).shortLabel, 'Overall structure confidence (pTM)');
    assert.match(describeCohortMetric(key('iptm_loss')).shortLabel, /loss objective/);
    assert.match(describeCohortMetric(key('interface_contacts')).description, /not a physical count or distance/);
    assert.match(describeCohortMetric(key('iptm', 'refine', 'peak recorded')).description, /not necessarily the best/);
    assert.equal(splitCohortMetric(key('iptm', 'refine', 'last recorded', 'arbitrary.target_A')).target, 'arbitrary.target_A');
    assert.equal(describeCohortMetric('novel_field').shortLabel, 'Novel field');
    assert.match(describeCohortMetric('ppiflow_sequence_identity').shortLabel, /PPIFlow/);
});

test('mounted picker: bounded grouped measurement search, exact stage/reading, missing and async keys, no color', async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    let renderer: ReactTestRenderer;
    let keys = fixture, value = key('iptm');
    const changes: string[] = [];
    const render = () => <CohortMetricPicker label="Color" keys={keys} value={value} allowEmpty onChange={next => { changes.push(next); value = next; renderer.update(render()); }} />;
    await act(async () => { renderer = create(render()); });
    const control = (name: string) => renderer.root.findByProps({ 'aria-label': name });
    const change = async (name: string, next: string) => { await act(async () => { control(name).props.onChange({ target: { value: next } }); }); };
    try {
        assert.deepEqual(changes, []);
        assert.ok(control('Color').findAllByType('option').length <= 14);
        assert.ok(control('Color').findAllByType('optgroup').length > 1);
        await change('Color stage', 'mutate');
        assert.equal(value, key('iptm', 'mutate'));
        await change('Color reading', 'peak recorded');
        assert.equal(value, key('iptm', 'mutate', 'peak recorded'));
        await change('Color search', 'contacts');
        assert.ok(control('Color').findAllByType('option').length <= 4);
        await change('Color', JSON.stringify(['trajectory', 'binder_contacts']));
        assert.equal(value, key('iptm', 'mutate', 'peak recorded'), 'missing combination must not substitute a different metric');
        assert.match(JSON.stringify(renderer.toJSON()), /combination is not available/);
        await change('Color stage', 'refine');
        assert.equal(value, key('binder_contacts', 'refine', 'peak recorded'));
        await act(async () => { keys = []; renderer.update(render()); });
        assert.match(JSON.stringify(renderer.toJSON()), /unavailable/);
        const before = changes.length;
        await act(async () => { keys = fixture; renderer.update(render()); });
        assert.equal(changes.length, before, 'async inventory must not auto-select');
        await change('Color', '');
        assert.equal(value, '');
        assert.match(JSON.stringify(renderer.toJSON()), /No measurement selected/);
    } finally { await act(async () => renderer.unmount()); }
});

test('mounted multi-target and generic identities stay distinct', async () => {
    Object.assign(globalThis, { IS_REACT_ACT_ENVIRONMENT: true });
    const keys = [key('iptm'), key('iptm', 'refine', 'last recorded', 'other.target'), 'target X · score', 'target Y · score', 'ppiflow_sequence_identity', 'boltzgen_score', 'human_EGFR.iptm', 'human_EGFR.iptm_loss', 'other.target.iptm'];
    let value = keys[0];
    let renderer: ReactTestRenderer;
    const render = () => <CohortMetricPicker label="X" keys={keys} value={value} onChange={v => { value = v; renderer.update(render()); }} />;
    await act(async () => { renderer = create(render()); });
    const change = async (name: string, next: string) => { await act(async () => renderer.root.findByProps({ 'aria-label': name }).props.onChange({ target: { value: next } })); };
    try {
        await change('X target', 'other.target'); assert.equal(value, keys[1]);
        await change('X', JSON.stringify(['target', 'score'])); assert.equal(value, keys[1], 'multiple targets need explicit target choice');
        await change('X target', 'target Y'); assert.equal(value, keys[3]);
        await change('X', JSON.stringify(['generic', 'ppiflow_sequence_identity'])); assert.equal(value, keys[4]);
        await change('X', JSON.stringify(['generic', 'boltzgen_score'])); assert.equal(value, keys[5]);
        await change('X', JSON.stringify(['trace', 'iptm'])); assert.equal(value, keys[5]);
        await change('X target', 'human_EGFR'); assert.equal(value, keys[6]);
        await change('X', JSON.stringify(['trace', 'iptm_loss'])); assert.equal(value, keys[7]);
        assert.equal(renderer.root.findAllByProps({ 'aria-label': 'X stage' }).length, 0);
    } finally { await act(async () => renderer.unmount()); }
});
