import { useEffect, useRef, useState } from 'react';
import { useBioXpWorkflowMutationOwner } from './BioXpWorkflowMutationOwner';
import { useQuery } from '@tanstack/react-query';
import './BioXpMethodsWorkspace.css';
import { liquidClassEditorSchema, methodParameterSchema, methodTipPolicySchema } from '../lib/bioxpMethodEditorSchema';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { bioXpErrorText } from '../lib/bioxpClient';
import { methodsGet, methodsPost, methodsSave, methodRows, newMethod, definiteMethodRefusal, type MethodValue, type MethodRecord, type MethodCollection, type MethodCatalog, type MethodCompile, type MethodRun } from '../lib/bioxpMethods';
import { canonicalWorkflowJobId } from '../lib/bioxpSavedWorkflowRun';
import { MethodFields, MethodOutline, object, duplicateNode, methodBindingsSchema } from './BioXpMethodFields';
import { BioXpWorkflowJobMonitor } from './BioXpWorkflowJobMonitor';
import { BioXpMethodPreview, BioXpMethodProgress, BioXpMethodApplicationFields } from './BioXpMethodPreview';
import { BioXpMethodProcedures } from './BioXpMethodProcedures';
import { BioXpWorkflowMaterials } from './BioXpWorkflowMaterials';
import type { WorkflowDeckPlan } from '../lib/bioxpWorkflowPlan';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { deckStations } from '../lib/bioxpWorkflowDeck';
import type { BioXpDeckSelection } from './BioXpWorkflowDeck';
const enc = encodeURIComponent;
const clone = <T,>(v: T): T => v === undefined ? v : JSON.parse(JSON.stringify(v));
const recordEqual = (a: unknown, b: unknown): boolean => {
    if (a === b) return true;
    if (Array.isArray(a) && Array.isArray(b)) return a.length === b.length && a.every((v, i) => recordEqual(v, b[i]));
    if (a && b && typeof a === 'object' && typeof b === 'object') { const x = object(a), y = object(b); return Object.keys(x).length === Object.keys(y).length && Object.keys(x).every(k => Object.hasOwn(y, k) && recordEqual(x[k], y[k])); }
    return false;
};
function download(value: unknown, filename: string) { const url = URL.createObjectURL(new Blob([JSON.stringify(value, null, 2)], { type: 'application/json' })); const a = document.createElement('a'); a.href = url; a.download = filename; a.click(); URL.revokeObjectURL(url); }
const reportProvenance = (report: MethodValue | null): MethodValue[] => {
    const rows = object(object(report?.method_snapshot).compilation).provenance;
    return Array.isArray(rows) ? rows as MethodValue[] : [];
};
const schemas = (raw: MethodValue) => object(raw.method_schema ?? raw.method ?? raw);
function Evidence({ title, value }: { title: string; value: unknown }) {
    if (value === undefined) return null;
    return <details><summary>{title}</summary>{Array.isArray(value) ? <ul>{value.map((item, i) => <li key={i}><Evidence title={String(object(item).field ?? object(item).occurrence_id ?? object(item).message ?? i)} value={item} /></li>)}</ul> : value && typeof value === 'object' ? <dl>{Object.entries(object(value)).map(([key, child]) => <div key={key}><dt>{key}</dt><dd>{typeof child === 'object' && child !== null ? <Evidence title={key} value={child} /> : String(child ?? 'unknown / null')}</dd></div>)}</dl> : <p>{String(value)}</p>}</details>;
}
export function BioXpMethodsWorkspace({ generation, connected, controlsEnabled, visible = true }: { generation: number; connected: boolean; controlsEnabled: boolean; visible?: boolean }) {
    const [tab, setTab] = useState('Methods');
    const [draft, setDraft] = useState<MethodValue>(newMethod);
    const [quick, setQuick] = useState<MethodValue>(newMethod);
    const [liquid, setLiquid] = useState<MethodValue>({ schema: 'bms.bioxp-liquid-class.v1', name: '' });
    const [savedMethod, setSavedMethod] = useState<MethodRecord | null>(null);
    const [savedLiquid, setSavedLiquid] = useState<MethodRecord | null>(null);
    const saveTargets = useRef<Partial<Record<MethodCollection | 'quick', MethodRecord>>>({});
    const [methodValues, setMethodValues] = useState({ bindings: {} as MethodValue, dependencies: {} as MethodValue, initialState: {} as unknown });
    const [quickValues, setQuickValues] = useState({ bindings: {} as MethodValue, dependencies: {} as MethodValue, initialState: {} as unknown });
    const { bindings, dependencies, initialState } = tab === 'Quick run' ? quickValues : methodValues;
    const setValues = tab === 'Quick run' ? setQuickValues : setMethodValues;
    const setBindings = (bindings: MethodValue) => { editVersion.current++; setValues(previous => ({ ...previous, bindings })); };
    const setDependencies = (dependencies: MethodValue) => { editVersion.current++; setValues(previous => ({ ...previous, dependencies })); };
    const setInitialState = (initialState: unknown) => { editVersion.current++; setValues(previous => ({ ...previous, initialState })); };
    const [check, setCheck] = useState<{ result: MethodCompile; input: MethodValue } | null>(null);
    const { busy, setBusy, busyRef } = useBioXpWorkflowMutationOwner();
    const [storageBusy, setStorageBusy] = useState(false), storageLock = useRef(false);
    const [error, setError] = useState<string | null>(null), [notice, setNotice] = useState<string | null>(null);
    const [search, setSearch] = useState(''), [offset, setOffset] = useState(0), [selected, setSelected] = useState('');
    const [revision, setRevision] = useState(''), [fromRevision, setFromRevision] = useState('');
    const [diff, setDiff] = useState<unknown>();
    const [ack, setAck] = useState(false);
    const [run, setRun] = useState<{ jobId: string; key: string; generation: number; snapshot: MethodValue; refused?: boolean } | null>(() => {
        try { const value = JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1') ?? 'null'); return value && /^protocol-live-[a-f0-9]{64}$/.test(value.jobId) ? value : null; } catch { return null; }
    });
    const [accepted, setAccepted] = useState<MethodRun | null>(null), [report, setReport] = useState<MethodValue | null>(null);
    const [liveObservation, setLiveObservation] = useState<unknown>(null);
    const [recoveryInitialState, setRecoveryInitialState] = useState<unknown>(undefined);
    const [recoveryEvidence, setRecoveryEvidence] = useState<MethodValue | null>(null);
    useEffect(() => { setRecoveryInitialState(undefined); setRecoveryEvidence(null); setOccurrence(''); setNativeAction(''); }, [run?.jobId]);
    const [occurrence, setOccurrence] = useState('');
    const [nativeAction, setNativeAction] = useState('');
    const [deckSelection, setDeckSelection] = useState<BioXpDeckSelection>({ station: '', wells: [] });
    const authority = useRef({ generation, connected }); authority.current = { generation, connected };
    const editVersion = useRef(0);
    const collection: MethodCollection = tab === 'Liquid classes' ? 'liquid-classes' : 'library';
    const saveTarget = tab === 'Quick run' ? 'quick' : collection;
    const activeDraft = tab === 'Quick run' ? quick : tab === 'Liquid classes' ? liquid : draft;
    const saved = collection === 'liquid-classes' ? savedLiquid : savedMethod;
    const setActive = (v: MethodValue) => { editVersion.current++; (tab === 'Quick run' ? setQuick : tab === 'Liquid classes' ? setLiquid : setDraft)(v); };
    const catalog = useQuery({ queryKey: ['bioxp', 'methods', 'catalog'], queryFn: () => methodsGet<MethodCatalog>('catalog'), retry: false, staleTime: Infinity });
    const schema = useQuery({ queryKey: ['bioxp', 'methods', 'schema'], queryFn: () => methodsGet<MethodValue>('schema'), retry: false, staleTime: Infinity });
    const examples = useQuery({ queryKey: ['bioxp', 'methods', 'examples'], queryFn: () => methodsGet<unknown>('examples'), retry: false, staleTime: Infinity });
    const library = useQuery({ queryKey: ['bioxp', 'methods', collection, search, offset], queryFn: () => methodsGet<unknown>(collection, { search, offset, limit: 25 }), retry: false, enabled: visible && tab !== 'Runs' });
    const revisions = useQuery({ queryKey: ['bioxp', 'methods', collection, selected, 'revisions'], queryFn: () => methodsGet<unknown>(`${collection}/${enc(selected)}/revisions`), retry: false, enabled: !!selected });
    const runs = useQuery({ queryKey: ['bioxp', 'methods', 'runs', generation, search, offset], queryFn: () => methodsGet<unknown>('runs', { expected_connection_generation: generation, search, offset, limit: 25 }), retry: false, enabled: visible && tab === 'Runs' && connected });
    const starters = useQuery({ queryKey: ['bioxp', 'methods', 'liquid-starters'], queryFn: () => methodsGet<unknown>('liquid-classes/starters'), enabled: visible && tab === 'Liquid classes', retry: false, staleTime: Infinity });
    const presets = useQuery({ queryKey: ['bioxp', 'methods', 'presets'], queryFn: () => methodsGet<unknown>('presets', { limit: 100 }), retry: false });
    const rootSchema = schemas(schema.data ?? {}) as Schema;
    const properties = rootSchema.properties ?? {};
    useEffect(() => { setSelected(''); setRevision(''); setFromRevision(''); setOffset(0); }, [collection]);
    async function storage(action: () => Promise<void>) {
        if (storageLock.current) return; storageLock.current = true; setStorageBusy(true); setError(null);
        try { await action(); } catch (e) { setError(bioXpErrorText(e)); } finally { storageLock.current = false; setStorageBusy(false); }
    }
    async function save() {
        const value = clone(activeDraft), version = editVersion.current, lane = collection;
        // UserTemplate stores the raw method. Keep its authored compile inputs in
        // presentation state as well, so a cold Open cannot lose recovery science.
        if (lane === 'library' && (Object.keys(bindings).length || Object.keys(dependencies).length || !recordEqual(initialState, {}) || object(value.editor_state).run_inputs)) {
            value.editor_state = { ...object(value.editor_state), run_inputs: clone({ bindings, dependencies, ...(initialState !== undefined ? { initial_state: initialState } : {}) }) };
        }
        await storage(async () => {
            const receipt = await methodsSave(lane, value, saveTargets.current[saveTarget] ?? (tab === 'Quick run' ? null : saved));
            // Keep accepted identity before GET: failed readback retry updates, not a duplicate create.
            saveTargets.current[saveTarget] = receipt;
            const exact = await methodsGet<MethodRecord>(`${lane}/${enc(receipt.id)}/revisions/${receipt.revision}`);
            if (!recordEqual(exact.method, value)) throw new Error('Saved revision readback differs; original editable draft retained.');
            if (lane === 'liquid-classes') setSavedLiquid(exact);
            else if (tab !== 'Quick run') setSavedMethod(exact);
            else if (version === editVersion.current) { saveTargets.current.library = exact; setSavedMethod(exact); setDraft(clone(value)); setMethodValues(clone(quickValues)); }
            setNotice(version === editVersion.current ? `Saved exact revision ${exact.revision}.` : `Revision ${exact.revision} saved; newer edits remain unsaved.`);
            void library.refetch();
        });
    }
    async function open() {
        const version = editVersion.current, lane = collection;
        await storage(async () => {
            const exact = await methodsGet<MethodRecord>(`${lane}/${enc(selected)}${revision ? `/revisions/${enc(revision)}` : ''}`);
            if (version !== editVersion.current) { setNotice('Open completed after an edit; editable draft retained. Open again explicitly.'); return; }
            saveTargets.current[saveTarget] = exact;
            (lane === 'liquid-classes' ? setLiquid : tab === 'Quick run' ? setQuick : setDraft)(clone(exact.method));
            if (lane === 'library') {
                const retained = object(object(exact.method.editor_state).run_inputs);
                setValues({ bindings: clone(object(retained.bindings)), dependencies: clone(object(retained.dependencies)), initialState: Object.hasOwn(object(exact.method.editor_state), 'run_inputs') ? clone(retained.initial_state) : {} });
            }
            if (lane === 'liquid-classes') setSavedLiquid(exact); else if (tab !== 'Quick run') setSavedMethod(exact); editVersion.current++;
            setNotice(`Opened exact revision ${exact.revision}.`);
        });
    }
    const compileInput = (method = activeDraft): MethodValue => ({ method: clone(method), bindings: clone(bindings), dependencies: clone(dependencies), initial_state: clone(initialState) });
    async function compile(path: 'check' | 'compile') { const input = compileInput(); await storage(async () => setCheck({ input, result: await methodsPost<MethodCompile>(path, input) })); }
    async function submit() {
        if (busyRef.current || !connected || !ack || (tab !== 'Quick run' && !savedMethod)) return;
        busyRef.current = true; setBusy(true); setError(null);
        const frozen = compileInput(tab === 'Quick run' ? quick : savedMethod!.method), pinned = clone(savedMethod), currentGeneration = generation, quickRun = tab === 'Quick run';
        let attempt: typeof run = null;
        try {
            const key = crypto.randomUUID(), jobId = await canonicalWorkflowJobId(key);
            if (authority.current.generation !== currentGeneration || !authority.current.connected) throw new Error('Connection changed before submission; nothing submitted.');
            attempt = { jobId, key, generation: currentGeneration, snapshot: frozen }; setRun(attempt); setAccepted(null); setReport(null);
            try { localStorage.setItem('bms.bioxp.method-run.v1', JSON.stringify(attempt)); } catch { setNotice('Browser retention unavailable; preserve the original job identity. Submission behavior is unchanged.'); }
            const body = { ...(quickRun ? { method: frozen.method } : { revision: pinned!.revision }), bindings: frozen.bindings, dependencies: frozen.dependencies, initial_state: frozen.initial_state, ...(object(object(frozen.method).editor_state).recovery_linkage ? { recovery: clone(object(object(frozen.method).editor_state).recovery_linkage) } : {}), idempotency_key: key, expected_generation: currentGeneration, acknowledge_live: true };
            const result = await methodsPost<MethodRun>(quickRun ? 'quick-runs' : `library/${enc(pinned!.id)}/runs`, body);
            if (result.job_id !== jobId) throw new Error('Returned identity differs; reconcile original canonical identity, no retry.');
            if (authority.current.generation === currentGeneration) setAccepted(result);
        } catch (e) {
            if (attempt && definiteMethodRefusal(e)) { const refused = { ...attempt, refused: true }; setRun(refused); try { localStorage.setItem('bms.bioxp.method-run.v1', JSON.stringify(refused)); } catch { /* retention is not permission */ } }
            setError(bioXpErrorText(e));
        } finally { busyRef.current = false; setBusy(false); }
    }
    async function recovery() {
        if (!run) return;
        await storage(async () => {
            const result = await methodsPost<MethodValue>(`runs/${enc(run.jobId)}/recovery-draft`, { occurrence: occurrence ? { ...object(reportProvenance(report).find((p: unknown) => String(object(p).occurrence_id) === occurrence)), ...(nativeAction ? { native_action_id: nativeAction } : {}) } : undefined, ...(recoveryInitialState !== undefined ? { initial_state: clone(recoveryInitialState) } : {}) }, { expected_connection_generation: generation });
            setRecoveryEvidence(result);
            if (!result.method && !result.draft) { setNotice(String(result.message ?? 'Lossless recovery draft unavailable; original run unchanged.')); return; }
            delete saveTargets.current.library; const recovered = clone(object(result.method ?? result.draft)); setDraft({ ...recovered, editor_state: { ...object(recovered.editor_state), recovery_linkage: clone(result.recovery) } }); setMethodValues({ bindings: clone(object(result.bindings)), dependencies: clone(object(result.dependencies)), initialState: clone(result.initial_state) }); setSavedMethod(null); setTab('Methods'); editVersion.current++;
            setNotice('Recovery is an unsaved editable draft. Review intended actions and explicit initial assumptions; no setup or execution was submitted.');
        });
    }
    const runCandidate = tab === 'Quick run' ? quick : savedMethod?.method;
    const runNotice = useQuery({ queryKey: ['bioxp', 'methods', 'run-notice', runCandidate, bindings, dependencies, initialState],
        queryFn: () => methodsPost<MethodCompile>('check', { method: clone(runCandidate), bindings: clone(bindings), dependencies: clone(dependencies), initial_state: clone(initialState) }),
        enabled: visible && !!runCandidate && ['Methods', 'Quick run'].includes(tab), retry: false, staleTime: Infinity });
    const ex = methodRows<MethodValue>(examples.data ?? []);
    return <section aria-label="Methods workspace" className="bioxp-methods">
        <nav role="tablist" aria-label="Workflow tools">{['Methods', 'Liquid classes', 'Runs', 'Quick run'].map((name, index, names) => <button key={name} id={`method-tab-${index}`} type="button" role="tab" tabIndex={tab === name ? 0 : -1} aria-selected={tab === name} aria-controls={name === 'Runs' ? 'method-runs-panel' : 'method-editor-panel'} onKeyDown={e => { const next = e.key === 'ArrowRight' ? (index + 1) % names.length : e.key === 'ArrowLeft' ? (index + names.length - 1) % names.length : e.key === 'Home' ? 0 : e.key === 'End' ? names.length - 1 : null; if (next !== null) { e.preventDefault(); setTab(names[next]); e.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#method-tab-${next}`)?.focus(); } }} onClick={() => setTab(name)}>{name}</button>)}</nav>
        {error && <p role="alert">{error} No automatic execution retry.</p>}{notice && <p role="status">{notice}</p>}
        {(catalog.isError || schema.isError) && <p role="status">Authoring catalog/schema unavailable. Existing raw values are retained; no robot connection is required for these reads.</p>}
        <div role="tabpanel" id="method-editor-panel" aria-labelledby={`method-tab-${['Methods', 'Liquid classes', 'Runs', 'Quick run'].indexOf(tab)}`} hidden={tab === 'Runs'}>
            <div className="flex flex-wrap gap-2"><button type="button" disabled={storageBusy} onClick={() => { delete saveTargets.current[saveTarget]; if (tab !== 'Liquid classes') setValues({ bindings: {}, dependencies: {}, initialState: {} }); setActive(tab === 'Liquid classes' ? { schema: 'bms.bioxp-liquid-class.v1', name: '' } : newMethod()); if (tab === 'Liquid classes') setSavedLiquid(null); else if (tab !== 'Quick run') setSavedMethod(null); }}>New</button>
                <button type="button" disabled={storageBusy} onClick={() => { delete saveTargets.current[saveTarget]; setActive({ ...clone(activeDraft), ...(Array.isArray(activeDraft.steps) ? { steps: (activeDraft.steps as MethodValue[]).map(duplicateNode), ...(Array.isArray(activeDraft.procedures) ? { procedures: (activeDraft.procedures as MethodValue[]).map(p => ({ ...p, ...(Array.isArray(p.steps) ? { steps: (p.steps as MethodValue[]).map(duplicateNode) } : {}) })) } : {}) } : {}) }); if (tab === 'Liquid classes') setSavedLiquid(null); else if (tab !== 'Quick run') setSavedMethod(null); setNotice('Duplicated as an unsaved draft with new step identities.'); }}>Duplicate</button>
                <button type="button" disabled={storageBusy} onClick={() => void save()}>{tab === 'Quick run' ? 'Save as method' : 'Save'}</button>
                <button type="button" onClick={() => download(activeDraft, `${String(activeDraft.name || 'method')}.json`)}>Export draft JSON</button>
                <label>Import JSON<input aria-label="Import JSON" disabled={storageBusy} type="file" accept="application/json,.json" onChange={e => { const file = e.target.files?.[0]; if (file) void storage(async () => { const value = JSON.parse(await file.text()); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Import must contain an object.'); delete saveTargets.current[saveTarget]; setActive(object(value.method ?? value)); if (value.dependencies && typeof value.dependencies === 'object') setDependencies(clone(value.dependencies)); if (collection === 'liquid-classes') setSavedLiquid(null); else if (tab !== 'Quick run') setSavedMethod(null); setNotice('Imported into a new unsaved draft; existing identities were not overwritten.'); }); }} /></label>
            </div>
            {tab === 'Quick run' && <button type="button" disabled={storageBusy} onClick={() => { delete saveTargets.current.quick; setActive(newMethod()); setQuickValues({ bindings: {}, dependencies: {}, initialState: {} }); }}>Clear next draft</button>}
            <fieldset><legend>Library and exact revisions</legend><label>Search<input aria-label="Library search" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label>
                <select aria-label="Library entry" value={selected} onChange={e => setSelected(e.target.value)}><option value="">Select…</option>{methodRows<MethodRecord>(library.data).map(row => <option key={row.id} value={row.id}>{row.name} · r{row.revision}</option>)}</select>
                <select aria-label="Revision" value={revision} onChange={e => setRevision(e.target.value)}><option value="">Latest exact read</option>{methodRows<MethodRecord>(revisions.data).map(row => <option key={row.revision} value={row.revision}>{row.revision}</option>)}</select>
                <button type="button" disabled={!selected || storageBusy} onClick={() => void open()}>Open</button>
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous library page</button><button type="button" disabled={methodRows(library.data).length < 25} onClick={() => setOffset(offset + 25)}>Next library page</button>
                <label>Compare from revision<input aria-label="Compare from revision" value={fromRevision} onChange={e => setFromRevision(e.target.value)} /></label><button type="button" disabled={!selected || !revision || !fromRevision || storageBusy} onClick={() => void storage(async () => setDiff(await methodsGet(`${collection}/${enc(selected)}/diff`, { from_revision: fromRevision, to_revision: revision })))}>Revision diff</button>
                <button type="button" disabled={!selected || storageBusy} onClick={() => void storage(async () => download(await methodsGet(`${collection}/${enc(selected)}/export`, revision ? { revision } : undefined), 'method-export.json'))}>Export saved revision</button>
                <Evidence title="Raw revision diff (not resolved values)" value={diff} />
            </fieldset>
            {String(activeDraft.schema).startsWith('bms.bioxp-workflow-draft.') && <button type="button" disabled={storageBusy} onClick={() => void storage(async () => { const migrated = await methodsPost<MethodValue>('migrate', { method: clone(activeDraft) }); setActive(object(migrated.method ?? migrated)); setNotice('Migrated into an editable method; original legacy content is retained by the migration contract. Save explicitly to create a revision.'); })}>Migrate legacy draft</button>}
            <label>Name<input aria-label="Method name" value={String(activeDraft.name ?? '')} onChange={e => setActive({ ...activeDraft, name: e.target.value })} /></label>
            {tab === 'Liquid classes' ? <><label>Source starter variant<select aria-label="Source starter variant" value="" onChange={e => { const entry = methodRows<MethodValue>(starters.data)[Number(e.target.value)]; if (entry) { delete saveTargets.current['liquid-classes']; setActive({ ...clone(entry), name: String(entry.name ?? entry.id) }); setSavedLiquid(null); } }}><option value="">Choose an exact source context…</option>{methodRows<MethodValue>(starters.data).map((entry, i) => <option key={String(entry.id)} value={i}>{String(entry.id)} · revision {String(entry.revision)} · {JSON.stringify(entry.context)}</option>)}</select></label><button type="button" disabled={storageBusy} onClick={() => void storage(async () => download(await methodsGet('liquid-classes/source'), 'Cavro-source-catalog.json'))}>Export original source catalog</button><Evidence title="Selected class context and source provenance" value={{ context: liquid.context, source: liquid.source }} /><p>Requested, resolved, emitted and reported-applied are separate values. Blank source values are not zero. T10 design intent does not establish adapter or consumable support.</p><MethodFields label="Liquid class" schema={schema.data?.liquid_class_schema ? object(schema.data.liquid_class_schema) as Schema : liquidClassEditorSchema} value={liquid} onChange={v => setActive(object(v))} />
                <button type="button" onClick={() => { delete saveTargets.current['liquid-classes']; setLiquid(clone(liquid)); setSavedLiquid(null); setNotice('Selected class content pinned into a new draft; original revision is unchanged. Applied status is not inferred.'); }}>Pin selected current entry as new class</button>
            </> : <>
                <label>Scientific example<select aria-label="Scientific example" disabled={storageBusy} value="" onChange={e => { const sample = ex[Number(e.target.value)]; if (sample) { delete saveTargets.current[saveTarget]; setActive(clone(object(sample.method ?? sample))); setValues({ bindings: clone(object(sample.bindings)), dependencies: clone(object(sample.dependencies)), initialState: clone(object(sample.initial_state)) }); if (tab !== 'Quick run') setSavedMethod(null); } }}><option value="">Choose a skeleton…</option>{ex.map((sample, i) => <option key={i} value={i}>{String(sample.name ?? object(sample.method).name ?? i)}</option>)}</select></label>
                <label>Bound software fixture<select aria-label="Bound software fixture" disabled={storageBusy} value="" onChange={e => { const fixture = object(ex[Number(e.target.value)]?.bound_fixture); if (fixture.method) { delete saveTargets.current[saveTarget]; setActive(clone(object(fixture.method))); setValues({ bindings: clone(object(fixture.bindings)), dependencies: clone(object(fixture.dependencies)), initialState: clone(object(fixture.initial_state)) }); if (tab !== 'Quick run') setSavedMethod(null); setNotice(String(fixture.fixture_provenance ?? 'Software fixture only; not scientific or physical qualification.')); } }}><option value="">Choose explicit test inputs…</option>{ex.map((sample, i) => sample.bound_fixture ? <option key={i} value={i}>{String(sample.name ?? object(sample.method).name ?? i)} · software fixture only</option> : null)}</select></label>
                <MethodOutline collapsed={object(object(activeDraft.editor_state).collapsed_steps) as Record<string, boolean>} onCollapse={(id, collapsed) => setActive({ ...activeDraft, editor_state: { ...object(activeDraft.editor_state), collapsed_steps: { ...object(object(activeDraft.editor_state).collapsed_steps), [id]: collapsed } } })} findings={check?.result.issues} nodes={Array.isArray(activeDraft.steps) ? activeDraft.steps as MethodValue[] : []} onChange={steps => setActive({ ...activeDraft, steps })} procedures={Array.isArray(activeDraft.procedures) ? activeDraft.procedures as MethodValue[] : []} catalog={catalog.data ?? {}} nodeSchema={properties.steps?.items} rootSchema={rootSchema} flat={tab === 'Quick run'} />
                <details><summary>Parameters, expressions and bindings</summary><MethodFields label="Parameters" schema={properties.parameters ?? methodParameterSchema} rootSchema={rootSchema} value={activeDraft.parameters ?? []} onChange={v => setActive({ ...activeDraft, parameters: v })} /><MethodFields label="Bindings" schema={methodBindingsSchema(activeDraft, catalog.data ?? {})} value={bindings} onChange={v => setBindings(object(v))} />
                    <select aria-label="Parameter preset" value="" onChange={e => void storage(async () => { const preset = await methodsGet<MethodRecord>(`presets/${enc(e.target.value)}`); setBindings(clone(object(preset.method.bindings))); setDependencies(clone(object(preset.method.dependencies))); })}><option value="">Load preset…</option>{methodRows<MethodRecord>(presets.data).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
                    <button type="button" disabled={storageBusy} onClick={() => void storage(async () => { const preset = await methodsSave('presets', { schema: 'bms.bioxp-method-preset.v1', name: String(activeDraft.name), bindings: clone(bindings), dependencies: clone(dependencies) }, null); await methodsGet(`presets/${enc(preset.id)}/revisions/${preset.revision}`); await presets.refetch(); setNotice('Parameter preset saved.'); })}>Save parameter preset</button>
                </details>
                <details><summary>Procedures and pinned dependencies</summary><BioXpMethodProcedures catalog={catalog.data ?? {}} rootSchema={rootSchema} value={activeDraft.procedures} onChange={v => setActive({ ...activeDraft, procedures: v })} /><MethodFields label="Dependencies" value={dependencies} onChange={v => setDependencies(object(v))} /></details>
                <details><summary>Deck, materials, tip policy and initial assumptions</summary><BioXpWorkflowDeck selection={deckSelection} onChange={selection => { setDeckSelection(selection); if (tab === 'Quick run' && selection.wells.length === 1) { const station = deckStations.find(s => s.id === selection.station); if (station?.locationId != null) setActive({ ...activeDraft, steps: [...(Array.isArray(activeDraft.steps) ? activeDraft.steps : []), { step_id: crypto.randomUUID(), type: 'action', action: 'move', inputs: { location_id: station.locationId, well: selection.wells[0] } }] }); } }} />
                    {tab === 'Quick run' && <p>Click one reference well to append Move to the next draft, never execute it. Author its applicable height in Properties. Named Park and non-well movements remain in the action palette; multiple-well planning is not an independent-head motion command.</p>}
                    <BioXpWorkflowMaterials methodAuthoring plan={{ ...object(activeDraft.deck_plan), labware: Array.isArray(object(activeDraft.deck_plan).labware) ? object(activeDraft.deck_plan).labware : [], materials: Array.isArray(object(activeDraft.deck_plan).materials) ? object(activeDraft.deck_plan).materials : [], assignments: Array.isArray(object(activeDraft.deck_plan).assignments) ? object(activeDraft.deck_plan).assignments : [] } as WorkflowDeckPlan} selection={deckSelection} onChange={v => setActive({ ...activeDraft, deck_plan: v })} />
                    <MethodFields label="Deck plan" schema={properties.deck_plan} rootSchema={rootSchema} value={activeDraft.deck_plan ?? {}} onChange={v => setActive({ ...activeDraft, deck_plan: v })} /><MethodFields label="Tip policy" schema={properties.tip_policy ?? methodTipPolicySchema} rootSchema={rootSchema} value={activeDraft.tip_policy} onChange={v => setActive({ ...activeDraft, tip_policy: v })} /><MethodFields label="Initial assumptions" value={initialState} onChange={setInitialState} />
                    <p>Planned contents, tips and custody are not observed robot state. Fixed four-channel head references remain native; map selection never moves hardware.</p>
                </details>
                <details><summary>All method fields and advanced controls</summary><MethodFields label="Method" schema={rootSchema} value={activeDraft} onChange={v => setActive(object(v))} /><Evidence title="Action capabilities and source evidence" value={catalog.data} /></details>
                <button type="button" disabled={storageBusy} onClick={() => void compile('check')}>Check</button><button type="button" disabled={storageBusy} onClick={() => void compile('compile')}>Compile</button>
                {check && <section aria-label="Method findings"><h3>Findings and effective settings</h3>{!recordEqual(check.input, compileInput()) && <p>Findings belong to an earlier immutable draft snapshot.</p>}{check.result.issues.map((issue, i) => <p key={i} role="status">{issue.path ?? issue.step_id}: {issue.message} ({issue.category ?? issue.code})</p>)}
                    <p>{check.result.document ? 'Representable native document.' : 'No native document; representation findings do not discard the draft.'} Advisory simulation is not a hardware admission rule.</p>
                    <Evidence title="Water substitutions — unspecified → resolved, revision and application status" value={check.result.water_substitutions} /><Evidence title="Requested / resolved / emitted / reported-applied" value={check.result.resolved} /><Evidence title="Tips, time, volumes and unknowns" value={check.result.simulation} /><Evidence title="Occurrence and generated action provenance" value={check.result.provenance} /><BioXpMethodApplicationFields value={check.result.resolved} /><BioXpMethodPreview result={check.result} initialState={check.input.initial_state} />
                </section>}
                <p>{tab === 'Quick run' ? 'Run freezes the current unsaved draft. Later editing or Clear changes only the next draft.' : savedMethod ? `Run uses saved revision ${savedMethod.revision}, not uncommitted edits.` : 'Save or open an exact revision before saved Run.'}</p>
                <p>Unspecified applicable liquid fields inherit pinned matching Water fields. This is recorded in effective settings, with no additional confirmation.</p>
                {runNotice.isPending && runCandidate && <p>Effective-settings notice pending; this is not an execution prerequisite.</p>}
                {runNotice.isError && <p>Effective-settings notice unavailable; original values retained. No additional acknowledgement or gate.</p>}
                {runNotice.data && <Evidence title="Before Run: matching Water substitutions and application status" value={runNotice.data.water_substitutions} />}
                <label><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />Acknowledge live robot execution</label><button type="button" disabled={busy || !connected || !ack || tab !== 'Quick run' && !savedMethod} onClick={() => void submit()}>{tab === 'Quick run' ? 'Run immutable quick snapshot' : 'Run saved revision'}</button>
            </>}
        </div>
        <div role="tabpanel" id="method-runs-panel" aria-labelledby="method-tab-2" hidden={tab !== 'Runs'}><h3>Runs</h3><label>Search recent native jobs<input aria-label="Run search" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous run page</button><button type="button" disabled={offset >= 75 || methodRows(runs.data).length < 25} onClick={() => setOffset(Math.min(75, offset + 25))}>Next run page</button><p>Bounded latest native jobs window, not complete historical pagination.</p><button type="button" onClick={() => void runs.refetch()}>Refresh runs</button>{methodRows<MethodRun>(runs.data).map(job => <button type="button" key={job.job_id} onClick={() => { setRun({ jobId: job.job_id, key: '', generation, snapshot: object(job.method_snapshot) }); setAccepted(job); setReport(null); }}>{job.job_id} · {job.status}</button>)}</div>
        {run && <section aria-label="Immutable method run"><h3>Original run: {run.jobId}</h3><p>Original key {run.key} · generation {run.generation}. Edits never modify this snapshot.</p><Evidence title="Frozen run input" value={run.snapshot} />
            {run.refused ? <p>Definite pre-admission refusal; nothing started for this submission. No nonexistent-job polling.</p> : <BioXpWorkflowJobMonitor onObserved={setLiveObservation} methodsFacade visible={visible && (tab === 'Runs' || tab === 'Quick run' || tab === 'Methods')} jobId={run.jobId} generation={generation} connected={connected} controlsEnabled={controlsEnabled} busyRef={busyRef} onBusyChange={setBusy} submitting={busy} pending acceptedJob={accepted} />}
            {object(liveObservation).job_id === run.jobId && <BioXpMethodProgress report={object(object(object(liveObservation).execution).runtime_state)} />}
            <button type="button" disabled={storageBusy} onClick={() => void storage(async () => { const original = await methodsPost<MethodValue>(`runs/${enc(run.jobId)}/clone`, {}, { expected_connection_generation: generation }); delete saveTargets.current.library; setDraft(clone(object(original.method))); setMethodValues({ bindings: clone(object(original.bindings)), dependencies: clone(object(original.dependencies)), initialState: clone(original.initial_state) }); setSavedMethod(null); setTab('Methods'); editVersion.current++; setNotice('Original immutable method cloned as an unsaved draft. No execution submitted.'); })}>Clone original method</button>
            <button type="button" disabled={storageBusy} onClick={() => void storage(async () => setReport(await methodsGet<MethodValue>(`runs/${enc(run.jobId)}/report`, { expected_connection_generation: generation })))}>Load method report</button>
            {report && <><BioXpMethodProgress report={report} /><BioXpMethodApplicationFields value={report} /><Evidence title="Method report: original settings, applied fields, outcomes and recovery linkage" value={report} /><button type="button" onClick={() => download(report, `${run.jobId}-report.json`)}>Export report</button>
                <label>Exact recovery occurrence<select aria-label="Exact recovery occurrence" value={occurrence} onChange={e => { setOccurrence(e.target.value); setNativeAction(''); }}><option value="">Select original occurrence…</option>{reportProvenance(report).map((p: unknown) => <option key={String(object(p).occurrence_id)} value={String(object(p).occurrence_id)}>{String(object(p).occurrence_id)} · {JSON.stringify(object(p).call_path ?? object(p).path)} · native {String(object(p).action_id ?? '')}</option>)}</select></label>
                <label>Exact native action<select aria-label="Recovery native action" value={nativeAction} onChange={e => setNativeAction(e.target.value)}><option value="">Whole authored occurrence (review partial children)</option>{reportProvenance(report).filter(p => p.occurrence_id === occurrence).flatMap(p => Array.isArray(p.native_action_ids) ? p.native_action_ids : []).map(action => <option key={String(action)} value={String(action)}>{String(action)}</option>)}</select></label>
                <p>Unspecified recovery assumptions retain the original snapshot. Select null or edit explicitly to override; current next-draft assumptions are separate.</p><MethodFields label="Recovery initial assumptions" value={recoveryInitialState} onChange={setRecoveryInitialState} /><Evidence title="Recovery linkage and original intentions" value={recoveryEvidence} /><button type="button" disabled={storageBusy || !occurrence} onClick={() => void recovery()}>Create recovery draft</button><p>New draft only, never Continue-as-retry. Review partial effects, generated setup and excluded actions explicitly; no automatic lift, load or aspiration replay.</p></>}
        </section>}
    </section>;
}
