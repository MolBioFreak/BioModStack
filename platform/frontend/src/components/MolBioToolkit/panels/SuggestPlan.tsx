import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
    fetchMolecularRevision,
    fetchGoldenGateAssemblyOptions,
    type SuggestGoldenGateRequest,
    suggestGibsonPlan,
    suggestGoldenGatePlan,
    prepareRestrictionCloning,
    suggestRestrictionCloning,
    type GoldenGateAssemblyOptionsResponse,
    type LigationAssemblyRequest,
    type SuggestRestrictionRequest,
    type SuggestRestrictionProposal,
    type ChooserItem,
    type SavedGibsonDesignRequest,
    type SequenceFeature,
    type SuggestGibsonProposal,
    type SuggestGibsonSource,
    type SuggestGoldenGateProposal,
    type SuggestGoldenGateResponse,
    type SuggestRestrictionResponse,
} from '../../../lib/api';
import type { WorkflowRequest } from '../../../lib/goldenGateWorkflowTypes';
import { FragmentChooser } from '../FragmentChooser';
import type { SequenceData, SelectionInfo } from '../types';
import { SequenceViewer } from '../SequenceViewer';
import { DEFAULT_VISIBILITY } from '../sequenceViewerConstants';
import { createSelectionSnapshot } from '../utils/selectionActions';
import { inlineFragmentFeatures } from '../utils/assemblyFeatures';

type Method = 'gibson' | 'golden_gate' | 'restriction';
type Molecule = { name: string; sequence: string; circular: boolean; features?: SequenceFeature[]; document_id?: string; revision_id?: string; revision_number?: number };
type TargetChoice = 'current' | 'selection' | 'saved' | 'pasted';

/** "EcoRI + BamHI", or "EcoRI (single enzyme)" when both ends use the same enzyme. */
export const enzymePairLabel = (enzymes: string[]) => enzymes.length > 1 && enzymes.every((e) => e === enzymes[0]) ? `${enzymes[0]} (single enzyme)` : enzymes.join(' + ');
/** One heading label: the shared pair when vector and insert use the same ends, otherwise both. */
export const proposalPairLabel = (p: Pick<SuggestRestrictionProposal, 'vector_enzymes' | 'insert_enzymes'>) => (
    p.vector_enzymes.length === p.insert_enzymes.length && p.vector_enzymes.every((e, k) => e === p.insert_enzymes[k])
        ? enzymePairLabel(p.vector_enzymes)
        : `Vector: ${enzymePairLabel(p.vector_enzymes)} · Insert: ${enzymePairLabel(p.insert_enzymes)}`
);
/** Junction lines; identical junctions collapse into one line. */
export const junctionLines = (sites: SuggestRestrictionProposal['junction_sites']): string[] => {
    const text = (j: SuggestRestrictionProposal['junction_sites'][number]) => `regenerated ${j.regenerated.join(', ') || 'none'} · destroyed ${j.destroyed.join(', ') || 'none'}`;
    if (sites.length > 1 && sites.every((j) => text(j) === text(sites[0]))) return [`Both junctions: ${text(sites[0])}`];
    return sites.map((j) => `${j.between}: ${text(j)}`);
};
const numbers = (text: string) => text.split(/[\s,]+/).filter(Boolean).map(Number).filter(Number.isFinite);

async function moleculeFromItem(item: ChooserItem): Promise<Molecule> {
    const revision = await fetchMolecularRevision(item.document_id, item.revision_id);
    const snapshot = revision.snapshot as { sequence?: string; is_circular?: boolean; features?: SequenceFeature[] };
    return {
        name: item.name, sequence: snapshot.sequence ?? '', circular: snapshot.is_circular === true, features: snapshot.features ?? undefined,
        document_id: item.document_id, revision_id: item.revision_id, revision_number: item.revision_number,
    };
}

const storedFeatures = (features: SequenceFeature[] | undefined) => (features ?? []).map((f) => ({
    id: f.id, name: f.name, type: f.type, strand: f.strand, qualifiers: f.qualifiers ?? f.notes ?? {},
    segments: (f.segments ?? [{ start: f.start, end: f.end }]).map((s) => ({ ...s, wraps_origin: s.start > s.end })),
    codon_start: null, status: 'intact', frame_preserved: null,
}));

const errorText = (e: unknown) => {
    const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof detail === 'string' ? detail : e instanceof Error ? e.message : String(e);
};

function Num({ label, value, onChange }: { label: string; value: number; onChange: (v: number) => void }) {
    return <label className="flex flex-col gap-1 text-slate-400">{label}
        <input type="number" className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" value={value}
            onChange={(e) => onChange(Number(e.target.value))} /></label>;
}

