import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from 'react';
import { AtomWorksStudioHost } from './AtomWorksStudioHost';
import { importStudio, type StudioCapture } from '../lib/atomworksStudio';
import { adoptAnnotatedCif, type CapturedAnnotatedDocument, type DocumentCaptureRegistry } from '../lib/deNovoDocumentCapture';
import { buildFileDownloadUrl, materializeExactStructure, type NativeGenerationSourceBindings, type StructureMaterialization, type StructureSourceSelection } from '../lib/api';

type Editor = { id: number; slot: string; original: StructureSourceSelection; parent: StructureSourceSelection; receipt: StructureMaterialization; sessionId: string; acceptedBindings: Set<string> };
type Actions = {
    open: (slot: string, source: StructureSourceSelection, receipt?: StructureMaterialization) => Promise<void>;
    panels: ReactNode;
    /** True while a Studio editor is opening or open, so the form can make room for the canvas. */
    active: boolean;
};
const EditorsContext = createContext<Actions | null>(null);
export const useDeNovoDocumentEditors = () => useContext(EditorsContext);
export function DeNovoDocumentEditorPanels() { return <>{useDeNovoDocumentEditors()?.panels}</>; }
/** Form column plus Studio canvas. Wide screens put the canvas beside the form while an editor is open;
 * narrow screens keep the editor under the form as before. */
export function DeNovoEditorLayout({ children }: { children: ReactNode }) {
    const split = Boolean(useDeNovoDocumentEditors()?.active);
    return <div data-editor-split={split ? 'true' : 'false'}
        className={split ? 'space-y-5 xl:grid xl:grid-cols-[minmax(0,5fr)_minmax(0,7fr)] xl:items-start xl:gap-x-5 xl:space-y-0' : 'space-y-5'}>{children}</div>;
}
// Property order is not source identity; compare every retained selector field.
const key = (value: unknown) => JSON.stringify(value, (_name, item) => item && typeof item === 'object' && !Array.isArray(item)
    ? Object.fromEntries(Object.keys(item).sort().map(name => [name, item[name]])) : item);
// Source custody already selects the actual MODEL ID. Studio's model is a
// frame ordinal in that single-model derivative, not the original MODEL ID.
// Do not expand assembly 1 implicitly on opening an exact source document.
const studioImportOptions = { parse_preset: 'minimal', assembly: 'asym', model: '1',
    advanced_options: JSON.stringify({ load_standard_annotations: true, altloc: 'first' }) };

/** One mount-local editor per opened source, shared by task and advanced source rows.
 * Sessions remain ephemeral; all successful captures use immutable source custody.
 */
