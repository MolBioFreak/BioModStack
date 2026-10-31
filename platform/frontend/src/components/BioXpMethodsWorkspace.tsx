import { useEffect, useRef, useState } from 'react';
import { useBioXpWorkflowMutationOwner } from './BioXpWorkflowMutationOwner';
import { useQuery } from '@tanstack/react-query';
import './BioXpMethodsWorkspace.css';
import { liquidClassEditorSchema, methodParameterSchema, methodTipPolicySchema } from '../lib/bioxpMethodEditorSchema';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { bioXpErrorText } from '../lib/bioxpClient';
import { methodsGet, methodsPost, methodsSave, methodRows, newMethod, definiteMethodRefusal, type MethodValue, type MethodRecord, type MethodCollection, type MethodCatalog, type MethodCompile, type MethodRun } from '../lib/bioxpMethods';
import { canonicalWorkflowJobId } from '../lib/bioxpSavedWorkflowRun';
import { BioXpCopyId } from './BioXpCopyId';
import { BioXpMethodPlanSummary, methodStepFindings, occurrenceSteps } from './BioXpMethodPlanSummary';
import { MethodFields, object, duplicateNode, methodBindingsSchema } from './BioXpMethodFields';
import { BioXpWorkflowJobMonitor } from './BioXpWorkflowJobMonitor';
import { BioXpMethodPreview, BioXpMethodProgress, BioXpMethodApplicationFields } from './BioXpMethodPreview';
import { BioXpMethodProcedures } from './BioXpMethodProcedures';
import { BioXpMethodDeckWorkbench } from './BioXpMethodDeckWorkbench';
import { BioXpWorkflowProfilesContext, publishedTipProfiles, pinLabwareProfile } from './BioXpWorkflowProfiles';
import { RepairWorkspaceContext, RepairWorkspaceClassEditor } from './repairWorkspaceClasses';
import { methodInitialState } from '../lib/bioxpMethodSimulation';
import { workspaceSnapshot, workspaceHydrate, workspaceRecoverySnapshot } from '../lib/repairWorkspaceSnapshot';
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
type RunEntry = { jobId: string; key: string; generation: number; snapshot: MethodValue; refused?: boolean; name: string; startedAt: string; saved?: { id: string; revision: number } | null };
const RUNS_KEY = 'bms.bioxp.method-runs.v2', MAX_RUNS = 25;
const validRun = (value: unknown): value is RunEntry => { const v = object(value); return typeof v.jobId === 'string' && /^protocol-live-[a-f0-9]{64}$/.test(v.jobId) && typeof v.key === 'string' && typeof v.generation === 'number'; };
function readRuns(): RunEntry[] {
    try {
        const list = JSON.parse(localStorage.getItem(RUNS_KEY) ?? 'null');
        if (Array.isArray(list)) return list.filter(validRun).slice(0, MAX_RUNS);
        const legacy = JSON.parse(localStorage.getItem('bms.bioxp.method-run.v1') ?? 'null');
        return validRun(legacy) ? [{ ...legacy, name: String(object(object(legacy.snapshot).method).name || 'Method run'), startedAt: legacy.startedAt ?? '' }] : [];
    } catch { return []; }
}
/** Bounded browser list of this browser's runs; robot history stays authoritative. */
function writeRuns(runs: RunEntry[]): string | null {
    for (let kept = runs.slice(0, MAX_RUNS); kept.length; kept = kept.slice(0, -1)) {
        try { localStorage.setItem(RUNS_KEY, JSON.stringify(kept)); return kept.length < runs.length ? 'Older runs were dropped from this browser list; robot history still has them.' : null; } catch { /* try fewer */ }
    }
    return 'Browser run list unavailable; keep the job ID. Submission behavior is unchanged.';
}
const when = (iso: unknown) => { const d = typeof iso === 'string' && iso ? new Date(iso) : null; return d && !Number.isNaN(d.getTime()) ? d.toLocaleString() : 'start time not recorded'; };
const plainContext = (context: unknown) => Object.entries(object(context)).filter(([, v]) => v !== null && v !== undefined && typeof v !== 'object').map(([k, v]) => `${k.replaceAll('_', ' ')} ${String(v)}`).join(', ');
export function BioXpMethodsWorkspace({ generation, connected, controlsEnabled, visible = true }: { generation: number; connected: boolean; controlsEnabled: boolean; visible?: boolean }) {
    const [tab, updateTab] = useState('Methods');
    const tabRef = useRef(tab); tabRef.current = tab;
    const setTab = (next: string) => { if (next !== tabRef.current) editVersion.current++; tabRef.current = next; updateTab(next); };
    const [authoringEpoch, setAuthoringEpoch] = useState(0);
    const [workflowKind, setWorkflowKind] = useState('');
    const [separation, setSeparation] = useState('');
    const [advancedMethodVisited, setAdvancedMethodVisited] = useState(false);
    const [draft, setDraft] = useState<MethodValue>(newMethod);
    const [liquid, setLiquid] = useState<MethodValue>({ schema: 'bms.bioxp-liquid-class.v1', name: '' });
    const [savedMethod, setSavedMethod] = useState<MethodRecord | null>(null);
    const [savedLiquid, setSavedLiquid] = useState<MethodRecord | null>(null);
    const saveTargets = useRef<Partial<Record<MethodCollection, MethodRecord>>>({});
    const examplesRef = useRef<HTMLDetailsElement>(null);
    const [methodValues, setMethodValues] = useState({ bindings: {} as MethodValue, dependencies: {} as MethodValue, initialState: {} as unknown });
    const { bindings, dependencies, initialState } = methodValues;
    const setValues = setMethodValues;
    const setBindings = (bindings: MethodValue) => { editVersion.current++; setValues(previous => ({ ...previous, bindings })); };
    const setDependencies = (dependencies: MethodValue) => { editVersion.current++; setValues(previous => ({ ...previous, dependencies })); };
    const setInitialState = (initialState: unknown) => { editVersion.current++; setValues(previous => ({ ...previous, initialState })); };
    const [check, setCheck] = useState<{ result: MethodCompile; input: MethodValue } | null>(null);
    const [checkError, setCheckError] = useState<string | null>(null);
    const [previewOpen, setPreviewOpen] = useState(false);
    const checkSeq = useRef(0);
    const { busy, setBusy, busyRef } = useBioXpWorkflowMutationOwner();
    const [storageBusy, setStorageBusy] = useState(false), storageLock = useRef(false);
    const [error, setError] = useState<string | null>(null);
    const [notice, updateNotice] = useState<{ tab: string; text: string } | null>(null);
    const setNotice = (text: string, owner = tab) => updateNotice({ tab: owner, text });
    const [runMessage, setRunMessage] = useState<string | null>(null);
    const [search, setSearch] = useState(''), [offset, setOffset] = useState(0), [selected, setSelected] = useState('');
    const [revision, setRevision] = useState(''), [fromRevision, setFromRevision] = useState('');
    const [diff, setDiff] = useState<unknown>();
    const [ack, setAck] = useState(false);
    const [history, setHistory] = useState<RunEntry[]>(readRuns);
    const [run, setRun] = useState<RunEntry | null>(() => history[0] ?? null);
    const historyRef = useRef(history); historyRef.current = history;
    // Synchronous: the original identity is in browser storage before the POST starts.
    const remember = (entry: RunEntry) => { const next = [entry, ...historyRef.current.filter(r => r.jobId !== entry.jobId)].slice(0, MAX_RUNS); historyRef.current = next; const warning = writeRuns(next); setRunMessage(warning); setHistory(next); };
    const [accepted, setAccepted] = useState<MethodRun | null>(null), [report, setReport] = useState<MethodValue | null>(null);
    const reportCache = useRef<Record<string, MethodValue>>({});
    const reportGeneration = useRef<number | null>(null);
    const reportIdentity = useRef({ jobId: run?.jobId, generation }); reportIdentity.current = { jobId: run?.jobId, generation };
    const visibleReport = report && report.job_id === run?.jobId && reportGeneration.current === generation ? report : null;
    const [liveObservation, setLiveObservation] = useState<unknown>(null);
    const [recoveryInitialState, setRecoveryInitialState] = useState<unknown>(undefined);
    const [recoveryEvidence, setRecoveryEvidence] = useState<MethodValue | null>(null);
    useEffect(() => { setRecoveryInitialState(undefined); setRecoveryEvidence(null); setOccurrence(''); setNativeAction(''); }, [run?.jobId]);
    const [occurrence, setOccurrence] = useState('');
    const [nativeAction, setNativeAction] = useState('');
    const authority = useRef({ generation, connected }); authority.current = { generation, connected };
    const editVersion = useRef(0);
    const collection: MethodCollection = tab === 'Liquid classes' ? 'liquid-classes' : 'library';
    const saveTarget = collection;
    const activeDraft = tab === 'Liquid classes' ? liquid : draft;
    const saved = collection === 'liquid-classes' ? savedLiquid : savedMethod;
    const setActive = (v: MethodValue) => { editVersion.current++; (tab === 'Liquid classes' ? setLiquid : setDraft)(v); };
    const catalog = useQuery({ queryKey: ['bioxp', 'methods', 'catalog'], queryFn: () => methodsGet<MethodCatalog>('catalog'), retry: false, staleTime: Infinity });
    const schema = useQuery({ queryKey: ['bioxp', 'methods', 'schema'], queryFn: () => methodsGet<MethodValue>('schema'), retry: false, staleTime: Infinity });
    const plates = useQuery({ queryKey: ['bioxp', 'methods', 'plates'], queryFn: () => methodsGet<MethodValue>('plates'), retry: false, staleTime: Infinity });
    const examples = useQuery({ queryKey: ['bioxp', 'methods', 'examples'], queryFn: () => methodsGet<unknown>('examples'), retry: false, staleTime: Infinity });
    const library = useQuery({ queryKey: ['bioxp', 'methods', collection, search, offset], queryFn: () => methodsGet<unknown>(collection, { search, offset, limit: 25 }), retry: false, enabled: visible && tab !== 'Runs' });
    const revisions = useQuery({ queryKey: ['bioxp', 'methods', collection, selected, 'revisions'], queryFn: () => methodsGet<unknown>(`${collection}/${enc(selected)}/revisions`), retry: false, enabled: !!selected });
    const runs = useQuery({ queryKey: ['bioxp', 'methods', 'runs', generation, search, offset], queryFn: () => methodsGet<unknown>('runs', { expected_connection_generation: generation, search, offset, limit: 25 }), retry: false, enabled: visible && tab === 'Runs' && connected });
    const starters = useQuery({ queryKey: ['bioxp', 'methods', 'liquid-starters'], queryFn: () => methodsGet<unknown>('liquid-classes/starters'), enabled: visible && tab !== 'Runs', retry: false, staleTime: Infinity });
    const presets = useQuery({ queryKey: ['bioxp', 'methods', 'presets'], queryFn: () => methodsGet<unknown>('presets', { limit: 100 }), retry: false });
    const rootSchema = schemas(schema.data ?? {}) as Schema;
    const properties = rootSchema.properties ?? {};
    useEffect(() => { setSelected(''); setRevision(''); setFromRevision(''); setOffset(0); }, [collection]);
    async function storage(action: () => Promise<void>) {
        if (storageLock.current) return; storageLock.current = true; setStorageBusy(true); setError(null);
        try { await action(); } catch (e) { setError(bioXpErrorText(e)); } finally { storageLock.current = false; setStorageBusy(false); }
    }
    async function save() {
        const value = collection === 'library' ? workspaceSnapshot(activeDraft, { bindings, dependencies, initialState }) : clone(activeDraft), version = editVersion.current, lane = collection;
        await storage(async () => {
            const receipt = await methodsSave(lane, value, saveTargets.current[saveTarget] ?? saved);
            // Keep accepted identity before GET: failed readback retry updates, not a duplicate create.
            saveTargets.current[saveTarget] = receipt;
            const exact = await methodsGet<MethodRecord>(`${lane}/${enc(receipt.id)}/revisions/${receipt.revision}`);
            if (!recordEqual(exact.method, value)) throw new Error('Saved revision readback differs; original editable draft retained.');
            if (lane === 'liquid-classes') setSavedLiquid(exact);
            else setSavedMethod(exact);
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
            (lane === 'liquid-classes' ? setLiquid : setDraft)(clone(exact.method));
            if (lane === 'library') {
                setValues(workspaceHydrate(exact.method).inputs);
            }
            if (lane === 'liquid-classes') setSavedLiquid(exact); else setSavedMethod(exact); editVersion.current++;
            setNotice(`Opened exact revision ${exact.revision}.`);
        });
    }
    const compileInput = (method = activeDraft): MethodValue => ({ method: clone(method), bindings: clone(bindings), dependencies: clone(dependencies), initial_state: clone(initialState) });
    // One pure check ~600 ms after the last edit; it never gates Save or Run.
    const hasContent = (Array.isArray(draft.steps) && draft.steps.length > 0) || draft.deck_plan !== undefined;
    const checkKey = tab === 'Methods' && visible && hasContent ? JSON.stringify(compileInput()) : '';
    useEffect(() => {
        if (!checkKey) return;
        const timer = setTimeout(() => {
            const seq = ++checkSeq.current, input = JSON.parse(checkKey) as MethodValue;
            methodsPost<MethodCompile>('check', input).then(result => { if (seq === checkSeq.current) { setCheck({ input, result }); setCheckError(null); } },
                e => { if (seq === checkSeq.current) setCheckError(bioXpErrorText(e)); });
        }, 600);
        return () => clearTimeout(timer);
    }, [checkKey]);
    const currentInput = compileInput();
    const freshCheck = check && recordEqual(check.input, currentInput) ? check : null;
    const unchangedSinceSave = !!savedMethod && recordEqual(savedMethod.method, workspaceSnapshot(draft, { bindings, dependencies, initialState }));
    async function submit() {
        if (busyRef.current || !connected || !ack) return;
        busyRef.current = true; setBusy(true); setError(null);
        // Freeze the exact draft before any await. A saved, unchanged draft runs its
        // revision; anything else runs the draft body. Both keep an exact copy on the run.
        const frozen = compileInput(draft), pinned = unchangedSinceSave ? clone(savedMethod) : null, basis = savedMethod ? { id: savedMethod.id, revision: savedMethod.revision, edited: !unchangedSinceSave } : null, currentGeneration = generation;
        let attempt: RunEntry | null = null;
        try {
            const key = crypto.randomUUID(), jobId = await canonicalWorkflowJobId(key);
            if (authority.current.generation !== currentGeneration || !authority.current.connected) throw new Error('Connection changed before submission; nothing submitted.');
            attempt = { jobId, key, generation: currentGeneration, snapshot: frozen, name: String(object(frozen.method).name || 'Untitled method'), startedAt: new Date().toISOString(), saved: pinned ? { id: pinned.id, revision: pinned.revision } : null };
            setRun(attempt); remember(attempt); setAccepted(null); setReport(null);
            const linkage = object(object(frozen.method).editor_state).recovery_linkage;
            const body = { ...(pinned ? { revision: pinned.revision } : { method: frozen.method, ...(basis ? { based_on: basis } : {}) }), bindings: frozen.bindings, dependencies: frozen.dependencies, initial_state: frozen.initial_state, ...(linkage ? { recovery: clone(linkage) } : {}), idempotency_key: key, expected_generation: currentGeneration, acknowledge_live: true };
            const result = await methodsPost<MethodRun>(pinned ? `library/${enc(pinned.id)}/runs` : 'quick-runs', body);
            if (result.job_id !== jobId) throw new Error('Returned identity differs; reconcile original canonical identity, no retry.');
            if (authority.current.generation === currentGeneration) setAccepted(result);
        } catch (e) {
            if (attempt && definiteMethodRefusal(e)) { const refused = { ...attempt, refused: true }; setRun(refused); remember(refused); }
            setError(bioXpErrorText(e));
        } finally { busyRef.current = false; setBusy(false); }
    }
    async function recovery() {
        if (!run) return;
        await storage(async () => {
            const result = await methodsPost<MethodValue>(`runs/${enc(run.jobId)}/recovery-draft`, { occurrence: occurrence ? { ...object(reportProvenance(report).find((p: unknown) => String(object(p).occurrence_id) === occurrence)), ...(nativeAction ? { native_action_id: nativeAction } : {}) } : undefined, ...(recoveryInitialState !== undefined ? { initial_state: clone(recoveryInitialState) } : {}) }, { expected_connection_generation: generation });
            setRecoveryEvidence(result);
            if (!result.method && !result.draft) { setNotice(String(result.message ?? 'Lossless recovery draft unavailable; original run unchanged.')); return; }
            delete saveTargets.current.library;
            const recovered = workspaceRecoverySnapshot(result);
            setDraft(recovered); setMethodValues(workspaceHydrate(recovered).inputs);
            setSavedMethod(null); setTab('Methods'); editVersion.current++;
            setNotice('Recovery is an unsaved editable draft. Review intended actions and explicit initial assumptions; no setup or execution was submitted.', 'Methods');
        });
    }
    const ex = methodRows<MethodValue>(examples.data ?? []);
    const starter = ex.find(sample => String(sample.id) === workflowKind);
    const variants = Array.isArray(starter?.authoring_variants) ? starter.authoring_variants as MethodValue[] : [];
    const createExperiment = (sample: MethodValue) => {
        delete saveTargets.current[saveTarget]; setAuthoringEpoch(epoch => epoch + 1);
        const method = clone(object(sample.method));
        const pcr = method.name === 'PCR';
        setActive(pcr ? { ...method, editor_state: { ...object(method.editor_state), selected_step_id: 'assemble' }, parameters: (method.parameters as MethodValue[]).map(p => p.id === 'cycle_inputs' ? { ...p, label: 'Repeated PCR cycles' } : p), steps: (method.steps as MethodValue[]).map(n => n.step_id === 'cycle' ? { ...n, label: 'Repeated PCR cycles' } : n) } : method);
        setValues({ bindings: clone(object(sample.bindings)), dependencies: clone(object(sample.dependencies)), initialState: Object.hasOwn(sample, 'initial_state') ? clone(sample.initial_state) : {} });
        setSavedMethod(null);
        setCheck(null);
        setNotice('Editable scaffold, not a validated recipe. Set up named labware, then author amounts, timing, placement and reaction settings. No robot action was sent.');
    };
    return <section aria-label="Methods workspace" className="bioxp-methods">
        <nav role="tablist" aria-label="Workflow tools">{['Methods', 'Liquid classes', 'Runs'].map((name, index, names) => <button key={name} id={`method-tab-${index}`} type="button" role="tab" tabIndex={tab === name ? 0 : -1} aria-selected={tab === name} aria-controls={name === 'Runs' ? 'method-runs-panel' : 'method-editor-panel'} onKeyDown={e => { const next = e.key === 'ArrowRight' ? (index + 1) % names.length : e.key === 'ArrowLeft' ? (index + names.length - 1) % names.length : e.key === 'Home' ? 0 : e.key === 'End' ? names.length - 1 : null; if (next !== null) { e.preventDefault(); setTab(names[next]); e.currentTarget.parentElement?.querySelector<HTMLButtonElement>(`#method-tab-${next}`)?.focus(); } }} onClick={() => setTab(name)}>{name}</button>)}</nav>
        {error && <p role="alert">{error} No automatic execution retry.</p>}{notice?.tab === tab && <p role="status">{notice.text}</p>}
        {(catalog.isError || schema.isError) && <p role="status">Authoring catalog/schema unavailable. Existing raw values are retained; no robot connection is required for these reads.</p>}
        <div role="tabpanel" id="method-editor-panel" aria-labelledby={`method-tab-${['Methods', 'Liquid classes', 'Runs'].indexOf(tab)}`} hidden={tab === 'Runs'}>
            <div className="bioxp-method-titlebar"><label className="bioxp-method-name">Method<input aria-label="Method name" placeholder="Name your method" value={String(activeDraft.name ?? '')} onChange={e => setActive({ ...activeDraft, name: e.target.value })} /></label><div className="bioxp-method-file-actions"><button type="button" disabled={storageBusy} onClick={() => { delete saveTargets.current[saveTarget]; if (tab !== 'Liquid classes') setValues({ bindings: {}, dependencies: {}, initialState: {} }); setActive(tab === 'Liquid classes' ? { schema: 'bms.bioxp-liquid-class.v1', name: '' } : newMethod()); if (tab === 'Liquid classes') setSavedLiquid(null); else setSavedMethod(null); }}>New</button>
                {tab !== 'Liquid classes' && <button type="button" disabled={storageBusy || !ex.some(sample => object(sample.method).name === 'PCR')} onClick={() => { const sample = ex.find(sample => object(sample.method).name === 'PCR'); if (sample) createExperiment(sample); }}>New PCR</button>}
                <button type="button" disabled={storageBusy} onClick={() => void save()}>Save</button>
                <select aria-label="Library entry" value={selected} onChange={e => setSelected(e.target.value)}><option value="">Open a saved method…</option>{methodRows<MethodRecord>(library.data).map(row => <option key={row.id} value={row.id}>{row.name} · r{row.revision}</option>)}</select>
                <button type="button" disabled={!selected || storageBusy} onClick={() => void open()}>Open</button>
                {tab !== 'Liquid classes' && <button type="button" aria-expanded={previewOpen} onClick={() => setPreviewOpen(!previewOpen)}>Preview</button>}
                {tab !== 'Liquid classes' && <button type="button" disabled={!freshCheck} onClick={() => download({ method: currentInput.method, compilation: freshCheck!.result }, `${String(draft.name || 'method')}-plan.json`)}>Export plan</button>}
            </div></div>
            <details className="bioxp-method-library"><summary>Library tools</summary><details><summary>Import, export & copy</summary>
                <button type="button" disabled={storageBusy} onClick={() => { delete saveTargets.current[saveTarget]; setActive({ ...clone(activeDraft), ...(Array.isArray(activeDraft.steps) ? { steps: (activeDraft.steps as MethodValue[]).map(duplicateNode), ...(Array.isArray(activeDraft.procedures) ? { procedures: (activeDraft.procedures as MethodValue[]).map(p => ({ ...p, ...(Array.isArray(p.steps) ? { steps: (p.steps as MethodValue[]).map(duplicateNode) } : {}) })) } : {}) } : {}) }); if (tab === 'Liquid classes') setSavedLiquid(null); else setSavedMethod(null); setNotice('Duplicated as an unsaved draft with new step identities.'); }}>Duplicate</button>
                <button type="button" onClick={() => download(collection === 'library' ? workspaceSnapshot(activeDraft, { bindings, dependencies, initialState }) : activeDraft, `${String(activeDraft.name || 'method')}.json`)}>Export draft JSON</button>
                <label>Import JSON<input aria-label="Import JSON" disabled={storageBusy} type="file" accept="application/json,.json" onChange={e => { const file = e.target.files?.[0], version = editVersion.current; if (file) void storage(async () => { const value = JSON.parse(await file.text()); if (!value || typeof value !== 'object' || Array.isArray(value)) throw new Error('Import must contain an object.'); if (version !== editVersion.current) { setNotice('Import completed after an edit or tab change; editable draft retained. Import again explicitly.'); return; } const hydrated = workspaceHydrate(value); delete saveTargets.current[saveTarget]; setActive(hydrated.method); if (collection === 'library') setValues(hydrated.inputs); if (collection === 'liquid-classes') setSavedLiquid(null); else setSavedMethod(null); setNotice('Imported into a new unsaved draft; existing identities were not overwritten.'); }); }} /></label>
            </details>
            <details><summary>Library revisions & comparison</summary><label>Search<input aria-label="Library search" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label>
                <select aria-label="Revision" value={revision} onChange={e => setRevision(e.target.value)}><option value="">Latest exact read</option>{methodRows<MethodRecord>(revisions.data).map(row => <option key={row.revision} value={row.revision}>{row.revision}</option>)}</select>
                <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous library page</button><button type="button" disabled={methodRows(library.data).length < 25} onClick={() => setOffset(offset + 25)}>Next library page</button>
                <label>Compare from revision<input aria-label="Compare from revision" value={fromRevision} onChange={e => setFromRevision(e.target.value)} /></label><button type="button" disabled={!selected || !revision || !fromRevision || storageBusy} onClick={() => void storage(async () => setDiff(await methodsGet(`${collection}/${enc(selected)}/diff`, { from_revision: fromRevision, to_revision: revision })))}>Revision diff</button>
                <button type="button" disabled={!selected || storageBusy} onClick={() => void storage(async () => download(await methodsGet(`${collection}/${enc(selected)}/export`, revision ? { revision } : undefined), 'method-export.json'))}>Export saved revision</button>
                {diff !== undefined && <section aria-label="Revision changes"><h4>Changes</h4><ul>{methodRows<MethodValue>(object(diff).changes).map((c, i) => <li key={i}>{String(c.op)} {String(c.path)}</li>)}</ul></section>}
            </details></details>
            {String(activeDraft.schema).startsWith('bms.bioxp-workflow-draft.') && <button type="button" disabled={storageBusy} onClick={() => void storage(async () => { const migrated = await methodsPost<MethodValue>('migrate', { method: clone(activeDraft) }); setActive(object(migrated.method ?? migrated)); setNotice('Migrated into an editable method; original legacy content is retained by the migration contract. Save explicitly to create a revision.'); })}>Migrate legacy draft</button>}
            {tab === 'Liquid classes' ? <><label>Source starter variant<select aria-label="Source starter variant" value="" onChange={e => { const entry = methodRows<MethodValue>(starters.data)[Number(e.target.value)]; if (entry) { delete saveTargets.current['liquid-classes']; setActive({ ...clone(entry), name: String(entry.name ?? entry.id) }); setSavedLiquid(null); } }}><option value="">Choose an exact source context…</option>{methodRows<MethodValue>(starters.data).map((entry, i) => <option key={String(entry.id)} value={i}>{String(entry.name || entry.id)} · revision {String(entry.revision)}{plainContext(entry.context) ? ` · ${plainContext(entry.context)}` : ''}</option>)}</select></label><button type="button" disabled={storageBusy} onClick={() => void storage(async () => download(await methodsGet('liquid-classes/source'), 'Cavro-source-catalog.json'))}>Export original source catalog</button>{plainContext(liquid.context) && <p>Context: {plainContext(liquid.context)}</p>}<p>Requested, resolved, emitted and reported-applied are separate values. Blank source values are not zero. T10 design intent does not establish adapter or consumable support.</p><RepairWorkspaceClassEditor catalog={catalog.data} schema={schema.data?.liquid_class_schema ? object(schema.data.liquid_class_schema) as Schema : liquidClassEditorSchema} value={liquid} onChange={setActive} />
                <button type="button" onClick={() => { delete saveTargets.current['liquid-classes']; setLiquid(clone(liquid)); setSavedLiquid(null); setNotice('Selected class content pinned into a new draft; original revision is unchanged. Applied status is not inferred.'); }}>Pin selected current entry as new class</button>
            </> : <>
                <details className="bioxp-method-start"><summary>Start from a workflow</summary><section aria-label="New experiment" className="bioxp-new-experiment">
                    <label>Workflow kind<select aria-label="Workflow kind" value={workflowKind} onChange={e => { setWorkflowKind(e.target.value); setSeparation(''); }}><option value="">Choose workflow…</option>{ex.map(sample => <option key={String(sample.id)} value={String(sample.id)}>{String(sample.name ?? object(sample.method).name)}</option>)}</select></label>
                    {variants.length > 0 && <label>Purification approach<select aria-label="Purification approach" value={separation} onChange={e => setSeparation(e.target.value)}><option value="">Choose separation explicitly…</option><option value="external">Operator / external separation</option>{variants.map(variant => <option key={String(variant.id)} value={String(variant.id)}>{String(variant.label)}</option>)}</select></label>}
                    <button type="button" disabled={storageBusy || !starter || variants.length > 0 && !separation} onClick={() => { const sample = variants.find(v => v.id === separation) ?? starter; if (sample) createExperiment(sample); }}>New experiment</button>
                    {variants.find(v => v.id === separation) && <p>{String(variants.find(v => v.id === separation)?.description)}</p>}
                    <small>Choose a starting structure; amounts and processing settings stay yours to author.</small>
                </section></details>
                <details ref={examplesRef} className="bioxp-method-fixtures"><summary>Examples & software fixtures</summary>
                <label>Scientific example<select aria-label="Scientific example" disabled={storageBusy} value="" onChange={e => { const sample = ex[Number(e.target.value)]; if (sample) { delete saveTargets.current[saveTarget]; setActive(clone(object(sample.method ?? sample))); setValues({ bindings: clone(object(sample.bindings)), dependencies: clone(object(sample.dependencies)), initialState: clone(object(sample.initial_state)) }); setSavedMethod(null); } }}><option value="">Choose a skeleton…</option>{ex.map((sample, i) => <option key={i} value={i}>{String(sample.name ?? object(sample.method).name ?? i)}</option>)}</select></label>
                <label>Bound software fixture<select aria-label="Bound software fixture" disabled={storageBusy} value="" onChange={e => { const fixture = object(ex[Number(e.target.value)]?.bound_fixture); if (fixture.method) { delete saveTargets.current[saveTarget]; setActive(clone(object(fixture.method))); setValues({ bindings: clone(object(fixture.bindings)), dependencies: clone(object(fixture.dependencies)), initialState: clone(object(fixture.initial_state)) }); setSavedMethod(null); setNotice(String(fixture.fixture_provenance ?? 'Software fixture only; not scientific or physical qualification.')); } }}><option value="">Choose explicit test inputs…</option>{ex.map((sample, i) => sample.bound_fixture ? <option key={i} value={i}>{String(sample.name ?? object(sample.method).name ?? i)} · software fixture only</option> : null)}</select></label>
                </details>
                <RepairWorkspaceContext.Provider value={{ method: activeDraft, dependencies, entries: methodRows<MethodValue>(starters.data), pin: entry => { const existing = dependencies.liquid_classes; if (existing !== undefined && !Array.isArray(existing)) { setNotice('Selected exact content retained in Transfer. Existing non-array dependencies are unchanged.'); return; } setDependencies({ ...dependencies, liquid_classes: [...(Array.isArray(existing) ? existing : []).filter(row => object(row).id !== entry.id || object(row).revision !== entry.revision), clone(entry)] }); } }}>
                <BioXpWorkflowProfilesContext.Provider value={{ profiles: [...(Array.isArray(plates.data?.profiles) ? plates.data!.profiles as MethodValue[] : []), ...publishedTipProfiles(methodRows<MethodValue>(starters.data))], custodyObjects: Array.isArray(object(object(catalog.data?.authoring).custody).objects) ? object(object(catalog.data?.authoring).custody).objects as MethodValue[] : [], loading: starters.isLoading, error: starters.isError, pin: profile => { if (dependencies.labware_profiles !== undefined && !Array.isArray(dependencies.labware_profiles)) setNotice('Profile identity assigned. Retained null/expression profile dependencies were not replaced; edit Dependencies explicitly to pin its source metadata.'); editVersion.current++; setValues(previous => ({ ...previous, dependencies: pinLabwareProfile(previous.dependencies, profile) })); } }}>
                <BioXpMethodDeckWorkbench onShowExamples={() => { const panel = examplesRef.current; if (panel) { panel.open = true; panel.scrollIntoView({ block: 'center' }); panel.querySelector<HTMLSelectElement>('select')?.focus(); } }} key={`method-${authoringEpoch}`} method={activeDraft} onChange={setActive} bindings={bindings} onBindingsChange={setBindings} catalog={catalog.data ?? {}} rootSchema={rootSchema} findings={methodStepFindings(check?.result, check ? object(check.input.method) : draft)} preview={freshCheck?.result} initialState={methodInitialState(check?.result.simulation, check?.input.initial_state)} dependencies={dependencies} />
                </BioXpWorkflowProfilesContext.Provider></RepairWorkspaceContext.Provider>
                <details><summary>Parameters, expressions and bindings</summary><MethodFields label="Parameters" schema={properties.parameters ?? methodParameterSchema} rootSchema={rootSchema} value={activeDraft.parameters ?? []} onChange={v => setActive({ ...activeDraft, parameters: v })} /><MethodFields label="Bindings" schema={methodBindingsSchema(activeDraft, catalog.data ?? {})} value={bindings} onChange={v => setBindings(object(v))} />
                    <select aria-label="Parameter preset" value="" onChange={e => void storage(async () => { const preset = await methodsGet<MethodRecord>(`presets/${enc(e.target.value)}`); setBindings(clone(object(preset.method.bindings))); setDependencies(clone(object(preset.method.dependencies))); })}><option value="">Load preset…</option>{methodRows<MethodRecord>(presets.data).map(p => <option key={p.id} value={p.id}>{p.name}</option>)}</select>
                    <button type="button" disabled={storageBusy} onClick={() => void storage(async () => { const preset = await methodsSave('presets', { schema: 'bms.bioxp-method-preset.v1', name: String(activeDraft.name), bindings: clone(bindings), dependencies: clone(dependencies) }, null); await methodsGet(`presets/${enc(preset.id)}/revisions/${preset.revision}`); await presets.refetch(); setNotice('Parameter preset saved.'); })}>Save parameter preset</button>
                </details>
                <details><summary>Procedures and pinned dependencies</summary><BioXpMethodProcedures catalog={catalog.data ?? {}} rootSchema={rootSchema} value={activeDraft.procedures} onChange={v => setActive({ ...activeDraft, procedures: v })} /><MethodFields label="Dependencies" value={dependencies} onChange={v => setDependencies(object(v))} /></details>
                <details><summary>Tip policy and initial assumptions</summary><MethodFields label="Tip policy" schema={properties.tip_policy ?? methodTipPolicySchema} rootSchema={rootSchema} value={activeDraft.tip_policy} onChange={v => setActive({ ...activeDraft, tip_policy: v })} /><MethodFields label="Initial assumptions" value={initialState} onChange={setInitialState} /><p>Planned contents are not observed robot state.</p></details>
                <details onToggle={e => { if (e.currentTarget.open) setAdvancedMethodVisited(true); }}><summary>All method fields and advanced controls</summary>{advancedMethodVisited && <MethodFields label="Method" schema={rootSchema} value={activeDraft} onChange={v => setActive(object(v))} />}</details>
                <section aria-label="Method findings" className="bioxp-method-findings">
                    {checkError && <p role="status">Preview unavailable right now ({checkError}). Your draft is unchanged; Run still works.</p>}
                    {!check && !checkError && <p role="status">{hasContent ? 'Preparing preview…' : 'Add a step or set up the deck to see a preview.'}</p>}
                    {check && <BioXpMethodPlanSummary result={check.result} method={object(check.input.method)} stale={!freshCheck} />}
                    {previewOpen && check && <details open><summary>Step-by-step preview</summary><BioXpMethodApplicationFields value={check.result.resolved} /><BioXpMethodPreview result={check.result} initialState={check.input.initial_state} custodyDestinations={Array.isArray(object(object(catalog.data?.authoring).custody).destinations) ? object(object(catalog.data?.authoring).custody).destinations as MethodValue[] : []} /></details>}
                </section>
                <section aria-label="Run method" className="bioxp-method-run"><p>{unchangedSinceSave ? `Runs saved revision ${savedMethod!.revision}.` : savedMethod ? 'Runs the current edited draft; the saved revision is unchanged. Save keeps these edits for later.' : 'Runs the current draft. Save is optional; an exact copy is kept with the run.'}</p>
                <label><input type="checkbox" checked={ack} onChange={e => setAck(e.target.checked)} />Acknowledge live robot execution</label><button type="button" disabled={busy || !connected || !ack} onClick={() => void submit()}>Run</button></section>
            </>}
        </div>
        <div role="tabpanel" id="method-runs-panel" aria-labelledby="method-tab-2" hidden={tab !== 'Runs'}><h3>Runs</h3>
            <section aria-label="Run history"><h4>Started from this browser</h4>{history.length === 0 && <p>No runs started here yet.</p>}<ul>{history.map(entry => <li key={entry.jobId}><button type="button" aria-pressed={run?.jobId === entry.jobId} onClick={() => { setRun(entry); setAccepted(null); reportGeneration.current = generation; setReport(reportCache.current[`${generation}:${entry.jobId}`] ?? null); }}>{entry.name} · {when(entry.startedAt)}{entry.refused ? ' · not started' : ''}</button></li>)}</ul></section>
            <details><summary>Recent robot jobs</summary><label>Search recent jobs<input aria-label="Run search" value={search} onChange={e => { setSearch(e.target.value); setOffset(0); }} /></label><button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - 25))}>Previous run page</button><button type="button" disabled={offset >= 75 || methodRows(runs.data).length < 25} onClick={() => setOffset(Math.min(75, offset + 25))}>Next run page</button><button type="button" onClick={() => void runs.refetch()}>Refresh runs</button>
            <ul>{methodRows<MethodRun & { created_at?: string }>(runs.data).map(job => <li key={job.job_id}><button type="button" onClick={() => { const known = history.find(h => h.jobId === job.job_id); setRun(known ?? { jobId: job.job_id, key: '', generation, snapshot: object(job.method_snapshot), name: String(object(object(job.method_snapshot).method).name || 'Robot job'), startedAt: String(job.created_at ?? '') }); setAccepted(job); reportGeneration.current = generation; setReport(reportCache.current[`${generation}:${job.job_id}`] ?? null); }}>{history.find(h => h.jobId === job.job_id)?.name ?? String(object(object(job.method_snapshot).method).name || 'Robot job')} · {when(job.created_at)} · {job.status}</button></li>)}</ul><small>Latest jobs window from the robot, not complete history.</small></details></div>
        {run && <section aria-label="Immutable method run"><h3>{run.name}</h3><p>Started {when(run.startedAt)}{run.saved ? ` · saved revision ${run.saved.revision}` : ' · unsaved draft'} <BioXpCopyId label="job ID" value={run.jobId} /> <BioXpCopyId label="request key" value={run.key} /></p>{runMessage && <p role="status">{runMessage}</p>}<p>An exact copy of this method is kept with the run; later edits never change it.</p>
            {run.refused ? <p>Definite pre-admission refusal; nothing started for this submission. No nonexistent-job polling.</p> : <BioXpWorkflowJobMonitor onObserved={setLiveObservation} methodsFacade visible={visible && (tab === 'Runs' || tab === 'Methods')} jobId={run.jobId} generation={generation} connected={connected} controlsEnabled={controlsEnabled} busyRef={busyRef} onBusyChange={setBusy} submitting={busy} pending acceptedJob={accepted} />}
            {object(liveObservation).job_id === run.jobId && <BioXpMethodProgress report={object(object(object(liveObservation).execution).runtime_state)} />}
            <button type="button" disabled={storageBusy} onClick={() => void storage(async () => { const original = await methodsPost<MethodValue>(`runs/${enc(run.jobId)}/clone`, {}, { expected_connection_generation: generation }); delete saveTargets.current.library; setDraft(clone(object(original.method))); setMethodValues({ bindings: clone(object(original.bindings)), dependencies: clone(object(original.dependencies)), initialState: clone(original.initial_state) }); setSavedMethod(null); setTab('Methods'); editVersion.current++; setNotice('Original immutable method cloned as an unsaved draft. No execution submitted.', 'Methods'); })}>Clone original method</button>
            <button type="button" disabled={storageBusy} onClick={() => { const jobId = run.jobId, requestedGeneration = generation; void storage(async () => { const result = await methodsGet<MethodValue>(`runs/${enc(jobId)}/report`, { expected_connection_generation: requestedGeneration }); if (result.job_id !== undefined && result.job_id !== jobId) throw new Error('Report identity differs from the requested job; previous report retained.'); const addressed = { ...result, job_id: jobId }; reportCache.current[`${requestedGeneration}:${jobId}`] = addressed; if (reportIdentity.current.jobId !== jobId || reportIdentity.current.generation !== requestedGeneration) return; reportGeneration.current = requestedGeneration; setReport(addressed); }); }}>Load method report</button>
            {visibleReport && <><h4>Report</h4><BioXpMethodProgress report={visibleReport} /><BioXpMethodApplicationFields value={report} /><button type="button" onClick={() => download(report, `${String(report!.job_id)}-report.json`)}>Export report</button>
                <label>Exact recovery occurrence<select aria-label="Exact recovery occurrence" value={occurrence} onChange={e => { setOccurrence(e.target.value); setNativeAction(''); }}><option value="">Select original occurrence…</option>{reportProvenance(report).map((p: unknown, index: number) => <option key={String(object(p).occurrence_id)} value={String(object(p).occurrence_id)}>Step {index + 1}: {occurrenceSteps({ provenance: reportProvenance(report) } as MethodCompile, object(object(report?.method_snapshot).method)).get(String(object(p).occurrence_id))?.label ?? String(object(p).step_id ?? 'step')}</option>)}</select></label>
                <label>Exact native action<select aria-label="Recovery native action" value={nativeAction} onChange={e => setNativeAction(e.target.value)}><option value="">Whole authored occurrence (review partial children)</option>{reportProvenance(report).filter(p => p.occurrence_id === occurrence).flatMap(p => Array.isArray(p.native_action_ids) ? p.native_action_ids : []).map((action, index) => <option key={String(action)} value={String(action)}>Robot action {index + 1}</option>)}</select></label>
                <p>Unspecified recovery assumptions retain the original snapshot. Select null or edit explicitly to override; current next-draft assumptions are separate.</p><MethodFields label="Recovery initial assumptions" value={recoveryInitialState} onChange={setRecoveryInitialState} />{recoveryEvidence && <p role="status">Recovery draft prepared from the original run.</p>}<button type="button" disabled={storageBusy || !occurrence} onClick={() => void recovery()}>Create recovery draft</button><p>New draft only, never Continue-as-retry. Review partial effects, generated setup and excluded actions explicitly; no automatic lift, load or aspiration replay.</p></>}
        </section>}
    </section>;
}
