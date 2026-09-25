import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import * as React from 'react';
import { act, create, type ReactTestRenderer } from 'react-test-renderer';
import ts from 'typescript';
import * as analytics from '../src/lib/cohortAnalytics';
import { correlateMetrics, formatMetric, metricKeys, metricLabel, numericMetricKeys, secondaryStructureComposition, summarizeMetric, type CohortRow } from '../src/lib/cohortAnalytics';

const rowsOf = (values: Record<string, unknown>[]): CohortRow[] => values.map((values, index) => ({ id: `row:${index}`, label: `Sample ${index}`, values }));
const metricRows = (values: unknown[]) => rowsOf(values.map(x => ({ x })));
const close = (actual: number | null, expected: number, tolerance = 1e-12) => {
    assert.notEqual(actual, null);
    assert.ok(Math.abs(actual! - expected) <= tolerance * Math.max(1, Math.abs(expected)), `${actual} != ${expected}`);
};

test('union includes late native keys in first-appearance order; numeric keys do not coerce', () => {
    const rows = rowsOf([{ zero: 0, bool: false, str: '1', null: null }, { late: 1, zero: 3, null: 2, inf: Infinity }, { bool: -2, nan: NaN }]);
    assert.deepEqual(metricKeys(rows), ['zero', 'bool', 'str', 'null', 'late', 'inf', 'nan']);
    assert.deepEqual(numericMetricKeys(rows), ['zero', 'bool', 'null', 'late']);
    assert.deepEqual(metricKeys([]), []);
    assert.deepEqual(numericMetricKeys([]), []);
});

test('summary partitions every row into observed, missing, explicit null or nonnumeric', () => {
    const rows = rowsOf([{ x: 0 }, {}, { x: undefined }, { x: null }, { x: false }, { x: true }, { x: '4' }, { x: Infinity }, { x: -Infinity }, { x: NaN }, { x: {} }, { x: [] }]);
    const stats = summarizeMetric(rows, 'x');
    assert.deepEqual(stats, { observed: 1, missing: 2, nulls: 1, nonNumeric: 8,
        min: 0, max: 0, mean: 0, median: 0, q1: 0, q3: 0, stdDev: null });
    assert.equal(stats.observed + stats.missing + stats.nulls + stats.nonNumeric, rows.length);
});

test('sample SD and explicitly linear interpolated quartiles', () => {
    const stats = summarizeMetric(metricRows([4, 1, 3, 2]), 'x');
    assert.deepEqual({ min: stats.min, max: stats.max, median: stats.median, q1: stats.q1, q3: stats.q3, mean: stats.mean },
        { min: 1, max: 4, median: 2.5, q1: 1.75, q3: 3.25, mean: 2.5 });
    close(stats.stdDev, Math.sqrt(5 / 3));
    const pair = summarizeMetric(metricRows([0, 10]), 'x');
    assert.equal(pair.q1, 2.5);
    assert.equal(pair.q3, 7.5);
    close(pair.stdDev, Math.sqrt(50));
});

test('empty, absent, singleton and constant cohorts remain honest', () => {
    assert.deepEqual(summarizeMetric([], 'x'), { observed: 0, missing: 0, nulls: 0, nonNumeric: 0,
        min: null, max: null, mean: null, median: null, q1: null, q3: null, stdDev: null });
    assert.equal(summarizeMetric(rowsOf([{}]), 'x').missing, 1);
    assert.equal(summarizeMetric(metricRows([2]), 'x').stdDev, null);
    assert.equal(summarizeMetric(metricRows([2, 2, 2]), 'x').stdDev, 0);
    assert.equal(summarizeMetric(metricRows([2, 2, 2]), 'x').mean, 2);
});

test('stable statistics with large offsets, extreme magnitude and tiny spread', () => {
    const shifted = summarizeMetric(metricRows([1e12 + 1, 1e12 + 2, 1e12 + 3, 1e12 + 4]), 'x');
    assert.equal(shifted.mean, 1e12 + 2.5);
    close(shifted.stdDev, Math.sqrt(5 / 3));
    const extremes = summarizeMetric(metricRows([-1e308, 0, 1e308]), 'x');
    assert.equal(extremes.mean, 0);
    close(extremes.stdDev, 1e308);
    assert.equal(extremes.q1, -5e307);
    assert.equal(extremes.q3, 5e307);
    assert.equal(summarizeMetric(metricRows([1e308, 1e308]), 'x').mean, 1e308);
    const tiny = summarizeMetric(metricRows([1e-200, 2e-200, 3e-200]), 'x');
    assert.ok(tiny.stdDev! > 0);
    assert.ok(Math.abs(tiny.stdDev! / 1e-200 - 1) < 1e-12);
    assert.equal(summarizeMetric(metricRows([-Number.MAX_VALUE, Number.MAX_VALUE]), 'x').stdDev, null);
});