export function DeNovoDocumentEditors({ registry, sources, molecularDocuments, onAdopt, children }: {
    registry: DocumentCaptureRegistry; sources: NativeGenerationSourceBindings | undefined;
    molecularDocuments?: Record<string, CapturedAnnotatedDocument>;
    onAdopt: (slot: string, document: CapturedAnnotatedDocument) => void;
    children: ReactNode;
}) {
    const [editors, setEditors] = useState<Editor[]>([]);
    const [active, setActive] = useState<Record<string, number>>({});
    const [error, setError] = useState('');
    const sequence = useRef(0);
    const panel = useRef<HTMLDivElement>(null);
    const [opening, setOpening] = useState('');
    const [focusRevision, setFocusRevision] = useState(0);
    const focusEditor = useRef<number | undefined>(undefined);
    useEffect(() => {
        if (!focusRevision) return;
        const target = panel.current?.querySelector<HTMLElement>(`[data-document-editor="${focusEditor.current}"]`) ?? panel.current;
        target?.focus(); target?.scrollIntoView?.({ block: 'nearest' });
    }, [focusRevision]);
    const live = useRef(true);
    const current = useRef({ identity: key(sources), generation: 0, sources });
    const identity = key(sources);
    if (current.current.identity !== identity) current.current = { identity, generation: current.current.generation + 1, sources };
    else current.current.sources = sources;
    const pending = useRef(new Map<string, number>());
    const opened = useRef(editors); opened.current = editors;
    useEffect(() => { live.current = true; return () => { live.current = false; pending.current.clear(); }; }, []);
    const open = async (slot: string, source: StructureSourceSelection, _known?: StructureMaterialization) => {
        setFocusRevision(value => value + 1);
        const existing = opened.current.find(editor => editor.slot === slot && editor.acceptedBindings.has(key(source)));
        focusEditor.current = existing?.id;
        if (existing) { setActive(previous => ({ ...previous, [slot]: existing.id })); return; }
        const token = ++sequence.current;
        const generation = current.current.generation;
        pending.current.set(slot, token); setError(''); setOpening(slot);
        const stillCurrent = () => live.current && pending.current.get(slot) === token
            && current.current.generation === generation && key(current.current.sources?.[slot]) === key(source);
        try {
            // A display/native receipt is not necessarily the representation
            // Studio consumes (PDB, encoded BCIF, or an unselected original).
            // Reacquire through the exact source owner; never relabel its bytes.
            const receipt = await materializeExactStructure({ ...source, output_format: 'cif' });
            if (!stillCurrent()) return;
            if (receipt.model_number == null && receipt.model_numbers.length > 1) {
                throw new Error('Choose the source MODEL before opening its editable derivative. The saved source and Generate are unchanged.');
            }
            const path = receipt.path;
            if (!path) throw new Error('The selected document has no editable decoded structure. Its original source is unchanged.');
            const response = await fetch(buildFileDownloadUrl(path), { credentials: 'same-origin', cache: 'no-store' });
            if (!response.ok) throw new Error(`Source readback failed (${response.status})`);
            const bytes = await response.arrayBuffer();
            const expected = receipt.sha256;
            const digest = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), byte => byte.toString(16).padStart(2, '0')).join('');
            if (expected && digest !== expected) throw new Error('The editable source bytes changed after inspection. Reload the source; its saved selector is unchanged.');
            if (!stillCurrent()) return;
            const session = await importStudio(new File([bytes], 'source.cif'), studioImportOptions);
            if (!stillCurrent()) return;
            const retained = molecularDocuments?.[slot];
            const original = retained && key(retained.source) === key(source) ? retained.original_source ?? source : source;
            const editor = { id: token, slot, original: structuredClone(original), parent: structuredClone(source), receipt, sessionId: session.session_id, acceptedBindings: new Set([key(source)]) };
            focusEditor.current = editor.id;
            opened.current = [...opened.current, editor]; setEditors(opened.current);
            setActive(previous => ({ ...previous, [slot]: editor.id }));
            setFocusRevision(value => value + 1);
        } catch (cause) { if (stillCurrent()) setError(cause instanceof Error ? cause.message : String(cause)); }
        finally { if (pending.current.get(slot) === token) { pending.current.delete(slot); setOpening(''); setFocusRevision(value => value + 1); } }
    };
    const close = (id: number) => {
        opened.current = opened.current.filter(editor => editor.id !== id); setEditors(opened.current);
    };
    return <EditorsContext.Provider value={{ open, active: Boolean(opening) || editors.length > 0, panels: <div ref={panel} tabIndex={-1} aria-label="Molecular document editor" className="min-w-0 space-y-5">
        {opening && <p role="status">Opening {opening} in AtomWorks Studio…</p>}
        {error && <p role="status">Studio unavailable: {error}</p>}
        {editors.map(editor => <DocumentEditor key={editor.id} editor={editor} source={sources?.[editor.slot]} selected={active[editor.slot] === editor.id}
            registry={registry} onAdopt={document => { setActive(previous => ({ ...previous, [editor.slot]: editor.id })); onAdopt(editor.slot, document); }} onClose={() => close(editor.id)} />)}
    </div> }}>{children}</EditorsContext.Provider>;
}

