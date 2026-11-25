import { useEffect, useRef, useState } from 'react';
import { MolecularGeometryControls } from './MolecularGeometryControls';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { constructionApi, constructionError, geometryApi, editGeometry, editConstruction, emptyConstructionDraft, schemaDefinition, schemaEnums, undoConstruction } from '../lib/molecularConstruction';
import type { ConstructionEditor, ConstructionGraph, ConstructionSchemas, MolecularConstructionDraft, MolecularGeometryResult } from '../lib/molecularConstruction';

export interface MolecularConstructionWorkbenchProps {
    /** Hydrate at mount; use a parent document key when intentionally loading another draft. */
    initialDraft?: MolecularConstructionDraft;
    onDraftChange: (draft: MolecularConstructionDraft) => void;
    /** Successful current geometry only; not source adoption. */
    onPositioned?: (result: MolecularGeometryResult) => void;
    /** Successful unpositioned graph only. */
    onGraphReady?: (graph: ConstructionGraph) => void;
}
const obj = (v: unknown): ConstructionEditor => v && typeof v === 'object' && !Array.isArray(v) ? v as ConstructionEditor : {};
const rows = (v: unknown): ConstructionEditor[] => Array.isArray(v) ? v.map(obj) : [];
const fieldInputClass = 'w-full min-w-0 rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2.5 py-1.5 text-sm text-[var(--text-primary)] focus:border-[var(--accent-primary)] focus:outline-none';
const btnBase = 'inline-flex items-center justify-center rounded-lg border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-1.5 text-xs font-medium text-[var(--text-primary)] transition hover:bg-[var(--bg-primary)] disabled:cursor-not-allowed disabled:opacity-45';
const btnPrimary = 'inline-flex items-center justify-center rounded-lg bg-sky-600 px-3.5 py-1.5 text-xs font-semibold text-white transition hover:bg-sky-500 disabled:cursor-not-allowed disabled:opacity-45';
const disclosureClass = 'rounded-lg border border-[var(--border-primary)] bg-[var(--bg-secondary)]/60 px-3 py-2 text-xs';
const summaryClass = 'cursor-pointer select-none font-medium text-[var(--text-primary)]';
const fallback = (label: string, value: unknown, change: (v: unknown) => void) => <label className="block space-y-1 text-xs font-medium text-[var(--text-secondary)]">{label}<textarea className="block w-full rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2 font-mono text-xs text-[var(--text-primary)]" rows={4} aria-label={label} value={JSON.stringify(value, null, 2) ?? ''} onChange={e => { try { change(JSON.parse(e.target.value)); } catch { /* Unknown values remain intact until a valid replacement is authored. */ } }} /></label>;

