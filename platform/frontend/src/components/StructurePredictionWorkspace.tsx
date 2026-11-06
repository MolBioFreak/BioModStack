import { useMemo, useState, type ReactNode } from 'react';
import { downloadDesignPdb, type Design, type Job } from '../lib/api';
import { predictionDisplayLabel, predictionJobLabel } from '../lib/designFriendlyLabel';
import { parseScientificNativeMetric } from '../lib/scientificViewerIdentity';
import { StructureWorkbench } from '../structureViewer/StructureWorkbench';
import { nativePlddtLayers, PLDDT_BANDS } from '../structureViewer/metrics/nativeConfidenceLayer';
import type { AtomRef } from '../structureViewer/contracts/structureIdentity';
import type { MetricSelection } from '../structureViewer/metrics/metricContracts';
import { ConfidenceProfile } from './ConfidenceProfile';
import { NativeArtifactDownloads, useConfidenceChains } from './StructurePredictionResults';
import { usePredictionScalars, type ScalarEvidence } from './predictionScalarEvidence';
import { useThemeColors } from './useThemeColors';
import './StructurePredictionWorkspace.css';

const MODEL_LABELS: Record<string, string> = { protenix: 'Protenix', boltz2: 'Boltz-2', boltz_cp_experimental: 'Boltz-2 via Fold-CP', esmfold2: 'Biohub ESMFold2', esmfold2_experimental: 'Biohub ESMFold2' };
const PLDDT_KEYS = ['complex_plddt', 'plddt', 'plddt_mean', 'plddt_overall'];
const PDE_KEYS = ['gpde', 'complex_pde'];
const RANK_KEYS = ['ranking_score', 'confidence_score'];
const PRIMARY_KEYS = new Set([...PLDDT_KEYS, 'ptm', 'iptm', ...PDE_KEYS, ...RANK_KEYS]);
type Entry = ScalarEvidence['entries'][number];
const asRecord = (value: unknown): Record<string, unknown> | null => value != null && typeof value === 'object' && !Array.isArray(value) ? value as Record<string, unknown> : null;
const pick = (evidence: ScalarEvidence, keys: string[]) => keys.map(key => evidence.entries.find(entry => entry.key === key)).find(Boolean);
/** Numeric value in the displayed scale (pLDDT 0–100); never a fabricated zero. */
const shown = (entry?: Entry) => { const n = entry ? Number(entry.display) : NaN; return Number.isFinite(n) ? n : undefined; };
const band = (plddt?: number) => plddt === undefined ? undefined : PLDDT_BANDS[plddt >= 90 ? 0 : plddt >= 70 ? 1 : plddt >= 50 ? 2 : 3];
const bandText = (plddt?: number) => plddt === undefined ? '' : plddt >= 90 ? 'Very high' : plddt >= 70 ? 'Confident' : plddt >= 50 ? 'Low' : 'Very low';
const fmt = (entry: Entry | undefined, digits: number) => { const n = shown(entry); return n === undefined ? entry?.display ?? '—' : n.toFixed(digits); };
const producerRank = (design: Design) => {
    const rank = design.native_sample?.producer_rank ?? asRecord(design.provenance?.native_producer)?.producer_rank;
    return typeof rank === 'number' && Number.isFinite(rank) ? rank : undefined;
};

/** Molecular composition from the saved request; absent components stay unknown. */
function composition(job: Job) {
    const components = Array.isArray(job.params?.complex_components) ? job.params.complex_components as Record<string, unknown>[] : [];
    if (!components.length) {
        const sequence = typeof job.params?.sequence === 'string' ? job.params.sequence : '';
        return sequence ? { chains: 1, residues: sequence.length, protein: true } : null;
    }
    let chains = 0, residues = 0;
    for (const component of components) {
        const count = typeof component.count === 'number' ? component.count : 1;
        chains += count;
        if (component.type === 'protein' && typeof component.sequence === 'string') residues += component.sequence.length * count;
    }
    return { chains, residues, protein: residues > 0 };
}

function Tile({ label, entry, value, subtitle, bar, muted = false, wide = false, extra }: { label: string; entry?: Entry; value: string; subtitle?: string; bar?: { fraction: number; color: string }; muted?: boolean; wide?: boolean; extra?: ReactNode }) {
    return <div className={`spw-tile${wide ? ' spw-tile-wide' : ''}`} data-muted={muted || undefined}>
        <div className="spw-tile-label"><span>{label}</span><span className="spw-info" title={entry ? `${entry.label}: ${entry.title}` : 'Not reported by this prediction.'} aria-label={`${label} source`}>ⓘ</span></div>
        <div className="spw-tile-value">{value}{extra}</div>
        {subtitle && <div className="spw-tile-sub">{subtitle}</div>}
        {bar && <div className="spw-bar" aria-hidden><span style={{ width: `${Math.max(0, Math.min(1, bar.fraction)) * 100}%`, background: bar.color }} /></div>}
    </div>;
}

