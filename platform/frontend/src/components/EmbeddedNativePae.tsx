import { lazy, Suspense } from 'react';
import type { ScientificPae } from '../lib/scientificViewerIdentity';
import { comparisonLabel } from './nativeComparisonProfiles';
import { useThemeColors } from './useThemeColors';
const Plot = lazy(() => import('react-plotly.js'));

/** Same Plotly native heatmap style as standalone PAE; numeric display indices
 * never collapse distinct atom tokens onto a categorical residue label. */
export function EmbeddedNativePae({ pae }: { pae: Extract<ScientificPae, { status: 'ok' }> }) {
    const colors = useThemeColors();
    const rows = pae.axisKind === 'model_token' ? pae.rowTokens.map(t => `Native token ${t.index}`) : pae.rows.map(comparisonLabel);
    const columns = pae.axisKind === 'model_token' ? pae.columnTokens.map(t => `Native token ${t.index}`) : pae.columns.map(comparisonLabel);
    const ticks = (n: number) => [...new Set(Array.from({ length: Math.min(5, n) }, (_, i) => Math.round(i * (n - 1) / Math.max(1, Math.min(5, n) - 1))))];
    return <div className="w-full max-w-[440px]" style={{ aspectRatio: '1' }}><Suspense fallback={<p>Loading PAE chart…</p>}><Plot
        data={[{ type: 'heatmap', z: pae.matrix, x: columns.map((_, i) => i), y: rows.map((_, i) => i),
            customdata: rows.map((row, i) => columns.map((column, j) => `Row ${i}: ${row}<br>Column ${j}: ${column}`)),
            zmin: 0, zmax: 30, zsmooth: false, colorscale: 'YlGnBu',
            colorbar: { title: { text: 'Å', side: 'top' }, thickness: 10, tickvals: [0, 10, 20, 30], outlinewidth: 0 },
            hovertemplate: '%{customdata}<br>PAE %{z:.2f} Å<extra></extra>' }]}
        layout={{ autosize: true, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: colors.textSecondary, size: 11 }, margin: { t: 20, l: 65, b: 60, r: 45 },
            xaxis: { type: 'linear', title: { text: 'Column index (scored)' }, tickmode: 'array', tickvals: ticks(columns.length), range: [-0.5, columns.length - 0.5], constrain: 'domain', automargin: true },
            yaxis: { type: 'linear', title: { text: 'Row index (aligned)' }, tickmode: 'array', tickvals: ticks(rows.length), range: [rows.length - 0.5, -0.5], scaleanchor: 'x', constrain: 'domain', automargin: true },
            hoverlabel: { bgcolor: colors.bgSecondary, font: { color: colors.textPrimary } } }}
        config={{ responsive: true, displaylogo: false, toImageButtonOptions: { format: 'svg', filename: `${pae.document.candidateId}-pae` } }}
        useResizeHandler style={{ width: '100%', height: '100%' }} /> </Suspense></div>;
}