test('descriptive functions never mutate input records or native number scales', () => {
    const rows = rowsOf([{ x: 0.25, y: 4 }, { x: 0.5, y: 2 }, { x: 0.75, y: 0 }]);
    for (const row of rows) { Object.freeze(row.values); Object.freeze(row); }
    Object.freeze(rows);
    const before = JSON.stringify(rows);
    metricKeys(rows); numericMetricKeys(rows); summarizeMetric(rows, 'x'); correlateMetrics(rows, 'x', 'y');
    assert.equal(JSON.stringify(rows), before);
    assert.equal(summarizeMetric(rows, 'x').mean, 0.5);
});

test('known positive, negative and zero Pearson relationships', () => {
    const rows = rowsOf([{ x: -1, y: -2, z: 2, orthogonal: 1 }, { x: 0, y: 0, z: 0, orthogonal: -2 }, { x: 1, y: 2, z: -2, orthogonal: 1 }]);
    close(correlateMetrics(rows, 'x', 'y').r, 1);
    close(correlateMetrics(rows, 'x', 'z').r, -1);
    close(correlateMetrics(rows, 'x', 'orthogonal').r, 0);
    close(correlateMetrics(rows, 'x', 'x').r, 1);
    assert.equal(correlateMetrics(rows, 'x', 'y').reason, null);
});

test('Pearson requires at least three complete finite pairs and nonconstant metrics', () => {
    const rows = rowsOf([{ x: 0, y: 0 }, { x: 1, y: 2 }, { x: 2, y: null }, { x: '3', y: 6 }, { x: true, y: 8 }, { x: Infinity, y: 10 }]);
    assert.deepEqual(correlateMetrics(rows, 'x', 'y'), { pairs: 2, r: null, reason: 'insufficient pairs' });
    assert.deepEqual(correlateMetrics([], 'x', 'y'), { pairs: 0, r: null, reason: 'insufficient pairs' });
    assert.deepEqual(correlateMetrics(rowsOf([{ x: 0, y: 1 }, { x: 0, y: 2 }, { x: 0, y: 3 }]), 'x', 'y'),
        { pairs: 3, r: null, reason: 'constant metric' });
});

test('pairwise missingness does not remove observations from unrelated pairs', () => {
    const rows = rowsOf([{ x: 0, y: 0 }, { x: 1, y: 2, z: 4 }, { x: 2, y: 4, z: 5 }, { x: 3, z: 6 }]);
    assert.equal(correlateMetrics(rows, 'x', 'y').pairs, 3);
    assert.equal(correlateMetrics(rows, 'x', 'z').pairs, 3);
    assert.equal(correlateMetrics(rows, 'y', 'z').pairs, 2);
});

test('correlations retain large-offset, tiny and extreme relationships without overflow', () => {
    for (const values of [[1e12 + 1, 1e12 + 2, 1e12 + 3], [1e-200, 2e-200, 3e-200], [-1e308, 0, 1e308]]) {
        const rows = rowsOf(values.map(x => ({ x, y: -x })));
        close(correlateMetrics(rows, 'x', 'y').r, -1);
    }
});

test('1000-row summaries and correlations include the entire cohort, including last-row-only metrics', () => {
    const rows = rowsOf(Array.from({ length: 1000 }, (_, x) => ({ x, y: 3 * x - 7, ...(x === 999 ? { last: 0 } : {}) })));
    const summary = summarizeMetric(rows, 'x');
    assert.equal(summary.observed, 1000);
    assert.equal(summary.mean, 499.5);
    assert.equal(summary.median, 499.5);
    close(summary.stdDev, Math.sqrt(1000 * 1001 / 12));
    assert.deepEqual(numericMetricKeys(rows), ['x', 'y', 'last']);
    assert.equal(summarizeMetric(rows, 'last').missing, 999);
    assert.equal(correlateMetrics(rows, 'x', 'y').pairs, 1000);
    close(correlateMetrics(rows, 'x', 'y').r, 1);
});

