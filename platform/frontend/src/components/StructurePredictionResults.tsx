import { useState, type ReactNode } from 'react';
import { useQuery } from '@tanstack/react-query';
import Plot from 'react-plotly.js';
import type { Data } from 'plotly.js';
import { fetchChainMetrics, fetchDesignResidueMetrics, fetchPAEData, type ChainMetric, type Design } from '../lib/api';
import { parseScientificNativeMetric, parseScientificPae, type NativePaeToken } from '../lib/scientificViewerIdentity';
import type { MetricSelection } from '../structureViewer/metrics/metricContracts';
import type { AtomRef } from '../structureViewer/contracts/structureIdentity';
import { ConfidenceProfile, type ConfidenceChainMetric } from './ConfidenceProfile';

export type ConfidenceStructure = ReactNode | ((selection: readonly AtomRef[], onSelection: (selection: MetricSelection) => void) => ReactNode);

export const isStandaloneStructurePrediction = (modelId?: string | null) =>
    ['protenix', 'boltz2', 'boltz_cp_experimental', 'esmfold2', 'esmfold2_experimental'].includes(modelId ?? '');
const asRecord = (value: unknown): Record<string, unknown> | null => value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const chainId = (ref: AtomRef) => ref.authAsymId ?? ref.labelAsymId ?? '';
const axisLabel = (ref: AtomRef) => `${chainId(ref)}:${ref.authSeqId ?? ref.labelSeqId}${ref.insertionCode ?? ''} ${ref.componentId ?? ''}${ref.labelAtomId ? ` / ${ref.labelAtomId}` : ''}`;
const tokenLabel = (token: NativePaeToken) => `Token ${token.index}`;
const nativeSummaryKeys: Record<string, string> = { plddt_mean: 'Native token-mean pLDDT (fraction)', plddt: 'Native summary pLDDT (0–100)', complex_plddt: 'Complex pLDDT (fraction)', complex_iplddt: 'Interface pLDDT (fraction)', ptm: 'pTM', iptm: 'iPTM', ranking_score: 'Producer ranking score', confidence_score: 'Producer confidence score', gpde: 'gPDE (Å)', complex_pde: 'Complex PDE (Å)', complex_ipde: 'Interface PDE (Å)' };
const nativeSummary = (design: Design) => Object.entries(asRecord(design.confidence_metrics) ?? {}).filter(([key, value]) => key in nativeSummaryKeys && typeof value === 'number' && Number.isFinite(value)) as [string, number][];
const chainTicks = (refs: AtomRef[]) => {
    const blocks: { id: string; start: number; end: number }[] = [];
    refs.forEach((ref, i) => {
        const id = chainId(ref), last = blocks[blocks.length - 1];
        if (last?.id === id) last.end = i;
        else blocks.push({ id, start: i, end: i });
    });
    return { tickmode: 'array' as const, tickvals: blocks.map(b => axisLabel(refs[Math.floor((b.start + b.end) / 2)])), ticktext: blocks.map(b => b.id), showticklabels: refs.length > 0 };
};
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
        return { chains, description: native.metric === 'atom_plddt' ? 'Native Cα atom pLDDT (fraction displayed as 0–100); no atom averaging.' : native.confidenceSource ? 'Native collapsed-CIF-residue pLDDT (stored percent; transported fraction displayed as 0–100). Not a token-mean summary.' : 'Native residue pLDDT (fraction displayed as 0–100). Not a token-mean summary.' };
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

