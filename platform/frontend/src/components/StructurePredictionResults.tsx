import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data } from 'plotly.js';
import { useThemeColors } from './useThemeColors';
import './StructurePredictionResults.css';
import { fetchChainMetrics, fetchDesignResidueMetrics, fetchPAEData, type ChainMetric, type Design } from '../lib/api';
import { parseScientificNativeMetric, parseScientificPae, type NativePaeToken } from '../lib/scientificViewerIdentity';
import { scalarCell, usePredictionScalars, type ScalarEvidence } from './predictionScalarEvidence';
import type { MetricSelection } from '../structureViewer/metrics/metricContracts';
import type { AtomRef } from '../structureViewer/contracts/structureIdentity';
import { ConfidenceProfile, type ConfidenceChainMetric } from './ConfidenceProfile';

export type ConfidenceStructure = ReactNode | ((selection: readonly AtomRef[], onSelection: (selection: MetricSelection) => void, companion: ReactNode) => ReactNode);

export const isStandaloneStructurePrediction = (modelId?: string | null) =>
    ['protenix', 'boltz2', 'boltz_cp_experimental', 'esmfold2', 'esmfold2_experimental'].includes(modelId ?? '');
const asRecord = (value: unknown): Record<string, unknown> | null => value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const chainId = (ref: AtomRef) => ref.authAsymId ?? ref.labelAsymId ?? '';
const axisLabel = (ref: AtomRef) => `${chainId(ref)}:${ref.authSeqId ?? ref.labelSeqId}${ref.insertionCode ?? ''} ${ref.componentId ?? ''}${ref.labelAtomId ? ` / ${ref.labelAtomId}` : ''}`;
const tokenLabel = (token: NativePaeToken) => String(token.index);
const sparseTicks = (labels: string[]) => {
    const positions = [...new Set(Array.from({ length: Math.min(5, labels.length) }, (_, i) =>
        Math.round(i * (labels.length - 1) / Math.max(1, Math.min(5, labels.length) - 1))))];
    return { tickmode: 'array' as const, tickvals: positions, ticktext: positions.map(i => labels[i]), tickangle: 0 };
};
const residueTick = (ref: AtomRef) => `${chainId(ref)}:${ref.authSeqId ?? ref.labelSeqId}${ref.insertionCode ?? ''}`;

/** Display positions preserve native matrix order, including ligand atoms and unmapped axes. */
function PaePlot({ matrix, rowLabels, columnLabels, rowTicks = rowLabels, columnTicks = columnLabels,
    axisTitle, onInspect, compact = false }: {
    matrix: number[][]; rowLabels: string[]; columnLabels: string[];
    rowTicks?: string[]; columnTicks?: string[]; axisTitle: string;
    onInspect?: (row: number, column: number) => void; compact?: boolean;
}) {
    const colors = useThemeColors();
    return <div className="prediction-pae-plot"><Plot
        data={[{ type: 'heatmap', z: matrix, x: columnLabels.map((_, i) => i), y: rowLabels.map((_, i) => i),
            customdata: rowLabels.map(row => columnLabels.map(column => `Row ${row}<br>Column ${column}`)),
            zmin: 0, zmax: 30, zsmooth: false, colorscale: 'YlGnBu',
            colorbar: { title: { text: 'Å', side: 'top' }, thickness: 10, len: 0.85, tickvals: [0, 10, 20, 30], outlinewidth: 0, xpad: 5 },
            hovertemplate: '%{customdata}<br>PAE %{z} Å<extra></extra>' } as Data]}
        layout={{ autosize: true, paper_bgcolor: 'transparent', plot_bgcolor: 'transparent',
            font: { color: colors.textSecondary, size: 11 }, margin: { l: compact ? 46 : 58, r: 40, t: 26, b: compact ? 42 : 54 },
            xaxis: { type: 'linear', title: { text: axisTitle, standoff: 10 }, ...sparseTicks(columnTicks),
                constrain: 'domain', range: [-0.5, columnLabels.length - 0.5], showgrid: false, zeroline: false, automargin: true },
            yaxis: { type: 'linear', title: { text: axisTitle, standoff: 10 }, ...sparseTicks(rowTicks),
                range: [rowLabels.length - 0.5, -0.5], scaleanchor: 'x', constrain: 'domain', showgrid: false, zeroline: false, automargin: true },
            hoverlabel: { bgcolor: colors.bgSecondary, font: { color: colors.textPrimary } },
        }}
        onClick={event => {
            const point = event.points[0];
            if (typeof point?.x === 'number' && typeof point.y === 'number') onInspect?.(Math.round(point.y), Math.round(point.x));
        }}
        config={{ responsive: true, displayModeBar: 'hover', displaylogo: false, toImageButtonOptions: { format: 'svg', filename: 'predicted-aligned-error' } }}
        useResizeHandler style={{ width: '100%', height: '100%' }} />
    </div>;
}
const missingPae = (reason: string) => reason === 'full_pae_not_requested' || reason === 'pae_not_requested' ? 'Full PAE was not requested for this prediction.' : reason;
const protein = new Set(['ALA','ARG','ASN','ASP','CYS','GLN','GLU','GLY','HIS','ILE','LEU','LYS','MET','PHE','PRO','SER','THR','TRP','TYR','VAL']);