test('display labels and formatting retain zeros, false, missing/null distinctions and native fractions', () => {
    assert.equal(metricLabel('seq_length'), 'Sequence length');
    assert.equal(metricLabel('dsasa'), 'dSASA');
    assert.equal(metricLabel('unfamiliar_metric'), 'Unfamiliar metric');
    assert.equal(metricLabel('ppiflow_sequence_identity'), 'PPIFlow sequence identity');
    assert.equal(formatMetric(0), '0');
    assert.equal(formatMetric(false), 'false');
    assert.equal(formatMetric(null), 'Explicit null');
    assert.equal(formatMetric(undefined), 'Not reported');
    assert.equal(formatMetric('1.23456789'), '1.23456789');
    assert.equal(formatMetric(0.123456), (0.1235).toLocaleString(undefined, { maximumFractionDigits: 4 }));
    assert.equal(formatMetric(0.5), (0.5).toLocaleString());
    assert.equal(formatMetric(NaN), 'NaN');
    assert.equal(formatMetric(Infinity), 'Infinity');
});

test('native composition requires complete residue fractions and never invents a campaign pie', () => {
    assert.deepEqual(secondaryStructureComposition(rowsOf([{ coil_percent: 0.4, helix_percent: 0.24, strand_percent: 0.36 }])[0]), [0.4, 0.24, 0.36]);
    assert.equal(secondaryStructureComposition(rowsOf([{ coil_percent: 0.4, helix_percent: 0.24 }])[0]), null);
    assert.equal(secondaryStructureComposition(rowsOf([{ coil_percent: 40, helix_percent: 24, strand_percent: 36 }])[0]), null);
    assert.equal(secondaryStructureComposition(rowsOf([{ coil_percent: 0.4, helix_percent: null, strand_percent: 0.6 }])[0]), null);
    assert.equal(secondaryStructureComposition(undefined), null);
});

