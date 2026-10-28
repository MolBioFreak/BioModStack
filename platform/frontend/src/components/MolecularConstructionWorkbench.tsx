import { useEffect, useRef, useState } from 'react';
import { BioXpSchemaInput } from './BioXpSchemaInput';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { constructionApi, constructionError, editConstruction, emptyConstructionDraft, schemaDefinition, schemaEnums, undoConstruction } from '../lib/molecularConstruction';
import type { ConstructionEditor, ConstructionGraph, ConstructionSchemas, MolecularConstructionDraft } from '../lib/molecularConstruction';

export interface MolecularConstructionWorkbenchProps {
    /** Hydrate at mount; use a parent document key when intentionally loading another draft. */
    initialDraft?: MolecularConstructionDraft;
    onDraftChange: (draft: MolecularConstructionDraft) => void;
    /** Optional later geometry-owner receiving seam; no geometry API is called here. */
    onGraphReady?: (graph: ConstructionGraph) => void;
}
const obj = (v: unknown): ConstructionEditor => v && typeof v === 'object' && !Array.isArray(v) ? v as ConstructionEditor : {};
const rows = (v: unknown): ConstructionEditor[] => Array.isArray(v) ? v.map(obj) : [];
const fallback = (label: string, value: unknown, change: (v: unknown) => void) => <label>{label}<textarea aria-label={label} value={JSON.stringify(value, null, 2) ?? ''} onChange={e => { try { change(JSON.parse(e.target.value)); } catch { /* Unknown values remain intact until a valid replacement is authored. */ } }} /></label>;