/** Explicit C-alpha selection for atom confidence; never an all-atom average. */
export function confidenceProfile(raw: unknown, design: Design): { chains: Record<string, ConfidenceChainMetric>; description: string; reason?: string } {
    const payload = asRecord(raw);
    if (payload?.schema_name === 'core_protein_viewer_metric' || (design.scientific_structure_document || design.core_protein_scientific_contract === 1)) {
        const native = parseScientificNativeMetric(raw, design.scientific_structure_document, 'residue_plddt', design.id);
        if (native.status !== 'ok') return { chains: {}, description: 'Native confidence', reason: native.reason };
        const chains: Record<string, ConfidenceChainMetric> = {};
        native.residues.forEach((ref, i) => {
            if (native.metric === 'atom_plddt' && ((ref.labelAtomId ?? ref.authAtomId) !== 'CA' || !protein.has(ref.componentId ?? ''))) return;
            const id = chainId(ref), position = ref.authSeqId ?? ref.labelSeqId;
            if (position == null) return;
            const chain = chains[id] ??= { type: protein.has(ref.componentId ?? '') ? 'protein' : 'unknown', length: 0, avg_plddt: null, plddt: [], residue_numbers: [], positionLabels: [], identities: [] };
            chain.identities!.push(ref);
            chain.plddt.push(native.values[i] * 100);
            chain.residue_numbers.push(position);
            chain.positionLabels!.push(`${position}${ref.insertionCode ?? ''}`);
            chain.length++;
        });
        for (const chain of Object.values(chains)) chain.avg_plddt = chain.plddt.reduce((a, b) => a + b, 0) / chain.length;
        return { chains, description: native.metric === 'atom_plddt' ? 'Protein Cα-only confidence profile (0–100); no atom averaging. Native atom and non-protein evidence is available in Structure → Metrics & tools.' : 'Confidence by residue. Higher is better.' };
    }
    return { chains: {}, description: 'Legacy per-chain confidence' };
}
function legacyChains(raw: unknown): Record<string, ChainMetric> {
    const entries = Object.entries(asRecord(raw) ?? {});
    return Object.fromEntries(entries.filter((entry): entry is [string, ChainMetric] => {
        const v = asRecord(entry[1]);
        return !!v && Array.isArray(v.plddt) && Array.isArray(v.residue_numbers) && v.plddt.length === v.residue_numbers.length && v.plddt.every(n => typeof n === 'number' && Number.isFinite(n));
    }));
}