export interface StructurePredictionWorkspaceProps {
    job: Job;
    designs: Design[];
    totalDesigns: number;
    selectedDesignId?: string | null;
    onSelectDesign: (id: string) => void;
    onOpenStructure: () => void;
    onCompare: () => void;
    actions?: ReactNode;
    operationIds: readonly string[];
    onOperationToggle: (id: string, selected: boolean) => void;
}

/** Overview for standalone structure prediction (Protenix, Boltz-2, Fold-CP, ESMFold2 and its alias). */
export function StructurePredictionWorkspace(props: StructurePredictionWorkspaceProps) {
    const { job, designs, totalDesigns, selectedDesignId } = props;
    const scalarsFor = usePredictionScalars(designs);
    const modelId = String(designs[0]?.provenance?.producer_model_id ?? job.model_id);
    const molecule = composition(job);
    const seeds = job.params?.protenix_seeds ?? job.params?.seed ?? job.params?.seeds;
    const ranked = useMemo(() => {
        const rows = designs.map((design, index) => ({ design, index, scalars: scalarsFor(design) }));
        const score = (row: typeof rows[number]) => shown(pick(row.scalars, RANK_KEYS));
        if (rows.length > 1 && rows.every(row => score(row) !== undefined)) return { by: 'producer score' as const, rows: [...rows].sort((a, b) => score(b)! - score(a)! || a.index - b.index) };
        if (rows.length > 1 && rows.every(row => producerRank(row.design) !== undefined)) return { by: 'producer rank' as const, rows: [...rows].sort((a, b) => producerRank(a.design)! - producerRank(b.design)! || a.index - b.index) };
        return { by: 'saved order' as const, rows };
    }, [designs, scalarsFor]);
    const selected = ranked.rows.find(row => row.design.id === selectedDesignId) ?? ranked.rows[0];
    const external = selected ? asRecord(selected.design.provenance?.external_import) : null;
    const modelLabel = external?.provider === 'boltz_api' ? 'Boltz API' : MODEL_LABELS[modelId] ?? 'Structure prediction';
    const statusClass = job.status === 'completed' ? 'spw-chip-ok' : job.status === 'failed' ? 'spw-chip-bad' : '';
    return <section aria-label="Structure prediction workspace" className="spw text-[var(--text-primary)]">
        <header className="spw-header">
            <div className="min-w-0">
                <h2 className="spw-title" title={job.name}>{predictionJobLabel(job)}</h2>
                <div className="spw-chips">
                    <span className="spw-chip">{modelLabel}</span>
                    {seeds != null && seeds !== '' && <span className="spw-chip">seed {Array.isArray(seeds) ? seeds.join(', ') : String(seeds)}</span>}
                    <span className="spw-chip">{totalDesigns} {totalDesigns === 1 ? 'sample' : 'samples'}</span>
                    {molecule && <span className="spw-chip">{molecule.chains} {molecule.chains === 1 ? 'chain' : 'chains'}{molecule.protein ? ` · ${molecule.residues} aa` : ''}</span>}
                    <span className={`spw-chip ${statusClass}`}>{job.status === 'completed' ? 'Completed' : job.status}</span>
                </div>
            </div>
            <div className="spw-actions">
                {selected && <details className="spw-menu"><summary className="spw-button">Download ▾</summary>
                    <div className="spw-menu-body">
                        <a className="underline" href={downloadDesignPdb(selected.design.id)}>Selected structure · {predictionDisplayLabel(selected.design, selected.index)}</a>
                        <NativeArtifactDownloads key={`menu-${selected.design.id}`} design={selected.design} />
                    </div>
                </details>}
                <button type="button" className="spw-button" onClick={props.onCompare}>Compare…</button>
                {props.actions}
            </div>
        </header>
        {!selected ? <p role="status">No saved predictions loaded.</p> : <>
            <SelectedOverview key={selected.design.id} {...props} selected={selected} ranked={ranked} molecule={molecule} />
        </>}
    </section>;
}