test('mounted chart contract: full cohort, exact inspection, explicit selection, analytics and empty state', async () => {
    // Mock only Plotly's browser engine; execute the real TSX and all real descriptive helpers.
    const nativeRequire = createRequire(import.meta.url);
    const source = readFileSync(new URL('../src/components/CohortAnalytics.tsx', import.meta.url), 'utf8');
    const compiled = ts.transpileModule(source, { compilerOptions: { jsx: ts.JsxEmit.ReactJSX, module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2022, esModuleInterop: true } }).outputText;
    const module = { exports: {} as { CohortAnalytics: React.ComponentType<Record<string, unknown>> } };
    new Function('require', 'module', 'exports', compiled)((name: string) => {
        if (name === 'react-plotly.js') return (props: Record<string, unknown>) => React.createElement('plot-probe', props);
        if (name === '../lib/cohortAnalytics') return analytics;
        return nativeRequire(name);
    }, module, module.exports);
    const { CohortAnalytics } = module.exports;
    const globals: Record<string, unknown> = {
        IS_REACT_ACT_ENVIRONMENT: true,
        document: { documentElement: {}, body: {} },
        window: { matchMedia: () => ({ addEventListener() {}, removeEventListener() {} }) },
        getComputedStyle: () => ({ getPropertyValue: () => '#123456' }),
        MutationObserver: class { observe() {} disconnect() {} },
        ResizeObserver: class { observe() {} disconnect() {} },
    };
    const saved = new Map(Object.keys(globals).map(key => [key, Object.getOwnPropertyDescriptor(globalThis, key)]));
    for (const [key, value] of Object.entries(globals)) Object.defineProperty(globalThis, key, { configurable: true, value });
    let renderer: ReactTestRenderer | undefined;
    const rows = rowsOf(Array.from({ length: 1000 }, (_, i) => ({ seq_length: i, dsasa: i / 1000, constant: 1, ...(i % 2 ? { color: i } : {}) })));
    const inspected: string[] = [], selections: string[][] = [];
    const props = { rows, selectedIds: ['row:5'], activeId: 'row:999', onInspect: (id: string) => inspected.push(id), onSelect: (ids: string[]) => selections.push(ids), mode: 'dashboard' };
    const plots = () => renderer!.root.findAll(node => String(node.type) === 'plot-probe');
    const select = (label: string) => renderer!.root.findByProps({ 'aria-label': label });
    try {
        await act(async () => { renderer = create(React.createElement(CohortAnalytics, props), { createNodeMock: () => ({ clientWidth: 620 }) }); });
        assert.equal(plots().length, 3, 'scatter, distribution and bounded native bars are visible without disclosure');
        const scatter = plots()[0].props;
        assert.equal(scatter.data[0].x.length, 1000);
        assert.equal(scatter.data[0].y[999], 0.999, 'native fractions are never multiplied');
        assert.equal(scatter.data[0].marker.symbol[999], 'star');
        assert.equal(scatter.data[0].marker.symbol[5], 'diamond');
        assert.equal(scatter.config.toImageButtonOptions.format, 'svg');
        assert.equal(scatter.layout.width, 620);
        assert.equal(plots()[1].props.data[1].q1[0], summarizeMetric(rows, 'dsasa').q1, 'box quartiles agree with the descriptive table');
        assert.equal(plots()[2].props.data[0].type, 'bar');
        assert.equal(plots()[2].props.data[0].customdata.length, 20);
        assert.equal(plots()[2].props.data[0].customdata[0], 'row:999');
        assert.equal(select('X metric').props.title, 'Sequence length (seq_length)');
        assert.deepEqual(selections, [], 'mount must not select candidates');
        await act(async () => {
            scatter.onClick({ points: [{ customdata: 'row:999' }] });
            scatter.onClick({ points: [{ customdata: 'foreign-row' }] });
            scatter.onSelected({ points: [{ customdata: 'row:888' }, { customdata: 'row:888' }, { customdata: 'foreign-row' }] });
            scatter.onSelected(null);
            select('Scatter drag mode').props.onChange({ target: { value: 'lasso' } });
        });
        assert.deepEqual(inspected, ['row:999']);
        assert.deepEqual(selections, [['row:888']], 'only explicitly selected valid IDs are returned, not the existing selection');
        assert.equal(plots()[0].props.layout.dragmode, 'lasso');
        await act(async () => { renderer!.update(React.createElement(CohortAnalytics, { ...props, mode: 'analytics' })); });
        assert.equal(plots().length, 5);
        assert.ok(renderer!.root.findByProps({ 'aria-label': 'Metric summary pages' }));
        assert.equal(plots()[3].props.data[0].type, 'scatter3d');
        assert.equal(plots()[3].props.data[0].customdata.length, 1000);
        assert.equal(plots()[4].props.data[0].z[2][0], null, 'constant correlations are undefined');
        assert.match(plots()[4].props.data[0].customdata[2][0], /constant metric/);
        await act(async () => { plots()[3].props.onClick({ points: [{ customdata: 'row:998' }] }); plots()[2].props.onClick({ points: [{ customdata: 'row:997' }] }); });
        assert.deepEqual(inspected, ['row:999', 'row:998', 'row:997']);
        await act(async () => { select('3D Z metric').props.onChange({ target: { value: 'color' } }); });
        assert.equal(plots()[3].props.data[0].z.length, 500, 'the third axis omits missing rather than filling zero');
        await act(async () => { select('Color metric').props.onChange({ target: { value: 'color' } }); });
        assert.equal(plots()[0].props.data[0].x.length, 500);
        assert.equal(plots()[0].props.data[1].x.length, 500, 'missing color never removes complete coordinate pairs');
        await act(async () => { select('Correlation metrics').props.onChange({ target: { selectedOptions: [{ value: 'seq_length' }, { value: 'dsasa' }] } }); });
        assert.equal(plots()[4].props.data[0].z.length, 2);
        const compositionRows = rowsOf([{ seq_length: 50, dsasa: 715, radius_of_gyration: 10.45,
            coil_percent: 0.4, helix_percent: 0.24, strand_percent: 0.36 }]);
        await act(async () => { renderer!.update(React.createElement(CohortAnalytics, { ...props, rows: compositionRows, activeId: 'row:0', mode: 'dashboard' })); });
        const pie = plots().find(plot => plot.props.data[0].type === 'pie')?.props.data[0];
        assert.deepEqual(pie?.labels, ['Coil', 'Helix', 'Strand']);
        assert.deepEqual(pie?.values, [0.4, 0.24, 0.36]);
        await act(async () => { renderer!.update(React.createElement(CohortAnalytics, { ...props, rows: rowsOf([{ x: null }, { x: false }]) })); });
        assert.equal(plots().length, 0);
        assert.match(JSON.stringify(renderer!.toJSON()), /No finite numeric observations/);
    } finally {
        if (renderer) await act(async () => { renderer!.unmount(); });
        for (const [key, descriptor] of saved) {
            if (descriptor) Object.defineProperty(globalThis, key, descriptor);
            else Reflect.deleteProperty(globalThis, key);
        }
    }
});