function GibsonProposal({ proposal, onUse }: { proposal: SuggestGibsonProposal; onUse: (design: SavedGibsonDesignRequest) => void }) {
    const primers = (proposal as unknown as { primers?: Array<{ id: string; tm?: number | null }> }).primers;
    const junctions = (proposal as unknown as { junctions?: Array<{ overlap_length?: number; overlap_tm?: number | null }> }).junctions;
    const synthBp = proposal.fragments.filter((f) => f.preparation === 'synthesis').reduce((n, f) => n + (f.sequence?.length ?? 0), 0);
    return <li className="space-y-1 rounded border border-slate-700 bg-slate-950/60 p-2">
        <div className="font-medium text-slate-100">#{proposal.rank} {proposal.summary}</div>
        <div className="text-slate-400">{proposal.counts.pcr} PCR · {proposal.counts.synthesis} synthesis ({synthBp} bp) · {proposal.counts.existing} existing</div>
        <ul className="text-slate-300">{proposal.fragments.map((f) => <li key={f.id}>
            {f.name}: {f.preparation}{f.source ? ` from ${f.source.id} ${f.source.start}–${f.source.end} (${f.source.strand === -1 ? '−' : '+'}${f.source.wraps_origin ? ', across origin' : ''})` : ''}{f.sequence ? ` · ${f.sequence.length} bp` : ''}
        </li>)}</ul>
        {primers && primers.length > 0 && <div className="text-slate-400">Primer Tm: {primers.map((p) => `${p.id} ${p.tm != null ? p.tm.toFixed(1) : '—'}`).join(', ')} °C</div>}
        {junctions && junctions.length > 0 && <div className="text-slate-400">Overlaps: {junctions.map((j) => `${j.overlap_length ?? '?'} nt${j.overlap_tm != null ? ` (Tm ${j.overlap_tm.toFixed(1)} °C)` : ''}`).join(', ')}</div>}
        <div className="text-slate-400">Features kept: {proposal.features.kept.join(', ') || 'none'} · removed: {proposal.features.removed.join(', ') || 'none'} · clipped: {proposal.features.clipped.join(', ') || 'none'}</div>
        {proposal.notes.map((n, i) => <div key={i} className="text-slate-500">{n}</div>)}
        <button type="button" className="rounded bg-violet-600 px-2 py-1 text-white" onClick={() => onUse(proposal.design_request)}>Use this plan</button>
    </li>;
}

function GoldenGateProposal({ proposal, onUse, onEdit, targetSequence, sourceBoundary }: { sourceBoundary?: (cut: number) => number; targetSequence: string; onEdit: (cuts: number[]) => void; proposal: SuggestGoldenGateProposal; onUse: (request: WorkflowRequest) => void }) {
    const per = proposal.candidate?.fidelity?.per_junction;
    const empirical = proposal.empirical === 'no empirical data';
    return <li className="space-y-1 rounded border border-slate-700 bg-slate-950/60 p-2">
        <div className="font-medium text-slate-100">#{proposal.rank} {proposal.enzyme_name} · {proposal.junction_mode === 'scarless' ? 'scarless' : proposal.junction_mode} · {proposal.internal_sites} internal site(s)</div>
        <div className="text-slate-400">{empirical ? 'No empirical data for this enzyme (ranked without fidelity numbers)' : `Empirical set: ${proposal.empirical}${proposal.f_set != null ? ` · F_set ${proposal.f_set.toFixed(3)}` : ''}`}</div>
        <div className="text-slate-300">Junctions: {proposal.junctions.map((j) => {
            const p = per?.find((x) => x.representative === j || x.representative === j.split('').reverse().join(''))?.probability;
            return `${j}${p != null ? ` (${(p * 100).toFixed(1)}%)` : ''}`;
        }).join(', ')}</div>
        <div aria-label="Target cut preview" className="text-slate-300">{proposal.cuts.map((cut, i) => <div key={i}>Cut after bp {cut}{sourceBoundary ? ` (source boundary ${sourceBoundary(cut)})` : ''}: <code>{targetSequence.slice(Math.max(0, cut - 8), cut)}|{targetSequence.slice(cut, cut + 8)}</code> · junction {proposal.junctions[i] ?? '—'}</div>)}</div>
        <button type="button" className="rounded border border-slate-600 px-2 py-1" onClick={() => onEdit(proposal.cuts)}>Edit these cuts</button>
        <div className="text-slate-400">Fragments: {proposal.fragment_lengths.join(', ')} bp · cuts at {proposal.cuts.join(', ')}</div>
        {!!proposal.notes?.length && <ul aria-label="Planning notes" className="text-slate-400">{proposal.notes.map((note, i) => <li key={i}>{note}</li>)}</ul>}
        {(proposal.candidate?.design?.materials ?? []).some(m => m.stage === 'prepared') && <details aria-label="Prepared fragment sequences" className="text-slate-300">
            <summary>Prepared fragments (with removable Type IIS flanks)</summary>
            {(proposal.candidate?.design?.materials ?? []).filter(m => m.stage === 'prepared').map(m => <div key={m.id}>{m.id} · {m.sequence.length} bp<code className="block max-h-24 overflow-auto break-all">{m.sequence}</code></div>)}
            <p>Use this plan, then Run Golden Gate task to save or export the fixed workup ZIP, including prepared FASTA. The target sequence above remains unchanged.</p>
        </details>}
        {proposal.pcr_products.length > 0 && <div className="text-slate-400">PCR: {proposal.pcr_products.map((p) => `${p.part_id} ${p.tailed_length} bp with tails (${p.payload_length} bp payload)`).join(', ')}</div>}
        <button type="button" className="rounded bg-violet-600 px-2 py-1 text-white" onClick={() => onUse(proposal.split_request as unknown as WorkflowRequest)}>Use this plan</button>
    </li>;
}

