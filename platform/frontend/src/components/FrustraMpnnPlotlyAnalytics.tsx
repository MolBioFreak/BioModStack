import { useMemo } from 'react';
import Plot from 'react-plotly.js';
import type { Data, Datum, Layout } from 'plotly.js';
import {
    CANONICAL_AMINO_ACIDS,
    type CmLandscapeResidue,
} from './conformationalMapping/conformationalMappingSemantics.js';
import { useThemeColors, useThemePlotlyLayout } from './useThemeColors.js';
import { buildFrustraMpnnPlotlyModel } from './frustraMpnnPlotlyModel.js';

const PLOT_CONFIG = {
    responsive: true,
    displaylogo: false,
    scrollZoom: true,
    modeBarButtonsToRemove: ['lasso2d', 'select2d'] as never[],
    toImageButtonOptions: { format: 'png' as const, filename: 'frustrampnn-landscape' },
};

export default function FrustraMpnnPlotlyAnalytics({
    residues,
    highMax,
    minimalMin,
}: {
    residues: CmLandscapeResidue[];
    highMax: number;
    minimalMin: number;
}) {
    const model = useMemo(() => buildFrustraMpnnPlotlyModel(residues), [residues]);
    const colors = useThemeColors();
    const baseLayout = useThemePlotlyLayout();
    const nativeMarkerColors = model.nativeClasses.map((className) => (
        className === 'high' ? '#ef4444' : className === 'minimal' ? '#06b6d4' : className === 'neutral' ? '#f59e0b' : '#475569'
    ));
    const thresholdShapes: Layout['shapes'] = [
        { type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: highMax, y1: highMax, line: { color: '#ef4444', width: 1.5, dash: 'dash' } },
        { type: 'line', xref: 'paper', x0: 0, x1: 1, yref: 'y', y0: minimalMin, y1: minimalMin, line: { color: '#06b6d4', width: 1.5, dash: 'dash' } },
    ];
    const commonLayout: Partial<Layout> = {
        ...baseLayout,
        margin: { l: 58, r: 24, t: 42, b: 58 },
        autosize: true,
        hovermode: 'closest',
    };

    const heatmapData: Data[] = [{
        type: 'heatmap',
        x: model.residueLabels,
        y: [...CANONICAL_AMINO_ACIDS],
        z: model.heatmapScores,
        customdata: model.heatmapCustomData as unknown as Datum[][],
        colorscale: 'RdBu',
        reversescale: true,
        colorbar: { title: { text: 'FrustraMPNN score' } },
        hovertemplate: 'Residue %{x}<br>WT %{customdata[0]} → %{y}<br>Score %{z:.3f}<br>Class %{customdata[1]}<br>Status %{customdata[2]}<br>%{customdata[3]}<extra></extra>',
        zsmooth: false,
    }];
    const nativeData: Data[] = [{
        type: 'scatter',
        mode: 'lines+markers',
        x: model.residueLabels,
        y: model.nativeScores,
        customdata: model.nativeClasses,
        line: { color: colors.textMuted, width: 1 },
        marker: { color: nativeMarkerColors, size: 5 },
        hovertemplate: 'Residue %{x}<br>Native score %{y:.3f}<br>Class %{customdata}<extra></extra>',
        name: 'Native score',
    }];
    const distributionData: Data[] = CANONICAL_AMINO_ACIDS.map((aa) => ({
        type: 'box',
        name: aa,
        y: model.substitutionScores[aa],
        boxpoints: 'outliers',
        boxmean: true,
        marker: { size: 3 },
        hovertemplate: `${aa} substitution<br>Score %{y:.3f}<extra></extra>`,
    }));
    const alternativeEnvelopeData: Data[] = [
        {
            type: 'scatter', mode: 'lines', name: 'Best alternative Δ', x: model.residueLabels, y: model.bestAlternativeDeltas,
            line: { color: '#06b6d4', width: 1.5 }, hovertemplate: 'Residue %{x}<br>Best non-native Δ %{y:.3f}<extra></extra>',
        },
        {
            type: 'scatter', mode: 'lines', name: 'Worst alternative Δ', x: model.residueLabels, y: model.worstAlternativeDeltas,
            line: { color: '#ef4444', width: 1.5 }, hovertemplate: 'Residue %{x}<br>Worst non-native Δ %{y:.3f}<extra></extra>',
        },
    ];
    const burdenData: Data[] = [
        {
            type: 'scatter', mode: 'lines', name: 'Highly frustrated alternatives', x: model.residueLabels, y: model.highAlternativeFractions,
            line: { color: '#ef4444', width: 1.5 }, hovertemplate: 'Residue %{x}<br>Highly frustrated alternatives %{y:.1%}<extra></extra>',
        },
        {
            type: 'scatter', mode: 'lines', name: 'Minimally frustrated alternatives', x: model.residueLabels, y: model.minimalAlternativeFractions,
            line: { color: '#06b6d4', width: 1.5 }, hovertemplate: 'Residue %{x}<br>Minimally frustrated alternatives %{y:.1%}<extra></extra>',
        },
    ];
    const nativeVsAlternativeData: Data[] = [{
        type: 'scatter',
        mode: 'markers',
        x: model.nativeScores,
        y: model.medianAlternativeScores,
        customdata: model.residueLabels,
        marker: { color: model.highAlternativeFractions, colorscale: 'YlOrRd', size: 7, opacity: 0.75, colorbar: { title: { text: 'High-alt fraction' } }, cmin: 0, cmax: 1 },
        hovertemplate: 'Residue %{customdata}<br>Native %{x:.3f}<br>Median non-native %{y:.3f}<br>High-alt fraction %{marker.color:.1%}<extra></extra>',
        name: 'Residues',
    }];
    const compositionData: Data[] = ([
        ['high', 'Highly frustrated', '#ef4444'],
        ['neutral', 'Neutral', '#f59e0b'],
        ['minimal', 'Minimally frustrated', '#06b6d4'],
        ['missing', 'Missing', '#475569'],
    ] as const).map(([key, label, color]) => ({
        type: 'bar',
        name: label,
        x: [...CANONICAL_AMINO_ACIDS],
        y: CANONICAL_AMINO_ACIDS.map((aa) => model.substitutionClassFractions[aa][key]),
        marker: { color },
        hovertemplate: `${label}<br>Mutation %{x}<br>Fraction %{y:.1%}<extra></extra>`,
    }));

    return (
        <section aria-label="FrustraMPNN Plotly visual analytics" className="overflow-hidden rounded-xl border border-slate-800 bg-slate-900/60">
            <div className="border-b border-slate-800 p-4">
                <h2 className="font-semibold">FrustraMPNN visual analytics</h2>
                <p className="mt-1 text-xs text-slate-400">{model.residueLabels.length.toLocaleString()} residues · 20 amino acids per residue. Zoom, pan, hover, or export as PNG.</p>
                <p className="mt-2 text-xs text-slate-400">Highly frustrated ≤ {highMax} · minimally frustrated ≥ {minimalMin}</p>
            </div>
            <div className="grid gap-3 p-3 xl:grid-cols-2">
                <article className="rounded-lg border border-slate-800 bg-slate-950/40 p-2 xl:col-span-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Complete score heatmap</h3>
                    <p className="px-2 text-xs text-slate-500">Drag to zoom; double-click to reset.</p>
                    <Plot data={heatmapData} layout={{ ...commonLayout, height: 460, title: { text: 'All residue × substitution scores' }, xaxis: { ...baseLayout.xaxis, title: { text: 'Author residue (chain:number+insertion)' }, nticks: 24 }, yaxis: { ...baseLayout.yaxis, title: { text: 'Mutation amino acid' }, autorange: 'reversed' } }} config={PLOT_CONFIG} className="h-[460px] w-full" useResizeHandler />
                </article>
                <article className="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Native frustration along sequence</h3>
                    <p className="px-2 text-xs text-slate-500">Dashed lines mark frustration thresholds.</p>
                    <Plot data={nativeData} layout={{ ...commonLayout, height: 390, title: { text: 'Native score by residue' }, shapes: thresholdShapes, xaxis: { ...baseLayout.xaxis, title: { text: 'Author residue' }, nticks: 16 }, yaxis: { ...baseLayout.yaxis, title: { text: 'FrustraMPNN score' } }, showlegend: false }} config={PLOT_CONFIG} className="h-[390px] w-full" useResizeHandler />
                </article>
                <article className="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Substitution score distributions</h3>
                    <p className="px-2 text-xs text-slate-500">Boxes show median and quartiles; points show outliers.</p>
                    <Plot data={distributionData} layout={{ ...commonLayout, height: 390, title: { text: 'Score distribution by mutation amino acid' }, xaxis: { ...baseLayout.xaxis, title: { text: 'Mutation amino acid' } }, yaxis: { ...baseLayout.yaxis, title: { text: 'FrustraMPNN score' } }, showlegend: false }} config={PLOT_CONFIG} className="h-[390px] w-full" useResizeHandler />
                </article>
                <article className="rounded-lg border border-slate-800 bg-slate-950/40 p-2 xl:col-span-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Alternative score range along sequence</h3>
                    <p className="px-2 text-xs text-slate-500">Δ = alternative score − native score; not a redesign recommendation.</p>
                    <Plot data={alternativeEnvelopeData} layout={{ ...commonLayout, height: 390, title: { text: 'Best and worst non-native score deltas' }, xaxis: { ...baseLayout.xaxis, title: { text: 'Author residue' }, nticks: 24 }, yaxis: { ...baseLayout.yaxis, title: { text: 'Alternative − native score' }, zeroline: true, zerolinecolor: colors.textMuted }, legend: { orientation: 'h', y: 1.12 } }} config={PLOT_CONFIG} className="h-[390px] w-full" useResizeHandler />
                </article>
                <article className="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Frustration among alternatives</h3>
                    <p className="px-2 text-xs text-slate-500">Fractions include only available non-native scores.</p>
                    <Plot data={burdenData} layout={{ ...commonLayout, height: 390, title: { text: 'Frustration among non-native substitutions' }, xaxis: { ...baseLayout.xaxis, title: { text: 'Author residue' }, nticks: 16 }, yaxis: { ...baseLayout.yaxis, title: { text: 'Fraction of available alternatives' }, tickformat: '.0%', range: [0, 1] }, legend: { orientation: 'h', y: 1.14 } }} config={PLOT_CONFIG} className="h-[390px] w-full" useResizeHandler />
                </article>
                <article className="rounded-lg border border-slate-800 bg-slate-950/40 p-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Native versus alternative profile</h3>
                    <p className="px-2 text-xs text-slate-500">Each point is a residue; color shows its highly frustrated alternative fraction.</p>
                    <Plot data={nativeVsAlternativeData} layout={{ ...commonLayout, height: 390, title: { text: 'Native score vs median non-native score' }, xaxis: { ...baseLayout.xaxis, title: { text: 'Native score' } }, yaxis: { ...baseLayout.yaxis, title: { text: 'Median score across 19 alternatives' } }, showlegend: false }} config={PLOT_CONFIG} className="h-[390px] w-full" useResizeHandler />
                </article>
                <article className="rounded-lg border border-slate-800 bg-slate-950/40 p-2 xl:col-span-2">
                    <h3 className="px-2 pt-2 text-sm font-medium">Frustration by amino acid</h3>
                    <p className="px-2 text-xs text-slate-500">Class fractions across all residues; missing scores are shown separately.</p>
                    <Plot data={compositionData} layout={{ ...commonLayout, height: 390, title: { text: 'Frustration by amino acid' }, barmode: 'stack', xaxis: { ...baseLayout.xaxis, title: { text: 'Mutation amino acid' } }, yaxis: { ...baseLayout.yaxis, title: { text: 'Fraction of residues' }, tickformat: '.0%', range: [0, 1] }, legend: { orientation: 'h', y: 1.13 } }} config={PLOT_CONFIG} className="h-[390px] w-full" useResizeHandler />
                </article>
            </div>
        </section>
    );
}