function PredictionEvidence({ design, structure, fullPaeRequested, scalars }: { design: Design; structure?: ConfidenceStructure; fullPaeRequested?: boolean; scalars: ScalarEvidence }) {
    const [inspection, setInspection] = useState<readonly AtomRef[]>([]);
    const acceptSelection = (selection: MetricSelection) => setInspection(selection.identities.flatMap(identity => {
        if ('first' in identity && 'second' in identity) return [identity.first, identity.second] as AtomRef[];
        return 'authSeqId' in identity || 'labelSeqId' in identity ? [identity as AtomRef] : [];
    }));
    const [rowChain, setRowChain] = useState('');
    const [columnChain, setColumnChain] = useState('');
    const residue = useQuery({ queryKey: ['prediction-residue-confidence', design.id], queryFn: () => fetchDesignResidueMetrics(design.id).then(r => r.data), retry: false });
    const chain = useQuery({ queryKey: ['prediction-chain-confidence', design.id], queryFn: () => fetchChainMetrics(design.id).then(r => r.data), retry: false });
    const pae = useQuery({ queryKey: ['prediction-pae', design.id], queryFn: () => fetchPAEData(design.id).then(r => r.data), retry: false });
    const profile = confidenceProfile(residue.data, design);
    const bound = design.scientific_structure_document || design.core_protein_scientific_contract === 1 || asRecord(residue.data)?.schema_name;
    let chains: Record<string, ConfidenceChainMetric> = bound ? profile.chains : legacyChains(chain.data);
    const legacyResidue = !bound ? asRecord(residue.data) : null;
    if (!bound && !Object.keys(chains).length && legacyResidue?.design_id === design.id) {
        const unmapped = legacyChains({ unmapped: { ...legacyResidue, type: 'unknown', avg_plddt: null } });
        if (unmapped.unmapped) chains = { unmapped: { ...unmapped.unmapped, label: 'Legacy profile (chain mapping unavailable)' } };
    }
    const nativePae = parseScientificPae(pae.data, design.scientific_structure_document, design.id);
    const nativeChain = parseScientificNativeMetric(chain.data, design.scientific_structure_document, 'chain_metrics', design.id);
    const legacy = !design.scientific_structure_document && design.core_protein_scientific_contract !== 1 && !asRecord(pae.data)?.schema_name ? asRecord(pae.data) : null;
    const legacyMatrix = Array.isArray(legacy?.pae_matrix) && legacy.pae_matrix.every(r => Array.isArray(r) && r.every(v => typeof v === 'number' && Number.isFinite(v))) ? legacy.pae_matrix as number[][] : null;
    const tokenPae = nativePae.status === 'ok' && nativePae.axisKind === 'model_token' ? nativePae : null;
    const matrix = nativePae.status === 'ok' ? nativePae.matrix : legacyMatrix;
    const rows = nativePae.status === 'ok' ? nativePae.rows : [];
    const columns = nativePae.status === 'ok' ? nativePae.columns : [];
    const ri = matrix?.map((_, i) => i).filter(i => tokenPae || !rowChain || chainId(rows[i]) === rowChain) ?? [];
    const ci = matrix?.[0]?.map((_, i) => i).filter(i => tokenPae || !columnChain || chainId(columns[i]) === columnChain) ?? [];
    const axisTitle = tokenPae || !rows.length ? 'Prediction position' : rows.some(ref => ref.labelAtomId || ref.authAtomId) ? 'Residue / atom' : 'Residue';
    const charts = <div className="prediction-charts">
                <section aria-label="Predicted Aligned Error" className="prediction-card">
                    <h3>Predicted aligned error</h3>
                    <p className="prediction-caption">Directed expected position error in Å. Lower is better; row and column identities retain native orientation. Colors saturate above 30 Å; hover retains the exact value.</p>
                    {rows.length > 0 && <div className="prediction-chain-controls">{(['Row', 'Column'] as const).map((name, i) => <label key={name}><span>{name} chain</span><select aria-label={`${name} chain`} value={i ? columnChain : rowChain} onChange={e => (i ? setColumnChain : setRowChain)(e.target.value)}><option value="">All</option>{[...new Set((i ? columns : rows).map(chainId))].map(id => <option key={id}>{id}</option>)}</select></label>)}</div>}
                    {matrix ? <PaePlot matrix={ri.map(r => ci.map(c => matrix[r][c]))}
                        rowLabels={ri.map(i => tokenPae ? tokenLabel(tokenPae.rowTokens[i]) : rows[i] ? axisLabel(rows[i]) : String(i))}
                        columnLabels={ci.map(i => tokenPae ? tokenLabel(tokenPae.columnTokens[i]) : columns[i] ? axisLabel(columns[i]) : String(i))}
                        rowTicks={ri.map(i => tokenPae ? tokenLabel(tokenPae.rowTokens[i]) : rows[i] ? residueTick(rows[i]) : String(i))}
                        columnTicks={ci.map(i => tokenPae ? tokenLabel(tokenPae.columnTokens[i]) : columns[i] ? residueTick(columns[i]) : String(i))}
                        axisTitle={axisTitle} onInspect={tokenPae ? undefined : (r, c) => {
                            const row = rows[ri[r]], column = columns[ci[c]];
                            if (row && column) setInspection([row, column]);
                        }} /> : <p role="status" className="py-8">{pae.isPending ? 'Loading PAE…' : pae.isError ? 'PAE request failed. Structure and other scores remain available.' : fullPaeRequested === false && nativePae.status === 'unavailable' && ['full_pae_not_requested', 'pae_not_requested'].includes(nativePae.reason) ? 'Full PAE was not requested for this prediction.' : nativePae.status === 'unavailable' ? missingPae(nativePae.reason) : 'Full PAE not retained.'}</p>}
                    {tokenPae && <p className="prediction-caption">Zero-based prediction positions; residue mapping unavailable.</p>}
                </section>
                <section aria-label="Confidence profile" className="prediction-card">
                    <h3>pLDDT by residue</h3><p className="prediction-caption">{chains.unmapped ? 'Residue confidence; chain mapping unavailable.' : profile.description}</p>
                    {Object.keys(chains).length ? <ConfidenceProfile chainMetrics={chains} onInspect={ref => setInspection([ref])} /> : <p role="status">{residue.isPending || chain.isPending ? 'Loading confidence…' : profile.reason ?? 'Per-chain confidence was not retained.'}</p>}
                </section>
            </div>;
    return <>
        <dl aria-label="Native confidence summary" className="prediction-summary">{scalars.entries.map(entry => <div key={entry.key}><dt>{entry.label}</dt><dd title={entry.title}>{entry.display}</dd></div>)}{scalars.reason && <div role="status">{scalars.reason}</div>}</dl>
        <div className="prediction-workspace">
            {structure ? <section aria-label="Selected prediction structure" className="prediction-structure">{typeof structure === 'function' ? structure(inspection, acceptSelection, charts) : structure}</section> : charts}
        </div>
        {inspection.length > 0 && <div aria-label="Confidence selection" className="text-sm">{inspection.map(axisLabel).join(' × ')} <button onClick={() => setInspection([])}>Clear inspection</button></div>}
        {nativeChain.status === 'ok' && <details><summary>Native chain-pair iPTM</summary><p>Directional entries are retained as published; no symmetrization. Diagonal entries describe one chain, not an inter-chain binding grade.</p><table><thead><tr><th>Row chain</th><th>Column chain</th><th>iPTM</th><th>Inspect</th></tr></thead><tbody>{nativeChain.chains.flatMap(a => nativeChain.chains.map(b => <tr key={`${a.providerIndex}:${b.providerIndex}`}><td>{a.chainId}</td><td>{b.chainId}</td><td>{nativeChain.pairChainsIptm[a.providerIndex][b.providerIndex]}</td><td><button onClick={() => { setRowChain(a.chainId); setColumnChain(b.chainId); setInspection([...a.residues, ...b.residues]); }}>PAE block</button></td></tr>))}</tbody></table></details>}
        <details><summary>Raw confidence details</summary><pre className="max-h-80 overflow-auto text-xs">{JSON.stringify({ residue: residue.data, chains: chain.data, ...(tokenPae ? { paeAxes: { row: tokenPae.rowAxis, column: tokenPae.columnAxis, nativeShape: tokenPae.nativeShape, sampledRows: tokenPae.sampledRowIndices, sampledColumns: tokenPae.sampledColumnIndices } } : {}) }, null, 2)}</pre></details>
    </>;
}