export function SuggestPlan({ builder = false, goldenGateOptions, selection, sequenceData, selectedSequenceId, onUseGibson, onUseGoldenGate, onUseRestriction }: {
    selection?: SelectionInfo | null;
    builder?: boolean;
    goldenGateOptions?: GoldenGateAssemblyOptionsResponse | null;
    sequenceData: SequenceData;
    selectedSequenceId: string | null;
    onUseGibson: (design: SavedGibsonDesignRequest) => void;
    onUseGoldenGate: (request: WorkflowRequest) => void;
    onUseRestriction: (request: LigationAssemblyRequest) => void;
}) {
    const [open, setOpen] = useState(false);
    const [method, setMethod] = useState<Method>('gibson');
    const [targetChoice, setTargetChoice] = useState<TargetChoice>('current');
    const [savedTarget, setSavedTarget] = useState<Molecule | null>(null);
    const [pasteName, setPasteName] = useState('');
    const [pasteSequence, setPasteSequence] = useState('');
    const [pasteCircular, setPasteCircular] = useState(true);
    const [useCurrentSource, setUseCurrentSource] = useState(false);
    const [sources, setSources] = useState<Molecule[]>([]);
    const [overlap, setOverlap] = useState(30);
    const [targetTm, setTargetTm] = useState(60);
    const [minAnneal, setMinAnneal] = useState(13);
    const [minMatch, setMinMatch] = useState(30);
    const [boundaries, setBoundaries] = useState('');
    const [enzyme, setEnzyme] = useState('');
    const discovery = useQuery({ queryKey: ['molbio-golden-gate-options'], queryFn: async ({ signal }) => (await fetchGoldenGateAssemblyOptions(signal)).data, enabled: open && method === 'golden_gate' && !goldenGateOptions, staleTime: 300000 });
    const options = goldenGateOptions ?? discovery.data;
    const compatibleEnzymes = (options?.enzymes ?? []).filter(e => options?.raw_design?.raw_enzyme_adapters.includes(e.canonical_name));
    const selectedEnzyme = enzyme || compatibleEnzymes[0]?.enzyme_id || '';
    useEffect(() => { if (builder) { setOpen(true); setMethod('golden_gate'); } }, [builder]);
    const [fragmentCount, setFragmentCount] = useState('');
    const [targetLength, setTargetLength] = useState('');
    const [minLength, setMinLength] = useState('1');
    const [maxLength, setMaxLength] = useState('');
    const [avoidRepeats, setAvoidRepeats] = useState(true);
    const [maxHomopolymerRun, setMaxHomopolymerRun] = useState('');
    const [homopolymerBuffer, setHomopolymerBuffer] = useState('0');
    const [preparation, setPreparation] = useState<'synthesis' | 'pcr'>('synthesis');
    const [fusion, setFusion] = useState('');
    const [restrictionEnzymes, setRestrictionEnzymes] = useState('');
    const [backboneStart, setBackboneStart] = useState('');
    const [backboneEnd, setBackboneEnd] = useState('');
    const [insertStart, setInsertStart] = useState('');
    const [insertEnd, setInsertEnd] = useState('');
    const [vectorPhosphate, setVectorPhosphate] = useState<'unknown' | 'phosphorylated' | 'unphosphorylated'>('unknown');
    const [restrictionInput, setRestrictionInput] = useState<{ body: SuggestRestrictionRequest; name: string; keys: string[] } | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [gibson, setGibson] = useState<{ proposals: SuggestGibsonProposal[]; diagnostics: string[] } | null>(null);
    const [goldenGate, setGoldenGate] = useState<SuggestGoldenGateResponse | null>(null);
    const [previewSelection, setPreviewSelection] = useState<SelectionInfo | null>(null);
    const [restriction, setRestriction] = useState<SuggestRestrictionResponse | null>(null);
    const request = useRef(0);

    const current: Molecule = {
        name: sequenceData.name, sequence: sequenceData.sequence, circular: sequenceData.circular,
        features: inlineFragmentFeatures(sequenceData.features, sequenceData.sequence.length) ?? [],
        document_id: selectedSequenceId ?? undefined,
    };
    const snapshot = createSelectionSnapshot(selection, sequenceData.sequence, sequenceData.circular);
    const selectedTarget: Molecule | null = snapshot ? {
        name: `${sequenceData.name} ${snapshot.coordinateLabel}`, sequence: snapshot.sequence, circular: false,
        features: inlineFragmentFeatures(sequenceData.features, sequenceData.sequence.length, snapshot.placement) ?? [],
    } : null;
    const target: Molecule | null = targetChoice === 'current' ? current
        : targetChoice === 'selection' ? selectedTarget : targetChoice === 'saved' ? savedTarget
            : pasteSequence.trim() ? { name: pasteName.trim() || 'Pasted target', sequence: pasteSequence.toUpperCase().replace(/[^A-Z]/g, ''), circular: pasteCircular } : null;
    // Field edits invalidate recommendations; only an explicit action runs the planner.
    useEffect(() => { request.current++; setGoldenGate(null); setBusy(false); }, [target?.sequence, target?.circular, selectedEnzyme, fragmentCount, boundaries, preparation, fusion, targetLength, minLength, maxLength, avoidRepeats, maxHomopolymerRun, homopolymerBuffer]);
    useEffect(() => { setPreviewSelection(null); }, [target?.sequence, target?.circular]);
    const editCuts = (cuts: number[]) => { setBoundaries(cuts.join(', ')); setFragmentCount(String(cuts.length + (target?.circular ? 0 : 1))); setGoldenGate(null); };
    const selectedSources: Molecule[] = [...(useCurrentSource ? [current] : []), ...sources];
    const pick = (apply: (m: Molecule) => void) => (item: ChooserItem) => { void moleculeFromItem(item).then(apply).catch((e) => setError(errorText(e))); };

    const run = async () => {
        if (!target) { setError('Choose a target molecule.'); return; }
        const mine = ++request.current;
        setBusy(true); setError(null);
        try {
            if (method === 'gibson') {
                const body = {
                    target: { name: target.name, sequence: target.sequence, circular: target.circular, ...(target.features ? { features: target.features } : {}) },
                    sources: selectedSources.map((m, i): SuggestGibsonSource => ({
                        id: `src${i + 1}`, name: m.name, sequence: m.sequence, circular: m.circular,
                        ...(m.document_id ? { source_sequence_id: m.document_id } : {}),
                        ...(m.revision_number != null ? { source_revision: m.revision_number } : {}),
                        ...(m.features ? { features: m.features } : {}),
                    })),
                    min_match: minMatch, overlap, target_tm: targetTm, min_anneal: minAnneal, allow_synthesis: true, limit: 3,
                    ...(numbers(boundaries).length ? { fixed_boundaries: numbers(boundaries) } : {}),
                };
                const { data } = await suggestGibsonPlan(body);
                if (mine === request.current) setGibson(data);
            } else if (method === 'golden_gate') {
                if (!selectedEnzyme) { setError('Choose a compatible Type IIS enzyme.'); return; }
                if (maxHomopolymerRun !== '' && (!Number.isSafeInteger(Number(maxHomopolymerRun)) || Number(maxHomopolymerRun) < 1)) { setError('Maximum homopolymer run must be a whole number of at least 1, or blank to disable.'); return; }
                if (homopolymerBuffer === '' || !Number.isSafeInteger(Number(homopolymerBuffer)) || Number(homopolymerBuffer) < 0) { setError('Homopolymer junction buffer must be a whole number of at least 0.'); return; }
                const fixed = numbers(boundaries);
                if (boundaries.trim() && (!/^[\d\s,]+$/.test(boundaries) || fixed.some(c => !Number.isInteger(c) || c < 0 || c >= target.sequence.length))) { setError('Enter whole-number cut boundaries within the target.'); return; }
                const body: SuggestGoldenGateRequest = {
                    target: { id: 'target', source: target.revision_id
                        ? { kind: 'molecular_revision' as const, revision_id: target.revision_id }
                        : { kind: 'inline' as const, sequence: target.sequence, topology: target.circular ? 'circular' as const : 'linear' as const, features: storedFeatures(target.features) } },
                    enzymes: [selectedEnzyme],
                    target_fragment_length: targetLength ? Number(targetLength) : null,
                    min_fragment_length: Number(minLength),
                    max_fragment_length: maxLength ? Number(maxLength) : null,
                    avoid_repeats: avoidRepeats,
                    max_homopolymer_run: maxHomopolymerRun ? Number(maxHomopolymerRun) : null,
                    homopolymer_junction_buffer: Number(homopolymerBuffer),
                    fragment_count: fragmentCount ? Number(fragmentCount) : numbers(boundaries).length ? numbers(boundaries).length + (target.circular ? 0 : 1) : null,
                    fixed_boundaries: numbers(boundaries),
                    preparation_kind: preparation,
                    ...(fusion.trim() ? { terminal_right_fusion: fusion.trim().toUpperCase() } : {}),
                    proposals: 3,
                };
                const { data } = await suggestGoldenGatePlan(body);
                if (mine === request.current) setGoldenGate(data);
            } else {
                const insert = selectedSources[0];
                if (!insert) { setError('Select the insert as a source.'); return; }
                const names = restrictionEnzymes.split(/[\s,]+/).filter(Boolean);
                const body: SuggestRestrictionRequest = {
                    ...(target.revision_id ? { backbone_revision_id: target.revision_id } : {
                        backbone_sequence: target.sequence, backbone_features: target.features ?? [],
                        backbone_source_document_id: target.document_id,
                    }),
                    backbone_topology: target.circular ? 'circular' : 'linear',
                    ...(insert.revision_id ? { insert_revision_id: insert.revision_id } : {
                        insert_sequence: insert.sequence, insert_features: insert.features ?? [],
                        insert_source_document_id: insert.document_id,
                    }),
                    insert_topology: insert.circular ? 'circular' : 'linear',
                    ...(backboneStart !== '' ? { backbone_region_start: Number(backboneStart) } : {}),
                    ...(backboneEnd !== '' ? { backbone_region_end: Number(backboneEnd) } : {}),
                    ...(insertStart !== '' ? { insert_start: Number(insertStart) } : {}),
                    ...(insertEnd !== '' ? { insert_end: Number(insertEnd) } : {}),
                    vector_five_prime_phosphate: vectorPhosphate,
                    ...(names.length ? { enzymes: names } : {}), limit: 3,
                };
                const { data } = await suggestRestrictionCloning(body);
                if (mine === request.current) {
                    setRestriction(data);
                    setRestrictionInput({ body, name: target.name, keys: data.proposals.map(() => crypto.randomUUID()) });
                }
            }
        } catch (e) {
            if (mine === request.current) setError(errorText(e));
        } finally {
            if (mine === request.current) setBusy(false);
        }
    };

    const useRestriction = async (proposal: SuggestRestrictionProposal, index: number) => {
        if (!restrictionInput) return;
        setBusy(true); setError(null);
        try {
            const { data } = await prepareRestrictionCloning({ request: restrictionInput.body, selection: proposal.selection,
                name: `${restrictionInput.name} restriction assembly`, idempotency_key: restrictionInput.keys[index] });
            onUseRestriction(data.assembly_request);
        } catch (e) { setError(errorText(e)); }
        finally { setBusy(false); }
    };
    const copy = (text: string) => { void navigator.clipboard?.writeText(text); };

    if (!open) {
        return <button type="button" onClick={() => setOpen(true)} className="rounded border border-slate-600 px-3 py-1.5 text-xs text-slate-200">Suggest plan</button>;
    }
    return (
        <section aria-label="Suggest plan" className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/50 p-3 text-xs">
            <div className="flex items-center justify-between">
                <h5 className="font-semibold text-slate-100">{builder ? 'Golden Gate target builder' : 'Suggest plan'}</h5>
                {!builder && <button type="button" className="px-2 text-slate-400" onClick={() => setOpen(false)}>Close</button>}
            </div>
            {!builder && <div className="flex flex-wrap gap-2">
                {([['gibson', 'HiFi / Gibson'], ['golden_gate', 'Golden Gate'], ['restriction', 'Restriction-ligation']] as const).map(([value, label]) => (
                    <button key={value} type="button" onClick={() => setMethod(value)}
                        className={`rounded-full px-3 py-1 ${method === value ? 'bg-violet-600 text-white' : 'bg-slate-800 text-slate-300'}`}>{label}</button>
                ))}
            </div>}

            <fieldset className="min-w-0 space-y-1">
                <legend className="text-slate-300">{method === 'restriction' ? 'Backbone' : 'Target (what you want to end up with)'}</legend>
                <select aria-label="Target molecule" className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" value={targetChoice} onChange={(e) => setTargetChoice(e.target.value as TargetChoice)}>
                    <option value="current">Open molecule: {sequenceData.name}</option>
                    <option value="selection" disabled={!snapshot}>Selected range{snapshot ? `: ${snapshot.coordinateLabel}` : " (select bases in the sequence view)"}</option>
                    <option value="saved">Another saved molecule</option>
                    <option value="pasted">Pasted sequence</option>
                </select>
                {targetChoice === 'saved' && <div className="flex items-center gap-2">
                    <FragmentChooser label="Choose target" onPick={pick(setSavedTarget)} />
                    {savedTarget && <span className="text-slate-300">{savedTarget.name} · {savedTarget.sequence.length} bp</span>}
                </div>}
                {targetChoice === 'pasted' && <div className="space-y-1">
                    <input aria-label="Pasted target name" className="w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" placeholder="Name" value={pasteName} onChange={(e) => setPasteName(e.target.value)} />
                    <textarea aria-label="Pasted target sequence" className="h-16 w-full rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-slate-200" value={pasteSequence} onChange={(e) => setPasteSequence(e.target.value)} />
                    <label className="flex items-center gap-1 text-slate-300"><input type="checkbox" checked={pasteCircular} onChange={(e) => setPasteCircular(e.target.checked)} /> Circular</label>
                </div>}
            </fieldset>

            {method === 'golden_gate' && <p className="text-slate-400">{target?.sequence.length ?? 0} bp · {target?.circular ? 'circular' : 'linear'}. Cut after bp N = boundary N (not N + 1). Positions are relative to this target{targetChoice === 'selection' && snapshot ? `; target bp 1 starts at source bp ${snapshot.placement.start + 1}` : ''}. Type IIS sites are added to ordered fragments, not to the target DNA.</p>}
            {method !== 'golden_gate' && <fieldset className="min-w-0 space-y-1">
                <legend className="text-slate-300">{method === 'restriction' ? 'Insert (first source is used)' : 'Sources to build from'}</legend>
                <p className="text-slate-500">Only the molecules selected here are searched.</p>
                <label className="flex items-center gap-1 text-slate-300"><input type="checkbox" checked={useCurrentSource} onChange={(e) => setUseCurrentSource(e.target.checked)} /> Current molecule ({sequenceData.name})</label>
                <FragmentChooser label="Add saved source" onPick={pick((m) => setSources((list) => [...list, m]))} />
                <ul>{sources.map((m, i) => <li key={`${m.revision_id}-${i}`} className="flex min-w-0 justify-between gap-2 text-slate-300"><span className="min-w-0 break-words" title={m.name}>{m.name} · {m.sequence.length} bp</span>
                    <button type="button" className="shrink-0 text-slate-400" onClick={() => setSources((list) => list.filter((_, j) => j !== i))}>Remove</button></li>)}</ul>
            </fieldset>}

            {method === 'gibson' && <div className="flex flex-wrap gap-3">
                <Num label="Overlap (nt)" value={overlap} onChange={setOverlap} />
                <Num label="Target Tm (°C)" value={targetTm} onChange={setTargetTm} />
                <Num label="Min anneal (nt)" value={minAnneal} onChange={setMinAnneal} />
                <Num label="Min match (nt)" value={minMatch} onChange={setMinMatch} />
            </div>}
            {method === 'golden_gate' && <div className="space-y-2">
                <label className="flex flex-col gap-1 text-slate-400">Type IIS enzyme
                    <select aria-label="Type IIS enzyme" value={selectedEnzyme} onChange={e => setEnzyme(e.target.value)} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
                        {compatibleEnzymes.map(e => <option key={e.enzyme_id} value={e.enzyme_id}>{e.canonical_name} · {e.overhang_length} nt overhang</option>)}
                    </select>
                </label>
                <div className="flex flex-wrap gap-3">
                    <label className="flex flex-col gap-1 text-slate-400">Fragment count
                        <input type="number" min={1} className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" value={fragmentCount} onChange={(e) => setFragmentCount(e.target.value)} /></label>
                    {([['Target fragment length (bp)', targetLength, setTargetLength], ['Minimum fragment length (bp)', minLength, setMinLength], ['Maximum fragment length (bp)', maxLength, setMaxLength]] as const).map(([label, value, change]) => <label key={label} className="flex flex-col gap-1 text-slate-400">{label}<input type="number" min={1} value={value} onChange={e => change(e.target.value)} className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" /></label>)}
                    {([['Maximum homopolymer run at junction (nt)', maxHomopolymerRun, setMaxHomopolymerRun, 1], ['Homopolymer junction buffer (bp)', homopolymerBuffer, setHomopolymerBuffer, 0]] as const).map(([label, value, change, min]) => <label key={label} className="flex flex-col gap-1 text-slate-400">{label}<input type="number" min={min} step={1} value={value} onChange={e => change(e.target.value)} className="w-24 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" /></label>)}
                    <label className="flex items-center gap-1 text-slate-300"><input type="checkbox" checked={avoidRepeats} onChange={e => setAvoidRepeats(e.target.checked)} /> Avoid repeats when recommending cuts</label>
                    <label className="flex flex-col gap-1 text-slate-400">Prepared by
                        <select className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" value={preparation} onChange={(e) => setPreparation(e.target.value as 'synthesis' | 'pcr')}>
                            <option value="synthesis">Synthesis</option><option value="pcr">PCR</option></select></label>
                    {!target?.circular && <label className="flex flex-col gap-1 text-slate-400">Terminal right fusion (linear target)
                        <input className="w-32 rounded border border-slate-700 bg-slate-950 px-2 py-1 font-mono text-slate-200" value={fusion} onChange={(e) => setFusion(e.target.value)} /><span>Required {compatibleEnzymes.find(e => e.enzyme_id === selectedEnzyme)?.overhang_length ?? ""} nt bottom-strand protrusion, outside the exact target top strand.</span></label>}
                </div>
                <p className="text-slate-400">Avoid cut junctions through longer homopolymer tracts (blank maximum disables the rule); the target sequence remains unchanged, not sequence editing or a synthesis guarantee.</p>
                {discovery.isFetching && <p className="text-slate-400">Loading compatible enzymes…</p>}
                {discovery.error && <p role="alert">{errorText(discovery.error)}</p>}
            </div>}
            {method === 'restriction' && <div className="space-y-2">
                <label className="flex flex-col gap-1 text-slate-400">Enzymes (optional, comma-separated)
                    <input className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" value={restrictionEnzymes} onChange={(e) => setRestrictionEnzymes(e.target.value)} /></label>
                <p className="text-slate-500">Optional intervals use zero-based boundaries, with the end excluded. Blank donor bounds use the existing flanking sites, or the whole donor for primer tails.</p>
                <div className="flex flex-wrap gap-3">{([
                    ['Backbone cut-window start', backboneStart, setBackboneStart], ['Backbone cut-window end', backboneEnd, setBackboneEnd],
                    ['Donor payload start', insertStart, setInsertStart], ['Donor payload end', insertEnd, setInsertEnd],
                ] as const).map(([label, value, change]) => <label key={label} className="flex flex-col gap-1 text-slate-400">{label}
                    <input type="number" className="w-28 rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200"
                        value={value} onChange={(e) => change(e.target.value)} /></label>)}</div>
                <label className="flex flex-col gap-1 text-slate-400">Vector 5′ phosphate
                    <select value={vectorPhosphate} onChange={(e) => setVectorPhosphate(e.target.value as typeof vectorPhosphate)} className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200">
                        <option value="unknown">As digested</option><option value="unphosphorylated">Dephosphorylated</option><option value="phosphorylated">Phosphorylated</option>
                    </select></label>
            </div>}
            {method !== 'restriction' && <label className="flex flex-col gap-1 text-slate-400">Fixed boundaries (positions, comma-separated)
                <input aria-label="Fixed boundaries" className="rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200" value={boundaries} onChange={(e) => setBoundaries(e.target.value)} /></label>}

            {method === 'golden_gate' && target && <details open={builder} className="min-w-0 space-y-2">
                <summary>Target sequence and cut positions</summary>
                <p className="text-slate-400">Select bases to place a cut at the selection end, or enter exact boundaries above. Recommendations do not alter the target. Edit cuts, then run again before using a plan.</p>
                <div aria-label="Target sequence cut editor" className="relative min-w-0 overflow-hidden rounded border border-slate-700" style={{ height: 320 }}>
                <SequenceViewer viewMode="linear" sequenceData={{ name: target.name, sequence: target.sequence, circular: target.circular, sequenceType: 'dna', features: (target.features ?? []).map((f, i) => ({ ...f, id: f.id ?? `target-${i}`, strand: f.strand === -1 ? -1 : 1 })), primers: [] }} visibility={DEFAULT_VISIBILITY}
                    selection={previewSelection} onSelection={setPreviewSelection}
                    highlightedRegions={(goldenGate?.proposals[0]?.cuts ?? numbers(boundaries)).map(c => ({ start: c, end: Math.min(c + 1, target.sequence.length), color: '#8b5cf6' }))} />
                </div>
                <button type="button" disabled={!previewSelection} onClick={() => { if (previewSelection) { const boundary = target.circular && previewSelection.end === target.sequence.length ? 0 : previewSelection.end; setBoundaries([...new Set([...numbers(boundaries), boundary])].sort((a, b) => a - b).join(', ')); } }}>Add cut at selection end{previewSelection ? ` (after bp ${previewSelection.end})` : ''}</button>
                <details><summary>Unchanged target bases</summary><code data-target-bases className="block max-h-24 overflow-auto break-all">{target.sequence}</code></details>
            </details>}
            <button type="button" disabled={busy} onClick={() => void run()} className="rounded bg-cyan-600 px-3 py-1.5 font-medium text-white disabled:opacity-50">{busy ? 'Working…' : builder ? 'Recommend cuts' : 'Suggest'}</button>
            {error && <p role="alert" className="text-red-300">{error}</p>}

            {method === 'gibson' && gibson && <div className="space-y-2">
                {gibson.proposals.length === 0 && <p className="text-slate-400">No proposals.</p>}
                <ul className="space-y-2">{gibson.proposals.map((p) => <GibsonProposal key={p.rank} proposal={p} onUse={onUseGibson} />)}</ul>
                {gibson.diagnostics.map((d, i) => <p key={i} className="text-slate-500">{d}</p>)}
            </div>}
            {method === 'golden_gate' && goldenGate && <div className="space-y-2">
                {goldenGate.proposals.length === 0 && <p className="text-slate-400">No proposals.</p>}
                <ul className="space-y-2">{goldenGate.proposals.map((p) => <GoldenGateProposal key={p.rank} proposal={p} onUse={onUseGoldenGate} onEdit={editCuts} targetSequence={target?.sequence ?? ''} sourceBoundary={targetChoice === 'selection' && snapshot ? cut => (snapshot.placement.start + cut) % sequenceData.sequence.length : undefined} />)}</ul>
                <ul className="text-slate-500">{goldenGate.enzymes.map((e) => <li key={e.enzyme_id}>{e.name}: {e.status}{e.internal_sites != null ? ` · ${e.internal_sites} internal sites` : ''}{e.empirical === 'no empirical data' ? ' · no empirical data' : ''}</li>)}</ul>
                {goldenGate.diagnostics.map((d, i) => <p key={i} className="text-slate-500">{d}</p>)}
            </div>}
            {method === 'restriction' && restriction && <div className="space-y-2">
                {restriction.no_suitable_pair && <p className="text-slate-400">No suitable enzyme pair found. Alternatives: {restriction.alternatives.map((a) => a.note).join(' ')}</p>}
                <ul className="space-y-2">{restriction.proposals.map((p, i) => <li key={i} className="space-y-1 rounded border border-slate-700 bg-slate-950/60 p-2">
                    <div className="font-medium text-slate-100">#{i + 1} {proposalPairLabel(p)}{p.insert_orientation === 'forward' || p.insert_orientation === 'reverse' ? ` · insert ${p.insert_orientation}` : ''}{p.kind === 'primer_tail' ? ' (via primer tails)' : ''}</div>
                    <div className="text-slate-300">{p.directional ? 'Directional' : 'Not directional'}{p.insert_orientation === 'forward' || p.insert_orientation === 'reverse' ? '' : ` · insert orientation: ${p.insert_orientation}`} · vector self-ligation: {p.vector_self_ligation}</div>
                    <div className="text-slate-400">Vector {p.vector_length} bp + insert {p.insert_contributed_length} bp = product {p.product_length} bp</div>
                    {junctionLines(p.junction_sites).map((line, k) => <div key={k} className="text-slate-400">{line}</div>)}
                    <details className="text-slate-500"><summary className="cursor-pointer">Segments</summary>Retained backbone: {p.vector_source_segments.map(([a, b]) => `${a}–${b}`).join(', ')} · {p.kind === 'primer_tail' ? 'prepared insert' : 'donor'}: {p.insert_source_segments.map(([a, b]) => `${a}–${b}`).join(', ')}</details>
                    {p.selection.primers.map((primer) => <div key={primer.strand} className="break-all text-slate-300">{primer.strand === 1 ? 'Forward' : 'Reverse'} primer: <code>{primer.sequence}</code> <button type="button" className="ml-2 text-cyan-300" onClick={() => copy(primer.sequence)}>Copy primer</button></div>)}
                    {(p.primer_tails || p.selection.primer_notes.length > 0) && <details className="text-slate-500"><summary className="cursor-pointer">Primer tails and notes</summary>
                        {p.primer_tails && <div className="space-y-1">
                            <div className="break-all">Forward 5′ tail: <code>{p.primer_tails.forward_primer_5prime_tail}</code> <button type="button" className="ml-2 text-cyan-300" onClick={() => copy(p.primer_tails!.forward_primer_5prime_tail)}>Copy</button></div>
                            <div className="break-all">Reverse 5′ tail: <code>{p.primer_tails.reverse_primer_5prime_tail}</code> <button type="button" className="ml-2 text-cyan-300" onClick={() => copy(p.primer_tails!.reverse_primer_5prime_tail)}>Copy</button></div>
                            <p>{p.primer_tails.note}</p>
                        </div>}
                        {p.selection.primer_notes.map((note, j) => <p key={j}>{note}</p>)}
                    </details>}
                    <div className="flex flex-wrap items-center gap-2 pt-1">
                    {p.prepared_insert_sequence && <button type="button" className="rounded border border-slate-600 px-2 py-0.5 text-cyan-300" onClick={() => copy(p.prepared_insert_sequence!)}>Copy prepared insert DNA</button>}
                    <button type="button" className="rounded border border-slate-600 px-2 py-0.5 text-cyan-300" onClick={() => copy(p.product_sequence)}>Copy product DNA</button>
                    <button type="button" disabled={busy} title="Creates reusable fragment children; save the final construct in Ligation." className="rounded bg-violet-600 px-2 py-1 text-white disabled:opacity-50" onClick={() => void useRestriction(p, i)}>Prepare fragments and use this plan</button>
                    </div>
                </li>)}</ul>
                {restriction.notes.map((n, i) => <p key={i} className="text-slate-500">{n}</p>)}
            </div>}
        </section>
    );
}
