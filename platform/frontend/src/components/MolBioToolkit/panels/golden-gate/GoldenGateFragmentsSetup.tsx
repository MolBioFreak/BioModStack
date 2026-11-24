import { useEffect, useRef, useState } from 'react';
import {
    fetchMolecularRevision, suggestGoldenGatePlan,
    type ChooserItem, type GoldenGatePartSpec, type SuggestGoldenGateProposal, type SuggestGoldenGateRequest, type SuggestGoldenGateResponse, type SuggestGoldenGateSource,
} from '../../../../lib/api';
import type { WorkflowRequest } from '../../../../lib/goldenGateWorkflowTypes';
import { FragmentChooser } from '../../FragmentChooser';
import { GoldenGateOrderList } from './GoldenGateOrderList';
import { GoldenGatePartsList, type PartRow } from './GoldenGatePartsList';

type Mol = { name: string; sequence: string; circular: boolean; revision_id?: string };
type Enzyme = { enzyme_id: string; canonical_name: string; overhang_length: number };
const btn = 'rounded border border-slate-600 px-2 py-1 text-slate-200';
const inp = 'rounded border border-slate-700 bg-slate-950 px-2 py-1 text-slate-200';
const clean = (s: string) => s.toUpperCase().replace(/[^A-Z]/g, '');
const errorText = (e: unknown) => {
    const detail = (e as { response?: { data?: { detail?: unknown } } })?.response?.data?.detail;
    return typeof detail === 'string' ? detail : e instanceof Error ? e.message : String(e);
};
const sourceOf = (id: string, m: Mol): SuggestGoldenGateSource => ({ id, source: m.revision_id
    ? { kind: 'molecular_revision', revision_id: m.revision_id }
    : { kind: 'inline', sequence: m.sequence, topology: m.circular ? 'circular' : 'linear', features: [] } });
async function molFromItem(item: ChooserItem): Promise<Mol> {
    const rev = await fetchMolecularRevision(item.document_id, item.revision_id);
    const s = rev.snapshot as { sequence?: string; is_circular?: boolean };
    return { name: item.name, sequence: s.sequence ?? '', circular: s.is_circular === true, revision_id: item.revision_id };
}

/** Request body for parts mode; exported so tests can assert it exactly. */
export function buildPartsRequest(o: { rows: PartRow[]; enzymes: string[]; order: 'given' | 'auto'; vector: Mol | null; circular: boolean; preparation: 'synthesis' | 'pcr' }): SuggestGoldenGateRequest {
    const parts: GoldenGatePartSpec[] = o.rows.map((r, i) => ({
        source: sourceOf(`part${i + 1}`, { name: r.name, sequence: r.sequence, circular: r.circular, revision_id: r.revision_id }),
        name: r.name, orientation: r.orientation, kind: r.kind,
        ...(r.kind !== 'prepared' && r.left ? { left_overhang: r.left } : {}),
        ...(r.kind !== 'prepared' && r.right ? { right_overhang: r.right } : {}),
        ...(r.retained != null ? { retained_fragment_index: r.retained } : {}),
    }));
    return {
        enzymes: o.enzymes, parts, order: o.order, circular: o.vector ? false : o.circular,
        ...(o.vector ? { vector: sourceOf('vector', o.vector) } : {}),
        fixed_boundaries: [], preparation_kind: o.preparation, proposals: 3,
    };
}

function PlanCard({ p, rows, onUse }: { p: SuggestGoldenGateProposal; rows: PartRow[]; onUse: (r: WorkflowRequest) => void }) {
    const order = (p.part_order ?? rows.map((_, i) => i));
    return <li className="space-y-2 rounded border border-slate-700 bg-slate-950/60 p-2">
        <div className="font-medium text-slate-100">#{p.rank} {p.enzyme_name} · {p.f_set != null ? `F_set ${p.f_set.toFixed(3)}` : 'no fidelity number'}{p.final_sequence ? ` · final product ${p.final_sequence.length} bp` : ''}</div>
        <div className="text-slate-300">Part order: {order.map(i => rows[i]?.name ?? `part ${i + 1}`).join(' → ')}</div>
        {(p.junction_slots ?? []).length > 0 && <table aria-label="Junctions" className="w-full text-left text-slate-300">
            <thead><tr className="text-slate-500"><th>Junction</th><th>Overhang</th><th>Origin</th></tr></thead>
            <tbody>{p.junction_slots!.map(s => <tr key={s.index}><td>{s.index + 1}</td><td><code>{s.overhang}</code></td><td>{s.origin}</td></tr>)}</tbody></table>}
        <GoldenGateOrderList proposal={p} />
        {!!p.notes?.length && <ul aria-label="Planning notes" className="text-slate-400">{p.notes.map((n, i) => <li key={i}>{n}</li>)}</ul>}
        <button type="button" disabled={!p.assemble_request} className="rounded bg-violet-600 px-2 py-1 text-white disabled:opacity-50" onClick={() => onUse(p.assemble_request as unknown as WorkflowRequest)}>Use this plan</button>
    </li>;
}