function PaeComparison({ design, onSelect, fullPaeRequested }: { design: Design; onSelect: () => void; fullPaeRequested?: boolean }) {
    const query = useQuery({ queryKey: ['prediction-pae', design.id], queryFn: () => fetchPAEData(design.id).then(r => r.data), retry: false, staleTime: 60_000 });
    const native = parseScientificPae(query.data, design.scientific_structure_document, design.id);
    return <article className="prediction-card">
        <button className="prediction-sample-link" onClick={onSelect}>{design.name}</button>
        {native.status === 'ok' ? <PaePlot matrix={native.matrix}
            rowLabels={native.axisKind === 'model_token' ? native.rowTokens.map(tokenLabel) : native.rows.map(axisLabel)}
            columnLabels={native.axisKind === 'model_token' ? native.columnTokens.map(tokenLabel) : native.columns.map(axisLabel)}
            rowTicks={native.axisKind === 'model_token' ? native.rowTokens.map(tokenLabel) : native.rows.map(residueTick)}
            columnTicks={native.axisKind === 'model_token' ? native.columnTokens.map(tokenLabel) : native.columns.map(residueTick)}
            axisTitle={native.axisKind === 'model_token' ? 'Prediction position' : 'Residue / atom'} compact /> : <p className="prediction-caption">{query.isPending ? 'Loading…' : query.isError ? 'PAE request failed.' : fullPaeRequested === false && ['full_pae_not_requested', 'pae_not_requested'].includes(native.reason) ? 'Full PAE was not requested.' : missingPae(native.reason)}</p>}
        {native.status === 'ok' && native.axisKind === 'model_token' && <p className="prediction-caption">Residue mapping unavailable.</p>}
    </article>;
}