export function MolecularConstructionWorkbench({ initialDraft, onDraftChange, onGraphReady, onPositioned }: MolecularConstructionWorkbenchProps) {
    const [draft, setDraft] = useState(() => structuredClone(initialDraft ?? emptyConstructionDraft()));
    const current = useRef(draft);
    const generation = useRef(0);
    const operation = useRef(0);
    const [schemas, setSchemas] = useState<ConstructionSchemas>();
    const [status, setStatus] = useState('Unpositioned recipe draft. No graph requested.');
    const [busy, setBusy] = useState(false);
    const [geometryOpen, setGeometryOpen] = useState(Boolean(initialDraft?.geometry));
    const changeGeometry = (choices: NonNullable<MolecularConstructionDraft['geometry']>['choices']) => {
        generation.current++; operation.current++; setBusy(false);
        publish(editGeometry(current.current, choices));
        setStatus('Geometry choices edited; previous result retained in Undo, not current.');
    };
    const position = async () => {
        const at = generation.current, requestId = ++operation.current;
        const request = { ...structuredClone(current.current.geometry?.choices ?? {}), construction: structuredClone(current.current.editor) };
        const next = editGeometry(current.current, current.current.geometry?.choices ?? {});
        publish({ ...next, geometry: { ...next.geometry!, request } });
        setBusy(true); setStatus('Constructing explicitly requested native idealized geometry…');
        try {
            const result = await geometryApi.construct(request);
            if (at !== generation.current || requestId !== operation.current) return;
            publish({ ...current.current, geometry: { ...current.current.geometry!, result } });
            setStatus('Native idealized geometry ready; not adopted as a source.');
            onPositioned?.(structuredClone(result));
        } catch (e) {
            if (at === generation.current && requestId === operation.current) setStatus(`Geometry unavailable: ${constructionError(e)}. Draft retained; graph/import/manual paths and model launch are unchanged.`);
        } finally { if (at === generation.current && requestId === operation.current) setBusy(false); }
    };
    useEffect(() => { let live = true; constructionApi.schemas().then(s => { if (live) setSchemas(s); }).catch(e => { if (live) setStatus(`Schema discovery unavailable: ${constructionError(e)}. Model launch is unchanged.`); }); return () => { live = false; generation.current++; operation.current++; }; }, []);
    const publish = (next: MolecularConstructionDraft) => { current.current = next; setDraft(next); onDraftChange(structuredClone(next)); };
    const edit = (editor: ConstructionEditor) => { generation.current++; operation.current++; setBusy(false); publish(editConstruction(current.current, editor)); setStatus('Edited; previous graph retained in Undo, not current.'); };
    const changeComplement = (complement: ConstructionEditor) => { generation.current++; operation.current++; setBusy(false); publish({ ...current.current, complement }); };
    const build = async () => {
        const at = generation.current, request = ++operation.current;
        const editor = structuredClone(current.current.editor);
        setBusy(true); setStatus('Constructing actual connected graph…');
        try {
            const graph = await constructionApi.graph(editor);
            if (at !== generation.current || request !== operation.current) return;
            publish({ ...current.current, graph }); setStatus('Connected graph ready — unpositioned, no source CIF.'); onGraphReady?.(structuredClone(graph));
        } catch (e) { if (at === generation.current && request === operation.current) setStatus(`Graph unavailable: ${constructionError(e)}. Recipe retained; model launch is unchanged.`); }
        finally { if (at === generation.current && request === operation.current) setBusy(false); }
    };
    const complement = async () => {
        const at = generation.current, request = ++operation.current;
        setBusy(true);
        try {
            const strand = await constructionApi.complement(structuredClone(current.current.complement));
            if (at !== generation.current || request !== operation.current) return;
            edit({ ...current.current.editor, strands: [...rows(current.current.editor.strands), strand] });
            setStatus('Explicit complement appended. No pairing/register or modifications inferred.');
        } catch (e) { if (at === generation.current && request === operation.current) setStatus(`Complement unavailable: ${constructionError(e)}. Draft retained.`); }
        finally { if (at === generation.current && request === operation.current) setBusy(false); }
    };
    const field = (label: string, schema: Schema, value: unknown, onChange: (v: unknown) => void, root = schemas?.recipe) => <BioXpSchemaInput label={label} schema={schema} rootSchema={root} useSchemaTitles value={value} onChange={onChange} fallback={fallback} />;
    const choose = (label: string, options: unknown[], value: unknown, change: (v: string) => void) => <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-secondary)]">{label}<select className={fieldInputClass} aria-label={label} value={String(value ?? '')} onChange={e => change(e.target.value)}><option value="">Choose explicitly…</option>{options.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}</select></label>;
    const strands = rows(draft.editor.strands);
    const updateStrand = (index: number, next: ConstructionEditor) => edit({ ...draft.editor, strands: strands.map((s, i) => i === index ? next : s) });
    const statusTone = /unavailable/i.test(status)
        ? 'border-amber-500/40 bg-amber-500/10 text-amber-200'
        : /ready|restored|appended/i.test(status)
            ? 'border-emerald-500/40 bg-emerald-500/10 text-emerald-200'
            : 'border-[var(--border-primary)] bg-[var(--bg-secondary)] text-[var(--text-secondary)]';
    return <section aria-label="Molecular construction workbench" className="space-y-3.5 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)]/40 p-4">
        <div className="flex flex-wrap items-start justify-between gap-2 border-b border-[var(--border-primary)]/70 pb-2.5">
            <div className="space-y-1">
                <h3 className="text-sm font-semibold text-[var(--text-primary)]">Connected molecular recipe</h3>
                <p className="text-xs text-[var(--text-secondary)]">Unpositioned chemistry companion, separate from imported coordinates and generator settings. Sequences run 5′→3′. Copies are not duplexes. No automatic complement, T/U conversion, pairing, protonation or geometry.</p>
            </div>
            <span className="rounded-full border border-[var(--border-primary)] bg-[var(--bg-secondary)] px-2.5 py-0.5 text-[11px] font-medium text-[var(--text-secondary)]">{strands.length} strand{strands.length === 1 ? '' : 's'}</span>
        </div>
        <p role="status" className={`rounded-lg border px-3 py-2 text-xs ${statusTone}`}>{status}</p>
        {schemas && <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
                {choose('Graph provider', schemaEnums(schemas.recipe, null, 'graph_provider'), draft.editor.graph_provider, graph_provider => edit({ ...draft.editor, graph_provider }))}
                {choose('Phosphate state', schemaEnums(schemas.recipe, null, 'phosphate_state'), draft.editor.phosphate_state, phosphate_state => edit({ ...draft.editor, phosphate_state }))}
            </div>
            <p className="text-[11px] text-[var(--text-secondary)]">Neutral acid is native graph chemistry, not a pH calculation. End chemistry must be authored separately.</p>
            {strands.length > 0 && <div className="space-y-2.5">
                {strands.map((strand, i) => {
                    const seq = rows(strand.residues).map(r => String(r.base ?? '')).join('');
                    return <fieldset key={i} className="space-y-2.5 rounded-xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-3">
                        <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-primary)]/60 pb-2">
                            <legend className="text-xs font-semibold uppercase tracking-wide text-[var(--text-primary)]">Strand {i + 1}{seq ? ` · ${seq.length} nt` : ''}</legend>
                            <button type="button" className="rounded-md border border-red-500/30 bg-red-500/10 px-2 py-1 text-[11px] font-medium text-red-300 hover:bg-red-500/20" onClick={() => edit({ ...draft.editor, strands: strands.filter((_, n) => n !== i) })}>Remove strand {i + 1}</button>
                        </div>
                        <div className="grid gap-2.5 sm:grid-cols-2 lg:grid-cols-4">
                            <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-secondary)]">Stable strand ID<input className={fieldInputClass} aria-label={`Strand ${i + 1} ID`} value={String(strand.id ?? '')} onChange={e => updateStrand(i, { ...strand, id: e.target.value })} /></label>
                            {choose(`Strand ${i + 1} polymer`, schemaEnums(schemas.recipe, 'Strand', 'polymer'), strand.polymer, polymer => updateStrand(i, { ...strand, polymer }))}
                            {(['five_prime', 'three_prime'] as const).map(end => <span key={end}>{choose(`Strand ${i + 1} ${end}`, schemaEnums(schemas.recipe, 'Strand', end), strand[end], value => updateStrand(i, { ...strand, [end]: value }))}</span>)}
                        </div>
                        <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-secondary)]">Sequence 5′→3′
                            <input className={`${fieldInputClass} font-mono tracking-wider`} aria-label={`Strand ${i + 1} sequence`} value={seq} onChange={e => updateStrand(i, { ...strand, residues: Array.from(e.target.value).map((base, n) => ({ ...rows(strand.residues)[n], id: rows(strand.residues)[n]?.id ?? `r-${crypto.randomUUID()}`, base })) })} />
                        </label>
                        <p className="text-[11px] text-[var(--text-secondary)]">Sequence edits retain positional residue IDs; use the residue table to author explicit identity/reorder. Invalid bases remain editable and are reported by the graph API.</p>
                        {field(`Strand ${i + 1} copies`, { type: 'array', items: { type: 'string' } }, strand.copy_ids ?? [], copy_ids => updateStrand(i, { ...strand, copy_ids }))}
                        <details className={disclosureClass}><summary className={summaryClass}>Residue identities, nicks and complete strand</summary><div className="mt-2">{field(`Strand ${i + 1} details`, schemaDefinition(schemas.recipe, 'Strand'), strand, value => updateStrand(i, obj(value)))}</div></details>
                    </fieldset>;
                })}
            </div>}
            <div className="flex flex-wrap items-center gap-2">
                <button type="button" className={btnBase} onClick={() => edit({ ...draft.editor, strands: [...strands, { id: '', polymer: '', residues: [], copy_ids: [], five_prime: '', three_prime: '' }] })}>Add independently authored strand</button>
            </div>
            <div className="space-y-2">
                {(['pairing', 'replacements', 'covalent_links'] as const).map(name => <details key={name} className={disclosureClass}><summary className={summaryClass}>{name === 'pairing' ? 'Explicit pairing, register and mismatches' : name === 'replacements' ? 'Base / sugar / linkage / terminal CCD replacements and atom maps' : 'Atom-qualified covalent links'}</summary><div className="mt-2">{field(name, schemas.recipe.properties?.[name] ?? {}, draft.editor[name] ?? [], value => edit({ ...draft.editor, [name]: value }))}</div></details>)}
                <details className={disclosureClass}><summary className={summaryClass}>Explicit complement action</summary>
                    <div className="mt-2 space-y-2.5">
                        <p className="text-[11px] text-[var(--text-secondary)]">Author source and all new identities/ends. This action creates only a canonical strand; it does not copy modifications or infer pairing.</p>
                        <label className="flex flex-col gap-1 text-xs font-medium text-[var(--text-secondary)] max-w-xs">Use source strand<select className={fieldInputClass} aria-label="Complement source strand" value="" onChange={e => changeComplement({ ...draft.complement, source: structuredClone(strands[Number(e.target.value)]) })}><option value="">Select source…</option>{strands.map((s, i) => <option key={i} value={i}>{String(s.id || `Strand ${i + 1}`)}</option>)}</select></label>
                        {field('Complement request', schemas.complement, draft.complement, value => changeComplement(obj(value)), schemas.complement)}
                        <button type="button" className={btnBase} disabled={busy} onClick={complement}>Create explicit complement</button>
                    </div>
                </details>
                <details className={disclosureClass}><summary className={summaryClass}>Complete typed recipe (including imported fields)</summary><div className="mt-2">{field('Construction recipe', schemas.recipe, draft.editor, value => edit(obj(value)))}</div></details>
            </div>
        </div>}
        <div className="flex flex-wrap items-center gap-2 border-t border-[var(--border-primary)]/70 pt-3">
            <button type="button" className={btnPrimary} disabled={busy} onClick={build}>Build connected graph</button>
            <button type="button" className={btnBase} disabled={!draft.undo.length} onClick={() => { generation.current++; operation.current++; setBusy(false); publish(undoConstruction(current.current)); setStatus('Exact recipe and graph snapshot restored.'); }}>Undo construction edit</button>
            <button type="button" className={btnBase} onClick={() => setGeometryOpen(true)}>Configure optional geometry</button>
        </div>
        {geometryOpen && <MolecularGeometryControls geometry={draft.geometry} busy={busy} onChange={changeGeometry} onConstruct={position} />}
        {draft.graph && <section aria-label="Actual molecular graph" className="space-y-3 rounded-xl border border-emerald-500/35 bg-[var(--bg-secondary)] p-3.5">
            <div className="flex flex-wrap items-center justify-between gap-2 border-b border-[var(--border-primary)]/60 pb-2">
                <h4 className="text-sm font-semibold text-[var(--text-primary)]">Retained graph · {draft.graph.geometry}</h4>
                <div className="flex flex-wrap items-center gap-1.5 text-[11px]">
                    <span className="rounded-full border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-0.5 font-mono text-[var(--text-primary)]">{draft.graph.atoms.length} atoms</span>
                    <span className="rounded-full border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-0.5 font-mono text-[var(--text-primary)]">{draft.graph.bonds.length} bonds</span>
                    <span className="rounded-full border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2 py-0.5 font-mono text-[var(--text-primary)]">{draft.graph.connected_components.length} covalent components</span>
                </div>
            </div>
            <p className="text-xs text-[var(--text-secondary)]">{draft.graph.atoms.length} atoms · {draft.graph.bonds.length} bonds · {draft.graph.connected_components.length} covalent components. Coordinates: none.</p>
            {draft.graph.isomeric_smiles && <div className="space-y-1">
                <span className="text-[11px] font-medium uppercase tracking-wide text-[var(--text-secondary)]">Isomeric SMILES</span>
                <pre className="overflow-x-auto whitespace-nowrap rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] px-2.5 py-1.5 font-mono text-[11px] text-[var(--text-primary)]">{draft.graph.isomeric_smiles}</pre>
            </div>}
            {draft.graph.observations.length > 0 && <p className="rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)]/60 px-2.5 py-2 text-xs text-[var(--text-secondary)]">{draft.graph.observations.join(' ')}</p>}
            <details className={disclosureClass}><summary className={summaryClass}>Atom-map / charge / stereochemistry inspection</summary>
                <div className="mt-2 max-h-64 overflow-auto rounded-lg border border-[var(--border-primary)]">
                    <table className="w-full border-collapse text-left text-xs">
                        <thead className="sticky top-0 bg-[var(--bg-primary)] text-[11px] uppercase tracking-wide text-[var(--text-secondary)]"><tr><th className="px-2.5 py-1.5">Atom identity</th><th className="px-2.5 py-1.5">Native component / atom</th><th className="px-2.5 py-1.5">Element</th><th className="px-2.5 py-1.5">Charge</th><th className="px-2.5 py-1.5">CIP / native tag</th><th className="px-2.5 py-1.5">H</th></tr></thead>
                        <tbody className="divide-y divide-[var(--border-primary)]/60 font-mono text-[11px]">
                            {draft.graph.atoms.map(a => <tr key={a.id} className="hover:bg-[var(--bg-primary)]/40"><td className="px-2.5 py-1">{a.id}</td><td className="px-2.5 py-1">{a.component_id} / {a.native_atom_name}</td><td className="px-2.5 py-1">{a.element}</td><td className="px-2.5 py-1">{a.formal_charge}</td><td className="px-2.5 py-1">{a.cip ?? '—'} / {a.chiral_tag}</td><td className="px-2.5 py-1">{a.hydrogen_count}</td></tr>)}
                        </tbody>
                    </table>
                </div>
            </details>
            <details className={disclosureClass}><summary className={summaryClass}>Actual bonds, provider provenance and complete graph</summary><pre className="mt-2 max-h-72 overflow-auto rounded-lg border border-[var(--border-primary)] bg-[var(--bg-primary)] p-2.5 font-mono text-[11px] text-[var(--text-secondary)]">{JSON.stringify(draft.graph, null, 2)}</pre></details>
        </section>}
    </section>;
}