function DocumentEditor({ editor, source, selected, registry, onAdopt, onClose }: {
    editor: Editor; source: NativeGenerationSourceBindings[string] | undefined; selected: boolean; registry: DocumentCaptureRegistry;
    onAdopt: (document: CapturedAnnotatedDocument) => void; onClose: () => void;
}) {
    const host = useRef<StudioCapture>(null);
    const accepted = useRef(editor.acceptedBindings);
    const attached = selected && accepted.current.has(key(source));
    const binding = useRef({ key: key(source), generation: 0 });
    if (binding.current.key !== key(source)) binding.current = { key: key(source), generation: binding.current.generation + 1 };
    const attachedRef = useRef(attached); attachedRef.current = attached;
    const [dirty, setDirty] = useState(false);
    const [error, setError] = useState('');
    const [busy, setBusy] = useState(false);
    const [captured, setCaptured] = useState<CapturedAnnotatedDocument>();
    const lifetime = useRef(0);
    useEffect(() => () => { lifetime.current++; }, []);
    const capture = useRef<() => Promise<CapturedAnnotatedDocument>>(async () => { throw new Error('Studio is opening'); });
    capture.current = async () => {
        const handle = host.current;
        if (!handle) throw new Error('Studio is opening. Existing saved source is unchanged.');
        const token = lifetime.current;
        const sourceGeneration = binding.current.generation;
        const generation = handle.generation();
        const snapshot = await handle.capture();
        const document = await adoptAnnotatedCif(snapshot.cif, { originalSource: editor.original,
            provenance: { editor: 'atomworks-studio', parent_source: editor.parent, revision: snapshot.revision, name: snapshot.name,
                issues: snapshot.issues, imported_source: editor.receipt.source_identity,
                imported_model: editor.receipt.model_number ?? null,
                imported_models: editor.receipt.model_numbers,
                imported_original: { path: editor.receipt.original_path, sha256: editor.receipt.original_sha256 },
                imported_document: { path: editor.receipt.path, sha256: editor.receipt.sha256, format: editor.receipt.format },
                import_options: studioImportOptions } });
        // This is readback of the already frozen persisted bytes, not another
        // mutable Studio GET. Inspection failure does not change admission.
        try {
            const receipt = await materializeExactStructure(document.source);
            document.provenance = { ...document.provenance, exported_models: receipt.model_numbers,
                ...(receipt.inspection_error ? { inspection_error: receipt.inspection_error } : {}) };
            if (receipt.model_numbers.length === 1) document.source = { ...document.source, model_number: receipt.model_numbers[0] };
        } catch (cause) {
            document.provenance = { ...document.provenance, inspection_error: cause instanceof Error ? cause.message : String(cause) };
        }
        if (lifetime.current !== token || host.current !== handle || handle.generation() !== generation || binding.current.generation !== sourceGeneration) {
            throw new Error('The molecular document changed during capture. Your editor remains open; capture the current edits again.');
        }
        accepted.current.add(key(document.source));
        setCaptured(document); setDirty(false); setError('');
        return document;
    };
    useEffect(() => {
        if (!attached) return;
        return registry.register(editor.slot, {
            generation: () => host.current?.generation() ?? -1,
            capture: async () => {
                try { return await capture.current(); }
                catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); throw cause; }
            },
        });
    }, [attached, editor.slot, registry]);
    const useEdited = async () => {
        setBusy(true); setError('');
        try { onAdopt(await capture.current()); }
        catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
        finally { setBusy(false); }
    };
    const download = async () => {
        setError('');
        try {
            if (!host.current) throw new Error('Studio is opening');
            const snapshot = await host.current.capture();
            const url = URL.createObjectURL(new Blob([snapshot.cif], { type: 'chemical/x-mmcif' }));
            const link = document.createElement('a'); link.href = url; link.download = 'edited-document.cif'; link.click();
            setTimeout(() => URL.revokeObjectURL(url), 0);
        } catch (cause) { setError(cause instanceof Error ? cause.message : String(cause)); }
    };
    return <section tabIndex={-1} data-document-editor={editor.id} className="space-y-3 rounded border border-slate-600 p-3" aria-label={`Molecular editor for ${editor.slot}`}>
        <h3>Editable document · {editor.slot}</h3>
        <p className="text-xs">Source MODEL {editor.receipt.model_number ?? editor.receipt.model_numbers[0]} · exact source coordinates (no assembly expansion).
            Studio uses its first-altloc policy; all original alternate locations remain in the retained source.</p>
        {!attached && <p role="status">This editor belongs to a replaced source. Its edits remain here for recovery and are not used by Save or Generate unless explicitly adopted.</p>}
        {dirty && <p role="status">Uncaptured edits. Save, Template, Project, Preview and Generate capture this exact document while it is attached.</p>}
        {captured && !dirty && <p className="text-xs">Immutable source captured. This does not mean a template, project or Job was saved.</p>}
        {error && <p role="alert">{error}. Existing saved source is unchanged.</p>}
        <AtomWorksStudioHost ref={host} sessionId={editor.sessionId} onDirty={() => { setDirty(true); if (attachedRef.current) registry.changed(); }} />
        <div className="flex flex-wrap gap-2">
            <button type="button" disabled={busy} onClick={() => void useEdited()}>{busy ? 'Capturing document…' : `Use edited document for ${editor.slot}`}</button>
            <button type="button" onClick={() => void download()}>Download frozen CIF for recovery</button>
            <button type="button" onClick={onClose}>{dirty ? 'Close editor and discard uncaptured edits' : 'Close editor'}</button>
        </div>
    </section>;
}