export function StructurePredictionResults({ designs, selectedDesignId, onSelectDesign, structure, enabled = true, fullPaeRequested, modelId, summaryOnly = false }: { summaryOnly?: boolean; modelId?: string | null; fullPaeRequested?: boolean; enabled?: boolean; designs: Design[]; selectedDesignId?: string | null; onSelectDesign?: (id: string) => void; structure?: ConfidenceStructure }) {
    const [localId, setLocalId] = useState('');
    const [compare, setCompare] = useState(false);
    const scalarsFor = usePredictionScalars(designs, enabled);
    const design = selectedDesignId ? designs.find(d => d.id === selectedDesignId) : designs.find(d => d.id === localId) ?? designs[0];
    if (!enabled) return <>{typeof structure === 'function' ? structure([], () => {}, null) : structure}</>;
    if (!design) return <p>No saved predictions loaded.</p>;
    const select = onSelectDesign ?? setLocalId;
    const labels: Record<string, string> = { protenix: 'Protenix', boltz2: 'Boltz-2', boltz_cp_experimental: 'Boltz-2 via Fold-CP', esmfold2: 'Biohub ESMFold2', esmfold2_experimental: 'Biohub ESMFold2' };
    const modelLabel = labels[modelId ?? String(design.provenance?.producer_model_id ?? design.provenance?.model_id ?? '')] ?? 'Structure prediction';
    return <section aria-label="Structure prediction confidence" className="prediction-results space-y-4 text-[var(--text-primary)]">
        <h2 className="text-lg font-semibold">{modelLabel} confidence</h2>
        {modelId === 'protenix' && <p className="prediction-caption">Producer sample rank is not generation order. The producer disorder field is a placeholder, not a measured disorder result.</p>}
        <p className="prediction-caption">pLDDT uses the native 0–100 confidence scale, not a probability of correctness. iPTM is an interface-placement score, not a binding grade; monomer values do not establish binding.</p>
        <label className="prediction-selector"><span>Selected prediction</span><select aria-label="Selected prediction" value={design.id} onChange={e => select(e.target.value)}>{designs.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        {summaryOnly ? <dl aria-label="Native confidence summary" className="prediction-summary">{scalarsFor(design).entries.map(entry => <div key={entry.key}><dt>{entry.label}</dt><dd title={entry.title}>{entry.display}</dd></div>)}{scalarsFor(design).reason && <p role="status">{scalarsFor(design).reason}</p>}</dl> : <PredictionEvidence key={design.id} design={design} structure={structure} fullPaeRequested={fullPaeRequested} scalars={scalarsFor(design)} />}
        {!summaryOnly && <details onToggle={event => setCompare(event.currentTarget.open)}><summary>Compare native PAE — fixed 0–30 Å scale</summary>
            <p className="text-xs">Same 0–30 Å scale for every prediction; each matrix keeps its original order.</p>
            {compare && <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">{designs.map(d => <PaeComparison key={d.id} fullPaeRequested={fullPaeRequested} design={d} onSelect={() => select(d.id)} />)}</div>}
        </details>}
        <details open><summary>Saved predictions ({designs.length})</summary><p className="text-xs">Saved sample identities, not a new ranking. Missing scores are not zero. pLDDT is not evidence of correct inter-chain placement.</p><div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr><th>Prediction</th><th>pLDDT (0–100)</th><th>pTM</th><th>iPTM</th></tr></thead><tbody>{designs.map(d => <tr key={d.id} aria-selected={design.id === d.id}><td><button onClick={() => select(d.id)}>{d.name}</button></td>{scalarCell(scalarsFor(d), ['complex_plddt', 'plddt', 'plddt_mean', 'plddt_overall'])}{scalarCell(scalarsFor(d), ['ptm'])}{scalarCell(scalarsFor(d), ['iptm'])}</tr>)}</tbody></table></div></details>
    </section>;
}