type RankedRow = { design: Design; index: number; scalars: ScalarEvidence };
function SelectedOverview({ selected, ranked, molecule, onSelectDesign, onOpenStructure, operationIds, onOperationToggle, totalDesigns, designs }: StructurePredictionWorkspaceProps & {
    selected: RankedRow; ranked: { by: 'producer score' | 'producer rank' | 'saved order'; rows: RankedRow[] }; molecule: ReturnType<typeof composition>;
}) {
    const colors = useThemeColors();
    const design = selected.design;
    const label = predictionDisplayLabel(design, selected.index);
    const { residue, chain, profile, chains } = useConfidenceChains(design);
    const [inspection, setInspection] = useState<readonly AtomRef[]>([]);
    const native = parseScientificNativeMetric(residue.data, design.scientific_structure_document, 'residue_plddt', design.id);
    const layers = useMemo(() => nativePlddtLayers(native), [residue.data, design.id]); // eslint-disable-line react-hooks/exhaustive-deps
    const metricLayers = useMemo(() => [layers.residue, layers.atomTokens].filter(layer => layer !== null), [layers]);
    const bound = Boolean(design.scientific_structure_document || design.core_protein_scientific_contract === 1);
    const document = design.scientific_structure_document;
    const scalars = selected.scalars;
    const plddt = pick(scalars, PLDDT_KEYS), ptm = pick(scalars, ['ptm']), iptm = pick(scalars, ['iptm']), pde = pick(scalars, PDE_KEYS), rank = pick(scalars, RANK_KEYS);
    const monomer = molecule?.chains === 1;
    const rankPosition = ranked.by === 'producer score' ? ranked.rows.findIndex(row => row.design.id === design.id) + 1 : 0;
    const extras = scalars.entries.filter(entry => !PRIMARY_KEYS.has(entry.key));
    const acceptSelection = (selection: MetricSelection) => setInspection(selection.identities.flatMap(identity =>
        'first' in identity && 'second' in identity ? [identity.first, identity.second] as AtomRef[] : 'authSeqId' in identity || 'labelSeqId' in identity ? [identity as AtomRef] : []));
    const loadedNote = designs.length < totalDesigns ? ` · ${designs.length} of ${totalDesigns} loaded` : '';
    return <>
        <div className="spw-grid">
            <section className="spw-card spw-samples" aria-label="Prediction samples">
                <h3 className="spw-heading">Samples · {ranked.by === 'saved order' ? 'saved order' : `ranked by ${ranked.by}`}{loadedNote}</h3>
                <div className="spw-table-wrap"><table className="spw-table">
                    <thead><tr><th>Sample</th><th>pLDDT</th><th>pTM</th><th>ipTM</th><th>{rank?.key === 'confidence_score' ? 'Confidence' : 'Rank score'}</th></tr></thead>
                    <tbody>{ranked.rows.map((row, position) => {
                        const rowPlddt = shown(pick(row.scalars, PLDDT_KEYS));
                        const active = row.design.id === design.id;
                        return <tr key={row.design.id} aria-selected={active} data-active={active || undefined} onClick={() => onSelectDesign(row.design.id)} title={row.design.name}>
                            <td><button type="button" onClick={event => { event.stopPropagation(); onSelectDesign(row.design.id); }}>
                                <span className="spw-rank">{position + 1}</span>
                                <span className="spw-dot" style={{ background: band(rowPlddt)?.color ?? 'var(--border-primary)' }} aria-hidden />
                                <span className="spw-name">{predictionDisplayLabel(row.design, row.index)}</span></button></td>
                            {[pick(row.scalars, PLDDT_KEYS), pick(row.scalars, ['ptm']), pick(row.scalars, ['iptm']), pick(row.scalars, RANK_KEYS)].map((entry, column) =>
                                <td key={column} title={entry ? `${entry.label}: ${entry.title}` : row.scalars.reason ?? 'Not reported'}>{fmt(entry, column === 0 ? 1 : column === 3 ? 4 : 3)}</td>)}
                        </tr>;
                    })}</tbody>
                </table></div>
                {selected.scalars.reason && <p role="status" className="spw-caption">{selected.scalars.reason}</p>}
                <div className="spw-samples-footer">
                    <label><input type="checkbox" checked={operationIds.includes(design.id)} onChange={event => onOperationToggle(design.id, event.target.checked)} /> Select {label} for operations</label>
                    <NativeArtifactDownloads key={`footer-${design.id}`} design={design} />
                </div>
            </section>
            <section className="spw-card spw-viewer" aria-label="Selected prediction structure preview">
                <div className="spw-card-head">
                    <h3 className="spw-heading">{label} · coloured by pLDDT</h3>
                    <button type="button" className="spw-button" onClick={onOpenStructure}>Open in Structure ↗</button>
                </div>
                <div className="spw-canvas">
                    <StructureWorkbench mode="compact" structureUrl={`/api/designs/${design.id}/pdb`}
                        format={design.pdb_path?.endsWith('.cif') || document?.sourceKind === 'mmcif' ? 'cif' : 'pdb'}
                        alphafoldView={!bound} metricLayers={metricLayers} activeMetricId={layers.residue ? 'native-plddt' : undefined}
                        residueSelections={inspection} onMetricSelection={acceptSelection} controlledResidueSelection
                        jobId={design.job_id} artifactJobId={design.job_id}
                        structureDocumentId={document?.documentId} structureContentSha256={document?.contentSha256}
                        showM6Workbench={false} showMeasurements={false} showComplexWorkbench={false} showSequenceTrack={false}
                        height="100%" backgroundColor={colors.bgPrimary} label={label} />
                </div>
                <div className="spw-legend" aria-label="pLDDT colour bands">{PLDDT_BANDS.map(item => <span key={item.key}><i style={{ background: item.color }} />{item.label}</span>)}</div>
                {bound && native.status !== 'ok' && <p role="status" className="spw-caption">{residue.isPending ? 'Loading native confidence…' : `Native confidence colouring unavailable: ${native.reason}. The structure remains available.`}</p>}
            </section>
            <section className="spw-card spw-confidence" aria-label="Selected prediction confidence">
                <h3 className="spw-heading">{label} confidence</h3>
                <div className="spw-tiles">
                    <Tile label={plddt?.key === 'complex_plddt' ? 'Complex pLDDT' : 'Mean pLDDT'} entry={plddt} value={fmt(plddt, 1)}
                        subtitle={shown(plddt) === undefined ? 'Not reported' : `${bandText(shown(plddt))} (0–100)`}
                        bar={shown(plddt) === undefined ? undefined : { fraction: shown(plddt)! / 100, color: band(shown(plddt))!.color }} />
                    <Tile label="pTM" entry={ptm} value={fmt(ptm, 3)} subtitle={ptm ? 'Global fold' : 'Not reported'}
                        bar={shown(ptm) === undefined ? undefined : { fraction: shown(ptm)!, color: 'var(--accent-primary)' }} />
                    <Tile label={pde?.key === 'complex_pde' ? 'Complex PDE' : 'gPDE'} entry={pde} value={pde ? `${fmt(pde, 2)} Å` : '—'} subtitle={pde ? 'Lower is better' : 'Not reported'} />
                    <Tile label="ipTM" entry={iptm} value={fmt(iptm, 3)} muted={!iptm || (monomer && shown(iptm) === 0)}
                        subtitle={!iptm ? 'Not reported' : monomer ? '1 chain, no interface' : 'Interface placement'} />
                    {rank && <Tile wide label={rank.key === 'confidence_score' ? 'Producer confidence score' : 'Producer rank score'} entry={rank} value={fmt(rank, 4)}
                        extra={<span className="spw-tile-inline">{rankPosition ? `rank ${rankPosition} of ${ranked.rows.length}` : ''}{producerRank(design) !== undefined ? ` · native sample index ${producerRank(design)}` : ''}</span>} />}
                    {extras.map(entry => <Tile key={entry.key} label={entry.label} entry={entry} value={entry.display} />)}
                </div>
                <p className="spw-caption">pLDDT is not a probability of correctness; ipTM scores interface placement, not binding.</p>
            </section>
        </div>
        <section className="spw-card" aria-label="Per-residue pLDDT">
            <h3 className="spw-heading">Per-residue pLDDT · {label}</h3>
            {Object.keys(chains).length
                ? <ConfidenceProfile chainMetrics={chains} height={240} onInspect={ref => setInspection([ref])} />
                : <p role="status" className="spw-caption">{residue.isPending || chain.isPending ? 'Loading confidence…' : profile.reason ?? 'Per-residue confidence was not retained.'}</p>}
            {Object.keys(chains).length > 0 && <p className="spw-caption">{profile.description.replace(/\.$/, '')}. Click a residue to highlight it in the viewer; drag to zoom.</p>}
            {inspection.length > 0 && <p className="spw-caption" aria-label="Overview confidence selection">Highlighted {inspection.map(ref => `${ref.authAsymId ?? ref.labelAsymId ?? ''}:${ref.authSeqId ?? ref.labelSeqId}${ref.insertionCode ?? ''}`).join(', ')} <button type="button" className="underline" onClick={() => setInspection([])}>Clear</button></p>}
        </section>
    </>;
}