export function MolecularConstructionWorkbench({ initialDraft, onDraftChange, onGraphReady }: MolecularConstructionWorkbenchProps) {
    const [draft, setDraft] = useState(() => structuredClone(initialDraft ?? emptyConstructionDraft()));
    const current = useRef(draft);
    const generation = useRef(0);
    const operation = useRef(0);
    const [schemas, setSchemas] = useState<ConstructionSchemas>();
    const [status, setStatus] = useState('Unpositioned recipe draft. No graph requested.');
    const [busy, setBusy] = useState(false);
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
    const choose = (label: string, options: unknown[], value: unknown, change: (v: string) => void) => <label>{label}<select aria-label={label} value={String(value ?? '')} onChange={e => change(e.target.value)}><option value="">Choose explicitly…</option>{options.map(v => <option key={String(v)} value={String(v)}>{String(v)}</option>)}</select></label>;
    const strands = rows(draft.editor.strands);
    const updateStrand = (index: number, next: ConstructionEditor) => edit({ ...draft.editor, strands: strands.map((s, i) => i === index ? next : s) });
    return <section aria-label="Molecular construction workbench" className="space-y-3 rounded border border-slate-700 p-3">
        <h3>Connected molecular recipe</h3>
        <p>Unpositioned chemistry companion, separate from imported coordinates and generator settings. Sequences run 5′→3′. Copies are not duplexes. No automatic complement, T/U conversion, pairing, protonation or geometry.</p>
        <p role="status">{status}</p>
        {schemas && <>
            {choose('Graph provider', schemaEnums(schemas.recipe, null, 'graph_provider'), draft.editor.graph_provider, graph_provider => edit({ ...draft.editor, graph_provider }))}
            {choose('Phosphate state', schemaEnums(schemas.recipe, null, 'phosphate_state'), draft.editor.phosphate_state, phosphate_state => edit({ ...draft.editor, phosphate_state }))}
            <p>Neutral acid is native graph chemistry, not a pH calculation. End chemistry must be authored separately.</p>
            {strands.map((strand, i) => <fieldset key={i}><legend>Strand {i + 1}</legend>
                <label>Stable strand ID<input aria-label={`Strand ${i + 1} ID`} value={String(strand.id ?? '')} onChange={e => updateStrand(i, { ...strand, id: e.target.value })} /></label>
                {choose(`Strand ${i + 1} polymer`, schemaEnums(schemas.recipe, 'Strand', 'polymer'), strand.polymer, polymer => updateStrand(i, { ...strand, polymer }))}
                <label>Sequence 5′→3′<input aria-label={`Strand ${i + 1} sequence`} value={rows(strand.residues).map(r => String(r.base ?? '')).join('')} onChange={e => updateStrand(i, { ...strand, residues: Array.from(e.target.value).map((base, n) => ({ ...rows(strand.residues)[n], id: rows(strand.residues)[n]?.id ?? `r-${crypto.randomUUID()}`, base })) })} /></label>
                <p>Sequence edits retain positional residue IDs; use the residue table to author explicit identity/reorder. Invalid bases remain editable and are reported by the graph API.</p>
                {(['five_prime', 'three_prime'] as const).map(end => <span key={end}>{choose(`Strand ${i + 1} ${end}`, schemaEnums(schemas.recipe, 'Strand', end), strand[end], value => updateStrand(i, { ...strand, [end]: value }))}</span>)}
                {field(`Strand ${i + 1} copies`, { type: 'array', items: { type: 'string' } }, strand.copy_ids ?? [], copy_ids => updateStrand(i, { ...strand, copy_ids }))}
                <details><summary>Residue identities, nicks and complete strand</summary>{field(`Strand ${i + 1} details`, schemaDefinition(schemas.recipe, 'Strand'), strand, value => updateStrand(i, obj(value)))}</details>
                <button type="button" onClick={() => edit({ ...draft.editor, strands: strands.filter((_, n) => n !== i) })}>Remove strand {i + 1}</button>
            </fieldset>)}
            <button type="button" onClick={() => edit({ ...draft.editor, strands: [...strands, { id: '', polymer: '', residues: [], copy_ids: [], five_prime: '', three_prime: '' }] })}>Add independently authored strand</button>
            {(['pairing', 'replacements', 'covalent_links'] as const).map(name => <details key={name}><summary>{name === 'pairing' ? 'Explicit pairing, register and mismatches' : name === 'replacements' ? 'Base / sugar / linkage / terminal CCD replacements and atom maps' : 'Atom-qualified covalent links'}</summary>{field(name, schemas.recipe.properties?.[name] ?? {}, draft.editor[name] ?? [], value => edit({ ...draft.editor, [name]: value }))}</details>)}
            <details><summary>Explicit complement action</summary><p>Author source and all new identities/ends. This action creates only a canonical strand; it does not copy modifications or infer pairing.</p>
                <label>Use source strand<select aria-label="Complement source strand" value="" onChange={e => changeComplement({ ...draft.complement, source: structuredClone(strands[Number(e.target.value)]) })}><option value="">Select source…</option>{strands.map((s, i) => <option key={i} value={i}>{String(s.id || `Strand ${i + 1}`)}</option>)}</select></label>
                {field('Complement request', schemas.complement, draft.complement, value => changeComplement(obj(value)), schemas.complement)}
                <button type="button" disabled={busy} onClick={complement}>Create explicit complement</button>
            </details>
            <details><summary>Complete typed recipe (including imported fields)</summary>{field('Construction recipe', schemas.recipe, draft.editor, value => edit(obj(value)))}</details>
        </>}
        <button type="button" disabled={busy} onClick={build}>Build connected graph</button>
        <button type="button" disabled={!draft.undo.length} onClick={() => { generation.current++; operation.current++; setBusy(false); publish(undoConstruction(current.current)); setStatus('Exact recipe and graph snapshot restored.'); }}>Undo construction edit</button>
        {draft.graph && <section aria-label="Actual molecular graph"><h4>Retained graph · {draft.graph.geometry}</h4>
            <p>{draft.graph.atoms.length} atoms · {draft.graph.bonds.length} bonds · {draft.graph.connected_components.length} covalent components. Coordinates: none.</p>
            <p>{draft.graph.observations.join(' ')}</p>
            <details><summary>Atom-map / charge / stereochemistry inspection</summary><table><thead><tr><th>Atom identity</th><th>Native component / atom</th><th>Element</th><th>Charge</th><th>CIP / native tag</th><th>H</th></tr></thead><tbody>{draft.graph.atoms.map(a => <tr key={a.id}><td>{a.id}</td><td>{a.component_id} / {a.native_atom_name}</td><td>{a.element}</td><td>{a.formal_charge}</td><td>{a.cip ?? '—'} / {a.chiral_tag}</td><td>{a.hydrogen_count}</td></tr>)}</tbody></table></details>
            <details><summary>Actual bonds, provider provenance and complete graph</summary><pre>{JSON.stringify(draft.graph, null, 2)}</pre></details>
        </section>}
    </section>;
}
