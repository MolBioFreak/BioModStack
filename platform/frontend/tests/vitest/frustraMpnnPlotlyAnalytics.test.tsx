import React, { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';

const roots: Root[] = [];
function render(element: React.ReactNode) {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    roots.push(root);
    act(() => root.render(element));
    return {
        container,
        rerender: (next: React.ReactNode) => act(() => root.render(next)),
        getAllByTestId: (id: string) => Array.from(container.querySelectorAll(`[data-testid="${id}"]`)),
        getByRole: (_role: string, { name }: { name: string }) => Array.from(container.querySelectorAll('h2,h3')).find(node => node.textContent === name),
    };
}
function cleanup() {
    roots.splice(0).forEach(root => act(() => root.unmount()));
    document.body.replaceChildren();
}
import { afterEach, expect, test, vi } from 'vitest';
import type { PlotParams } from 'react-plotly.js';
import Analytics from '../../src/components/FrustraMpnnPlotlyAnalytics';
import { ThemeContext, THEMES, type ThemeId } from '../../src/components/themeContext';
import { CANONICAL_AMINO_ACIDS as AA, type CmLandscapeResidue } from '../../src/components/conformationalMapping/conformationalMappingSemantics';
import { buildFrustraMpnnPlotlyModel } from '../../src/components/frustraMpnnPlotlyModel';

const capture = vi.hoisted(() => ({ plots: [] as PlotParams[] }));
vi.mock('react-plotly.js', () => ({ default: (props: PlotParams) => { capture.plots.push(props); return <div data-testid="plot" />; } }));
afterEach(() => { cleanup(); capture.plots = []; document.documentElement.style.cssText = ''; });
const residues: CmLandscapeResidue[] = Array.from({ length: 540 }, (_, i) => {
    const identity = { entity_instance_id: 'pdb:A', auth_asym_id: 'A', auth_seq_id: String(i + 1), insertion_code: '', sequence_index: i, wt: AA[i % 20] };
    return { ...identity, key: `pdb:A:${i + 1}:`, slots: AA.map((aa, j) => ({ ...identity, candidate_id: 'candidate', mutation_aa: aa, score: i + j / 100, class: j % 3 === 0 ? 'high' : j % 3 === 1 ? 'neutral' : 'minimal', scoreable: true, status: 'ok', reason: null, provenance: {} })) };
});
const view = (rows = residues, highMax = -1, theme: ThemeId = 'midnight', unrelated = 0) => <ThemeContext.Provider value={{ theme, setTheme: () => {}, themeConfig: THEMES.find(t => t.id === theme)! }}><span>{unrelated}</span><Analytics residues={rows} highMax={highMax} minimalMin={1} /></ThemeContext.Provider>;
const series = () => capture.plots.slice(-7).map(p => p.data as any[]);

test('all seven figures preserve full numerical arrays, native slots and all 80 composition values', () => {
    const ui = render(view());
    expect(ui.getAllByTestId('plot')).toHaveLength(7);
    const m = buildFrustraMpnnPlotlyModel(residues);
    const [heat, native, distribution, envelope, burden, profile, composition] = series();
    expect(heat[0].z).toEqual(m.heatmapScores);
    expect(heat[0].z.flat()).toHaveLength(10800);
    expect(heat[0].customdata).toEqual(m.heatmapCustomData);
    expect(heat[0]).toMatchObject({ colorscale: 'RdBu', reversescale: true });
    expect(native[0].y).toEqual(m.nativeScores);
    expect(native[0].y).toHaveLength(540);
    distribution.forEach((trace, i) => expect(trace.y).toEqual(m.substitutionScores[AA[i]]));
    expect(envelope.map(t => t.y)).toEqual([m.bestAlternativeDeltas, m.worstAlternativeDeltas]);
    expect(burden.map(t => t.y)).toEqual([m.highAlternativeFractions, m.minimalAlternativeFractions]);
    expect(profile[0].x).toEqual(m.nativeScores);
    expect(profile[0].y).toEqual(m.medianAlternativeScores);
    expect(profile[0].marker.color).toEqual(m.highAlternativeFractions);
    expect(composition.flatMap(t => t.y)).toHaveLength(80);
    ['high', 'neutral', 'minimal', 'missing'].forEach((key, i) => expect(composition[i].y).toEqual(AA.map(aa => m.substitutionClassFractions[aa][key as 'high'])));
});

test('plain labels retain thresholds, export and one heading per chart', () => {
    const ui = render(view());
    expect(ui.container.textContent).not.toMatch(/Question:|Purpose:|backend|persisted|canonical|19 alternatives/i);
    expect(ui.getByRole('heading', { name: 'Frustration classes by amino acid' })).toBeTruthy();
    capture.plots.forEach(p => { expect(p.layout.title).not.toHaveProperty('text'); expect(p.config).toMatchObject({ scrollZoom: true, toImageButtonOptions: { format: 'png' } }); });
});

test('unrelated context and parent rerenders retain every figure prop and zoom range', () => {
    const ui = render(view());
    const before = capture.plots.slice(-7);
    // Plotly can mutate layout with a user zoom. Reusing it must not reset that state.
    before[0].layout.xaxis!.range = [10, 20];
    ui.rerender(view(residues, -1, 'midnight', 1));
    const after = capture.plots.slice(-7);
    after.forEach((p, i) => { expect(p.data).toBe(before[i].data); expect(p.layout).toBe(before[i].layout); expect(p.config).toBe(before[i].config); });
    expect(after[0].layout.xaxis!.range).toEqual([10, 20]);
});

test('threshold, theme and changed residue inputs refresh without stale figures', () => {
    const ui = render(view());
    const before = capture.plots.slice(-7);
    ui.rerender(view(residues, -2));
    expect(capture.plots.slice(-7)[1].layout.shapes![0].y0).toBe(-2);
    document.documentElement.style.setProperty('--bg-secondary', '#ffffff');
    ui.rerender(view(residues, -2, 'light'));
    capture.plots.slice(-7).forEach((p, i) => { expect(p.layout).not.toBe(before[i].layout); expect(p.layout.plot_bgcolor).toBe('#ffffff'); });
    const partial = structuredClone(residues.slice(0, 1));
    partial[0].slots[1] = { ...partial[0].slots[1], score: null, class: null, status: 'missing', scoreable: false };
    ui.rerender(view(partial, -2, 'light'));
    const m = buildFrustraMpnnPlotlyModel(partial);
    expect(series()[0][0].z).toEqual(m.heatmapScores);
    expect(series()[4][0].y).toEqual(m.highAlternativeFractions);
    expect(series()[5][0].y).toEqual(m.medianAlternativeScores);
    expect(ui.container.textContent).toContain('available scored non-native alternatives');
});