export function GoldenGateFragmentsSetup({ enzymes, currentMolecule, selectedMolecule, onUse }: {
    enzymes: Enzyme[]; currentMolecule: Mol; selectedMolecule: Mol | null; onUse: (request: WorkflowRequest) => void;
}) {
    const [rows, setRows] = useState<PartRow[]>([]);
    const [chosen, setChosen] = useState<string[]>([]);
    const [order, setOrder] = useState<'given' | 'auto'>('given');
    const [preparation, setPreparation] = useState<'synthesis' | 'pcr'>('synthesis');
    const [vector, setVector] = useState<Mol | null>(null);
    const [vectorMode, setVectorMode] = useState<'none' | 'saved' | 'pasted'>('none');
    const [vectorPaste, setVectorPaste] = useState('');
    const [circular, setCircular] = useState(true);
    const [pasteName, setPasteName] = useState('');
    const [pasteSeq, setPasteSeq] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [result, setResult] = useState<{ data: SuggestGoldenGateResponse; rows: PartRow[] } | null>(null);
    const seq = useRef(0); const keys = useRef(0);
    const enzymeIds = chosen.length ? chosen : enzymes[0] ? [enzymes[0].enzyme_id] : [];
    useEffect(() => { seq.current++; setResult(null); setBusy(false); }, [rows, chosen, order, preparation, vector, vectorMode, vectorPaste, circular]);

    const add = (m: Mol) => setRows(list => [...list, { key: `p${++keys.current}`, name: m.name, sequence: m.sequence, circular: m.circular, revision_id: m.revision_id, orientation: 'forward', kind: 'auto', left: '', right: '', retained: null }]);
    const patch = (key: string, p: Partial<PartRow>) => setRows(list => list.map(r => r.key === key ? { ...r, ...p } : r));
    const move = (key: string, d: -1 | 1) => setRows(list => { const i = list.findIndex(r => r.key === key); const j = i + d; if (i < 0 || j < 0 || j >= list.length) return list; const c = [...list]; [c[i], c[j]] = [c[j], c[i]]; return c; });
    const activeVector: Mol | null = vectorMode === 'saved' ? vector : vectorMode === 'pasted' && clean(vectorPaste) ? { name: 'Pasted vector', sequence: clean(vectorPaste), circular: true } : null;

    const run = async (using: PartRow[] = rows) => {
        if (using.length === 0) { setError('Add at least one part.'); return; }
        if (!enzymeIds.length) { setError('Choose a compatible Type IIS enzyme.'); return; }
        const mine = ++seq.current; setBusy(true); setError(null);
        try {
            const { data } = await suggestGoldenGatePlan(buildPartsRequest({ rows: using, enzymes: enzymeIds, order, vector: activeVector, circular, preparation }));
            if (mine === seq.current) setResult({ data, rows: using });
        } catch (e) { if (mine === seq.current) setError(errorText(e)); }
        finally { if (mine === seq.current) setBusy(false); }
    };
    const pickFragment = (key: string, index: number) => {
        const next = rows.map(r => r.key === key ? { ...r, retained: index } : r);
        setRows(next);
        setTimeout(() => void run(next), 0);
    };
    const reports = result?.data.part_reports ?? [];

    return <div aria-label="Assemble from fragments" className="space-y-3">
        <fieldset className="space-y-2 rounded border border-slate-700 p-2">
            <legend className="px-1 text-slate-300">Parts</legend>
            <p className="text-slate-400">Bare DNA gets Type IIS sites in the order sequences only. Pre-configured fragments are used exactly as supplied.</p>
            <GoldenGatePartsList rows={rows} reports={reports} onChange={patch} onRemove={k => setRows(l => l.filter(r => r.key !== k))} onMove={move} onPickFragment={pickFragment} />
            <div className="flex flex-wrap items-center gap-2">
                <FragmentChooser label="Add part from saved molecules" onPick={(item) => { void molFromItem(item).then(add).catch(e => setError(errorText(e))); }} />
                <button type="button" className={btn} disabled={!currentMolecule.sequence} onClick={() => add(currentMolecule)}>Add open sequence as part</button>
                <button type="button" className={btn} disabled={!selectedMolecule} onClick={() => selectedMolecule && add(selectedMolecule)}>Add selected range as part</button>
            </div>
            <div className="space-y-1">
                <input aria-label="Pasted part name" className={`${inp} w-full`} placeholder="Part name" value={pasteName} onChange={e => setPasteName(e.target.value)} />
                <textarea aria-label="Pasted part sequence" className={`${inp} h-14 w-full font-mono`} value={pasteSeq} onChange={e => setPasteSeq(e.target.value)} />
                <button type="button" className={btn} disabled={!clean(pasteSeq)} onClick={() => { add({ name: pasteName.trim() || `Part ${rows.length + 1}`, sequence: clean(pasteSeq), circular: false }); setPasteSeq(''); setPasteName(''); }}>Add pasted sequence as part</button>
            </div>
        </fieldset>
        <fieldset className="space-y-2 rounded border border-slate-700 p-2">
            <legend className="px-1 text-slate-300">Assembly</legend>
            <div className="flex flex-wrap gap-3">
                <label className="flex flex-col gap-1 text-slate-400">Part order
                    <select aria-label="Part order" className={inp} value={order} onChange={e => setOrder(e.target.value as 'given' | 'auto')}><option value="given">Keep my order</option><option value="auto">Arrange by overhangs</option></select></label>
                <label className="flex flex-col gap-1 text-slate-400">Prepared by
                    <select aria-label="Fragments prepared by" className={inp} value={preparation} onChange={e => setPreparation(e.target.value as 'synthesis' | 'pcr')}><option value="synthesis">Synthesis</option><option value="pcr">PCR</option></select></label>
                <label className="flex flex-col gap-1 text-slate-400">Destination vector
                    <select aria-label="Destination vector" className={inp} value={vectorMode} onChange={e => setVectorMode(e.target.value as typeof vectorMode)}><option value="none">None (parts join to each other)</option><option value="saved">Saved molecule</option><option value="pasted">Pasted sequence</option></select></label>
                {vectorMode === 'none' && <label className="flex items-center gap-1 self-end text-slate-300"><input type="checkbox" checked={circular} onChange={e => setCircular(e.target.checked)} /> Circular product</label>}
            </div>
            {vectorMode === 'saved' && <div className="flex items-center gap-2"><FragmentChooser label="Choose vector" onPick={(item) => { void molFromItem(item).then(setVector).catch(e => setError(errorText(e))); }} />{vector && <span className="text-slate-300">{vector.name} · {vector.sequence.length} bp</span>}</div>}
            {vectorMode === 'pasted' && <textarea aria-label="Pasted vector sequence" className={`${inp} h-14 w-full font-mono`} value={vectorPaste} onChange={e => setVectorPaste(e.target.value)} />}
            <div role="group" aria-label="Type IIS enzymes for bare DNA" className="space-y-1">
                <div className="text-slate-400">Type IIS enzymes to try for bare DNA (tick one or more)</div>
                <div className="flex flex-wrap gap-3">{enzymes.map(e => <label key={e.enzyme_id} className="flex items-center gap-1 text-slate-300">
                    <input type="checkbox" checked={enzymeIds.includes(e.enzyme_id)} onChange={ev => setChosen(ev.target.checked ? [...enzymeIds, e.enzyme_id] : enzymeIds.filter(x => x !== e.enzyme_id))} />{e.canonical_name} · {e.overhang_length} nt overhang</label>)}</div>
            </div>
        </fieldset>
        <button type="button" disabled={busy} onClick={() => void run()} className="rounded bg-cyan-600 px-3 py-1.5 font-medium text-white disabled:opacity-50">{busy ? 'Working…' : 'Build plan from fragments'}</button>
        {error && <p role="alert" className="text-red-300">{error}</p>}
        {result && <div aria-label="Fragment assembly plans" className="space-y-2">
            {result.data.proposals.length === 0 && <p className="text-slate-400">No proposals.</p>}
            <ul className="space-y-2">{result.data.proposals.map(p => <PlanCard key={`${p.rank}-${p.enzyme_id}`} p={p} rows={result.rows} onUse={onUse} />)}</ul>
            <ul aria-label="Enzyme outcomes" className="text-slate-400">{result.data.enzymes.map(e => <li key={e.enzyme_id}>{e.name}: {e.status}{e.internal_sites != null ? ` · ${e.internal_sites} internal sites` : ''}</li>)}</ul>
            {result.data.diagnostics.map((d, i) => <p key={i} className="text-slate-500">{d}</p>)}
        </div>}
    </div>;
}