function PredictionEvidence({ design, structure, fullPaeRequested }: { design: Design; structure?: ConfidenceStructure; fullPaeRequested?: boolean }) {
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
    return <>
        <dl aria-label="Native confidence summary" className="flex flex-wrap gap-4">{nativeSummary(design).map(([key, value]) => <div key={key}><dt className="text-xs text-[var(--text-secondary)]">{nativeSummaryKeys[key]}</dt><dd className="font-mono" title={String(value)}>{value.toPrecision(4)}</dd></div>)}</dl>
        <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
            <section aria-label="Selected prediction structure" className="min-w-0">{typeof structure === 'function' ? structure(inspection, acceptSelection) : structure}</section>
            <section aria-label="Predicted Aligned Error" className="min-w-0 rounded-xl border border-[var(--border-color)] p-3">
                <h3>Predicted Aligned Error (Å)</h3>
                <p className="text-xs text-[var(--text-secondary)]">Native row/column order; token axes may include ligand atoms. Fixed 0–30 Å color scale.</p>
                {tokenPae && <p className="text-xs">Native model-token indices (zero-based); structure mapping unavailable. Orientation: {tokenPae.rowAxis.orientation}. Mapping evidence: {tokenPae.rowAxis.mapping_reason}. No residue or chain selections.</p>}
                {rows.length > 0 && <div className="flex flex-wrap gap-3 py-2">{(['Row', 'Column'] as const).map((name, i) => <label key={name}>{name} chain <select aria-label={`${name} chain`} value={i ? columnChain : rowChain} onChange={e => (i ? setColumnChain : setRowChain)(e.target.value)}><option value="">All</option>{[...new Set((i ? columns : rows).map(chainId))].map(id => <option key={id}>{id}</option>)}</select></label>)}</div>}
                {matrix ? <Plot onClick={event => {
                    if (tokenPae) return;
                    const point = event.points[0];
                    const row = rows.find(ref => axisLabel(ref) === point?.y);
                    const column = columns.find(ref => axisLabel(ref) === point?.x);
                    if (row && column) setInspection([row, column]);
                }} data={[{ type: 'heatmap', z: ri.map(r => ci.map(c => matrix[r][c])), x: ci.map(i => tokenPae ? tokenLabel(tokenPae.columnTokens[i]) : columns[i] ? axisLabel(columns[i]) : String(i)), y: ri.map(i => tokenPae ? tokenLabel(tokenPae.rowTokens[i]) : rows[i] ? axisLabel(rows[i]) : String(i)), zmin: 0, zmax: 30, colorscale: 'YlGnBu', colorbar: { title: { text: 'PAE (Å)' } }, hovertemplate: 'Column %{x}<br>Row %{y}<br>PAE %{z:.2f} Å<extra></extra>' } as Data]} layout={{ paper_bgcolor: 'transparent', plot_bgcolor: 'transparent', font: { color: '#94a3b8' }, margin: { l: 65, r: 65, t: 20, b: 65 }, xaxis: { title: { text: rows.length ? 'Native column token' : 'Native column index (mapping unavailable)' }, type: 'category', constrain: 'domain', ...(tokenPae ? { showticklabels: true } : chainTicks(ci.flatMap(i => columns[i] ? [columns[i]] : []))) }, yaxis: { title: { text: rows.length ? 'Native row token' : 'Native row index' }, type: 'category', autorange: 'reversed', scaleanchor: 'x', constrain: 'domain', ...(tokenPae ? { showticklabels: true } : chainTicks(ri.flatMap(i => rows[i] ? [rows[i]] : []))) } }} config={{ responsive: true, displayModeBar: true, toImageButtonOptions: { format: 'svg' } }} style={{ width: '100%', aspectRatio: '1 / 1' }} /> : <p role="status" className="py-10">{pae.isPending ? 'Loading PAE…' : fullPaeRequested === false ? 'Full PAE was not requested for this prediction.' : pae.isError ? 'PAE request failed. Structure and other scores remain available.' : nativePae.status === 'unavailable' ? missingPae(nativePae.reason) : 'Full PAE not retained.'}</p>}
            </section>
        </div>
        <section aria-label="Confidence profile"><h3>Per-Residue pLDDT Profile</h3><p className="text-xs text-[var(--text-secondary)]">{chains.unmapped ? 'Retained legacy residue confidence; chain mapping unavailable.' : profile.description}</p>{Object.keys(chains).length ? <ConfidenceProfile chainMetrics={chains} onInspect={ref => setInspection([ref])} /> : <p role="status">{residue.isPending || chain.isPending ? 'Loading confidence…' : profile.reason ?? 'Per-chain confidence was not retained.'}</p>}</section>
        {inspection.length > 0 && <div aria-label="Confidence selection" className="text-sm">{inspection.map(axisLabel).join(' × ')} <button onClick={() => setInspection([])}>Clear inspection</button></div>}
        {nativeChain.status === 'ok' && <details><summary>Native chain-pair iPTM</summary><p>Directional entries are retained as published; no symmetrization.</p><table><thead><tr><th>Row chain</th><th>Column chain</th><th>iPTM</th><th>Inspect</th></tr></thead><tbody>{nativeChain.chains.flatMap(a => nativeChain.chains.map(b => <tr key={`${a.providerIndex}:${b.providerIndex}`}><td>{a.chainId}</td><td>{b.chainId}</td><td>{nativeChain.pairChainsIptm[a.providerIndex][b.providerIndex]}</td><td><button onClick={() => { setRowChain(a.chainId); setColumnChain(b.chainId); setInspection([...a.residues, ...b.residues]); }}>PAE block</button></td></tr>))}</tbody></table></details>}
        <details><summary>Raw confidence details</summary><pre className="max-h-80 overflow-auto text-xs">{JSON.stringify({ residue: residue.data, chains: chain.data, ...(tokenPae ? { paeAxes: { row: tokenPae.rowAxis, column: tokenPae.columnAxis, nativeShape: tokenPae.nativeShape, sampledRows: tokenPae.sampledRowIndices, sampledColumns: tokenPae.sampledColumnIndices } } : {}) }, null, 2)}</pre></details>
    </>;
}

function PaeComparison({ design, onSelect, fullPaeRequested }: { design: Design; onSelect: () => void; fullPaeRequested?: boolean }) {
    const query = useQuery({ queryKey: ['prediction-pae', design.id], queryFn: () => fetchPAEData(design.id).then(r => r.data), retry: false, staleTime: 60_000 });
    const native = parseScientificPae(query.data, design.scientific_structure_document, design.id);
    return <article className="min-w-0 rounded-lg border border-[var(--border-color)] p-2">
        <button onClick={onSelect}>{design.name}</button>
        {native.status === 'ok' && native.axisKind === 'model_token' && <p className="text-xs">Native model-token indices; structure mapping unavailable. {native.rowAxis.orientation} · {native.rowAxis.mapping_reason}</p>}
        {native.status === 'ok' ? <Plot data={[{ type: 'heatmap', z: native.matrix, x: native.axisKind === 'model_token' ? native.columnTokens.map(tokenLabel) : native.columns.map(axisLabel), y: native.axisKind === 'model_token' ? native.rowTokens.map(tokenLabel) : native.rows.map(axisLabel), zmin: 0, zmax: 30, colorscale: 'YlGnBu', showscale: false, hovertemplate: 'Column %{x}<br>Row %{y}<br>PAE %{z:.2f} Å<extra></extra>' } as Data]}
            layout={{ paper_bgcolor: 'transparent', margin: { l: 5, r: 5, t: 5, b: 5 }, xaxis: { type: 'category', showticklabels: false, constrain: 'domain' }, yaxis: { type: 'category', showticklabels: false, autorange: 'reversed', scaleanchor: 'x', constrain: 'domain' } }}
            config={{ responsive: true, toImageButtonOptions: { format: 'svg' } }} style={{ width: '100%', aspectRatio: '1 / 1' }} /> : <p className="text-xs">{query.isPending ? 'Loading…' : fullPaeRequested === false ? 'Full PAE was not requested.' : query.isError ? 'PAE request failed.' : missingPae(native.reason)}</p>}
    </article>;
}

export function StructurePredictionResults({ designs, selectedDesignId, onSelectDesign, structure, enabled = true, fullPaeRequested, modelId }: { modelId?: string | null; fullPaeRequested?: boolean; enabled?: boolean; designs: Design[]; selectedDesignId?: string | null; onSelectDesign?: (id: string) => void; structure?: ConfidenceStructure }) {
    const [localId, setLocalId] = useState('');
    const [compare, setCompare] = useState(false);
    const design = selectedDesignId ? designs.find(d => d.id === selectedDesignId) : designs.find(d => d.id === localId) ?? designs[0];
    if (!enabled) return <>{typeof structure === 'function' ? structure([], () => {}) : structure}</>;
    if (!design) return <p>No saved predictions loaded.</p>;
    const select = onSelectDesign ?? setLocalId;
    const labels: Record<string, string> = { protenix: 'Protenix', boltz2: 'Boltz-2', boltz_cp_experimental: 'Boltz-2 via Fold-CP', esmfold2: 'Biohub ESMFold2', esmfold2_experimental: 'Biohub ESMFold2' };
    const modelLabel = labels[modelId ?? String(design.provenance?.producer_model_id ?? design.provenance?.model_id ?? '')] ?? 'Structure prediction';
    return <section aria-label="Structure prediction confidence" className="space-y-4 p-4 text-[var(--text-primary)]">
        <h2 className="text-lg font-semibold">{modelLabel} confidence</h2>
        <label>Selected prediction <select aria-label="Selected prediction" value={design.id} onChange={e => select(e.target.value)}>{designs.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}</select></label>
        <PredictionEvidence key={design.id} design={design} structure={structure} fullPaeRequested={fullPaeRequested} />
        <details onToggle={event => setCompare(event.currentTarget.open)}><summary>Compare native PAE — fixed 0–30 Å scale</summary>
            <p className="text-xs">Native token order for each saved prediction; no rescaling, matrix averaging or symmetrization.</p>
            {compare && <div className="grid grid-cols-1 gap-3 md:grid-cols-2 xl:grid-cols-4">{designs.map(d => <PaeComparison key={d.id} fullPaeRequested={fullPaeRequested} design={d} onSelect={() => select(d.id)} />)}</div>}
        </details>
        <details open><summary>Saved predictions ({designs.length})</summary><p className="text-xs">Saved sample identities, not a new ranking. Missing scores are not zero. pLDDT is not evidence of correct inter-chain placement.</p><div className="overflow-auto"><table className="w-full text-left text-sm"><thead><tr><th>Prediction</th><th>pLDDT (0–100)</th><th>pTM</th><th>iPTM</th></tr></thead><tbody>{designs.map(d => <tr key={d.id} aria-selected={design.id === d.id}><td><button onClick={() => select(d.id)}>{d.name}</button></td><td title={String(d.plddt_overall ?? '')}>{d.plddt_overall?.toFixed(2) ?? '—'}</td><td title={String(d.ptm ?? '')}>{d.ptm?.toFixed(4) ?? '—'}</td><td title={String(d.confidence_metrics?.iptm ?? d.iptm ?? '')}>{(typeof d.confidence_metrics?.iptm === 'number' ? d.confidence_metrics.iptm : d.iptm)?.toFixed(4) ?? '—'}</td></tr>)}</tbody></table></div></details>
    </section>;
}
