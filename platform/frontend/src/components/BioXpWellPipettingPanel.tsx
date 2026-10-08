import { useBioXpDocumentVisible } from './BioXpObservationVisibility';
import { useEffect, useRef, useState } from 'react';
import { BioXpPipetteResults } from './BioXpPipetteResults';
import { BioXpWorkflowMaterials } from './BioXpWorkflowMaterials';
import { BioXpWorkflowTransferEditor } from './BioXpWorkflowTransferEditor';
import { BioXpSavedWorkflowRun } from './BioXpSavedWorkflowRun';
import { emptyDeckPlan, emptyTransferIntent, previewBioXpWorkflow, type WorkflowDeckPlan, type WorkflowTransferIntent, type SavedWorkflowSnapshot, type WorkflowPreview, type WorkflowJobClone } from '../lib/bioxpWorkflowPlan';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import './BioXpWorkflowEditor.css';
import { BioXpCalibrationRun } from './BioXpCalibrationRun';
import { pipetteResults } from '../lib/bioxpPipetteResults';
import { bioXpErrorText, useBioXpWorkflowJob, useSubmitBioXpProtocol,
    type BioXpDeckDestinationV1, type BioXpWorkflowJob } from '../lib/bioxpClient';
import { BioXpSourcePipettingEditor, sourceDefaults, sourceLabels } from './BioXpSourcePipettingEditor';
import { type BioXpSourceStep, describeManualStep, manualPipettingDocument, type BioXpManualStep } from '../lib/bioxpManualPipetting';
import { createUserTemplate, updateUserTemplate, fetchUserTemplates, fetchUserTemplate, type UserTemplate } from '../lib/api';
import { isDraftObject, mergeDraftEdits, sameDraftValue, nativeIntent, readWorkflowDraft, type DraftObject, type NativeDraft, type WorkflowDraftRow } from '../lib/bioxpWorkflowDraft';

type Operation = BioXpManualStep['operation'] | 'transfer';
const operations: Operation[] = ['move', 'lower', 'lift', 'aspirate', 'dispense', 'mix', 'load_tip', 'measure_fluid_height', 'source_fluid_offset', 'diagnostic_detect_fluid', 'source_calwith_fluid'];
const allOperations: Operation[] = [...operations, ...Object.keys(sourceDefaults) as BioXpSourceStep['operation'][]];
const label = (operation: Operation) => operation in sourceLabels ? sourceLabels[operation as BioXpSourceStep['operation']] : operation === 'load_tip' ? 'Load tip' : operation === 'measure_fluid_height' ? 'Measure fluid height' : operation === 'source_fluid_offset' ? 'OEM fluid offset scan' : operation === 'diagnostic_detect_fluid' ? 'OEM Detect Fluid' : operation === 'source_calwith_fluid' ? 'OEM calibrate with fluid' : operation[0].toUpperCase() + operation.slice(1);
// A visual projection only: mergeDraftEdits retains missing/null/unknown raw fields.
const projectTransfer = (value: DraftObject): WorkflowTransferIntent => {
    const blank = emptyTransferIntent();
    const endpoint = (raw: unknown) => {
        const obj = isDraftObject(raw) ? raw : {};
        return { station: typeof obj.station === 'string' ? obj.station : '',
            location_id: typeof obj.location_id === 'string' || typeof obj.location_id === 'number' ? obj.location_id : '',
            wells: Array.isArray(obj.wells) ? obj.wells.filter((w): w is string => typeof w === 'string') : [] };
    };
    return { ...blank, ...Object.fromEntries(Object.keys(blank).filter(key => typeof value[key] === 'string' || typeof value[key] === 'number' || key.endsWith('lift_height_steps') && value[key] === null).map(key => [key, value[key]])),
        operation: 'transfer', source: endpoint(value.source), destination: endpoint(value.destination),
        channels: Array.isArray(value.channels) ? value.channels.filter((c): c is number => typeof c === 'number') : [] } as WorkflowTransferIntent;
};
const wells = [...'ABCDEFGH'].flatMap(row => Array.from({ length: 12 }, (_, col) => `${row}${col + 1}`));

export function BioXpWellPipettingPanel({ generation, connected, destinations = [], positionTableRevision, workflowAuthoring = false, controlsEnabled = false, visible = true }: { visible?: boolean;
    controlsEnabled?: boolean; workflowAuthoring?: boolean; generation: number; connected: boolean; destinations?: BioXpDeckDestinationV1[]; positionTableRevision?: string | null;
}) {
    const [sourceDrafts, setSourceDrafts] = useState<Record<BioXpSourceStep['operation'], NativeDraft<BioXpSourceStep>>>(sourceDefaults);
    const updateSource = (step: NativeDraft<BioXpSourceStep>) => setSourceDrafts(current => ({ ...current, [step.operation]: step }));
    const [tray, setTray] = useState('1');
    const [tipWell, setTipWell] = useState('A1');
    const [overpress, setOverpress] = useState(false);
    const [liftZ, setLiftZ] = useState(false);
    const [detectionSpeed, setDetectionSpeed] = useState('300');
    const [scanPlate, setScanPlate] = useState<'TC' | 'MS' | 'OC' | 'RC' | 'STRIP' | 'OCMS'>('TC');
    const [scanPrefill, setScanPrefill] = useState(false);
    const [scanSpacing, setScanSpacing] = useState('4');
    const [location, setLocation] = useState('');
    const [well, setWell] = useState('');
    const [flag, setFlag] = useState('');
    const [liftMode, setLiftMode] = useState('');
    const [height, setHeight] = useState('');
    const [channels, setChannels] = useState<number[]>([]);
    const [volume, setVolume] = useState('');
    const [aspirateSpeed, setAspirateSpeed] = useState('');
    const [dispenseSpeed, setDispenseSpeed] = useState('');
    const [cycles, setCycles] = useState('');
    const [operation, setOperation] = useState<Operation>('move');
    const [schema, setSchema] = useState<'bms.bioxp-workflow-draft.v1' | 'bms.bioxp-workflow-draft.v2'>('bms.bioxp-workflow-draft.v1');
    const [deckPlan, setDeckPlan] = useState<WorkflowDeckPlan>(emptyDeckPlan);
    const [transfer, setTransfer] = useState<WorkflowTransferIntent>(emptyTransferIntent);
    const [savedWorkflow, setSavedWorkflow] = useState<SavedWorkflowSnapshot | null>(null);
    const [preview, setPreview] = useState<{ result: WorkflowPreview; snapshot: string } | null>(null);
    const [previewBusy, setPreviewBusy] = useState(false);
    const previewLock = useRef(false);
    const [previewIndex, setPreviewIndex] = useState(0);
    const [deck, setDeck] = useState<BioXpDeckSelection>({ station: '', wells: [] });
    const [workflowView, setWorkflowView] = useState<'build' | 'review'>('build');
    const [steps, setSteps] = useState<WorkflowDraftRow[]>([]);
    const [workflowName, setWorkflowName] = useState('');
    const [workflowId, setWorkflowId] = useState<string | null>(null);
    const [savedNotice, setSavedNotice] = useState('');
    const [storageBusy, setStorageBusy] = useState(false);
    const storageLock = useRef(false);
    const [openList, setOpenList] = useState<UserTemplate[] | null>(null);
    const [editingId, setEditingId] = useState<string | null>(null);
    const editBaseline = useRef<DraftObject | null>(null);
    const storedEditor = useRef<DraftObject>({});
    const loadedForm = useRef<DraftObject>({});
    const captureHydratedForm = useRef(false);
    const editingChanged = useRef(false);
    const currentEditor = useRef('');
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState(false);
    const busy = useRef(false);
    const mounted = useRef(true);
    const connection = useRef({ generation, connected });
    connection.current = { generation, connected };
    useEffect(() => { mounted.current = true; return () => { mounted.current = false; }; }, []);
    const [attempt, setAttempt] = useState<{ id: string; key: string; generation: number } | null>(null);
    const [accepted, setAccepted] = useState<BioXpWorkflowJob | null>(null);
    const submit = useSubmitBioXpProtocol();
    const sameConnection = !attempt || attempt.generation === generation;
    const documentVisible = useBioXpDocumentVisible();
    const [settledJob, setSettledJob] = useState<string | null>(null);
    const observationId = attempt?.id ?? null;
    const observeJob = documentVisible && (visible || (observationId !== null && settledJob !== observationId));
    const query = useBioXpWorkflowJob(observationId, attempt?.generation ?? generation, connected && sameConnection && observeJob);
    useEffect(() => {
        if (query.data?.command?.terminal && query.data.job_id === observationId) setSettledJob(observationId);
    }, [query.data, observationId]);
    const mismatch = !!query.data && !!attempt && (query.data.job_id !== attempt.id || query.data.command?.idempotency_key !== attempt.key);
    const job = mismatch ? null : query.data ?? accepted;
    // Historical jobs, receipts, unavailable observations and unrelated pending
    // controls are evidence, never a new admission lock. Only our HTTP is reserved.
    const enabled = connected && !pending;
    const number = (value: string, name: string) => {
        if (!value.trim() || !Number.isFinite(Number(value))) throw new Error(`Enter ${name}.`);
        return Number(value);
    };
    const draft = (op: Operation): BioXpManualStep => {
        if (op in sourceDefaults) return nativeIntent(sourceDrafts[op as BioXpSourceStep['operation']] as unknown as DraftObject);
        if (op === 'load_tip') return { operation: op, tray: number(tray, 'tip tray'), well: tipWell, overpress, lift_z: liftZ };
        if (op === 'measure_fluid_height') return { operation: op, speed: number(detectionSpeed, 'detection speed') };
        if (op === 'source_fluid_offset') return { operation: op, plate: scanPlate, speed: number(detectionSpeed, 'detection speed'), transfer_fluid: scanPrefill, skip_steps: number(scanSpacing, 'sample spacing') };
        if (op === 'diagnostic_detect_fluid' || op === 'source_calwith_fluid') return { operation: op };
        if (op === 'move') {
            if (!flag) throw new Error('Select the move Z position.');
            return { operation: op, location_id: number(location, 'locationID'), well, position_flag: Number(flag) as 0 | 1 | 2 };
        }
        if (op === 'lower') return { operation: op, location_id: number(location, 'locationID') };
        if (op === 'lift') {
            if (!liftMode) throw new Error('Select the lift target.');
            return { operation: op, location_id: number(location, 'locationID'), height_steps: liftMode === 'high' ? null : number(height, 'lift height in steps') };
        }
        const common = { channels: [...channels], volume_ul: number(volume, 'volume in µL') };
        if (op === 'mix') return { operation: op, ...common, aspirate_speed: number(aspirateSpeed, 'aspirate speed'),
            dispense_speed: number(dispenseSpeed, 'dispense speed'), cycles: number(cycles, 'mix cycles') };
        if (op !== 'aspirate' && op !== 'dispense') throw new Error('Choose a supported operation.');
        return { operation: op, ...common, speed: number(op === 'aspirate' ? aspirateSpeed : dispenseSpeed, `${op} speed`) };
    };
    const rawDraft = (op: Operation): DraftObject => {
        if (op === 'transfer') return transfer as unknown as DraftObject;
        if (op in sourceDefaults) return sourceDrafts[op as BioXpSourceStep['operation']] as unknown as DraftObject;
        if (op === 'load_tip') return { operation: op, tray, well: tipWell, overpress, lift_z: liftZ };
        if (op === 'measure_fluid_height') return { operation: op, speed: detectionSpeed };
        if (op === 'source_fluid_offset') return { operation: op, plate: scanPlate, speed: detectionSpeed, transfer_fluid: scanPrefill, skip_steps: scanSpacing };
        if (op === 'diagnostic_detect_fluid' || op === 'source_calwith_fluid') return { operation: op };
        if (op === 'move') return { operation: op, location_id: location, well, position_flag: flag };
        if (op === 'lower') return { operation: op, location_id: location };
        if (op === 'lift') return { operation: op, location_id: location, height_steps: liftMode === 'high' ? null : height };
        const common = { operation: op, channels: [...channels], volume_ul: volume };
        if (op === 'mix') return { ...common, aspirate_speed: aspirateSpeed, dispense_speed: dispenseSpeed, cycles };
        return { ...common, speed: op === 'aspirate' ? aspirateSpeed : dispenseSpeed };
    };
    useEffect(() => { if (editingId && !editBaseline.current) editBaseline.current = rawDraft(operation); });
    const append = () => {
        if (operation === 'transfer') setSchema('bms.bioxp-workflow-draft.v2');
        setSteps(current => [...current, { step_id: crypto.randomUUID(), intent: rawDraft(operation) }]);
        setError(null); setSavedNotice('');
    };
    const cancelEdit = () => { editingChanged.current = true; setEditingId(null); editBaseline.current = null; };
    const updateStep = () => {
        if (operation === 'transfer') setSchema('bms.bioxp-workflow-draft.v2');
        const after = rawDraft(operation), before = editBaseline.current;
        setSteps(current => current.map(row => row.step_id !== editingId ? row : { ...row,
            intent: row.intent.operation === operation && before
                ? mergeDraftEdits(row.intent, before, after) : after }));
        cancelEdit(); setSavedNotice('');
    };
    const form = (): DraftObject => ({ sourceDrafts: sourceDrafts as unknown as DraftObject, tray, tipWell, overpress, liftZ,
        detectionSpeed, scanPlate, scanPrefill, scanSpacing, location, well, flag, liftMode, height, channels,
        volume, aspirateSpeed, dispenseSpeed, cycles, operation, ...(workflowAuthoring ? { deck: deck as unknown as DraftObject, ...(operation === 'transfer' || schema === 'bms.bioxp-workflow-draft.v2' ? { transfer: transfer as unknown as DraftObject } : {}) } : {}) });
    currentEditor.current = JSON.stringify({ workflowName, steps, schema, deckPlan, form: form(), editingId });
    useEffect(() => { if (captureHydratedForm.current) { loadedForm.current = form(); captureHydratedForm.current = false; } });
    const hydrateForm = (value: DraftObject) => {
        const textSetters = { tray: setTray, tipWell: setTipWell, detectionSpeed: setDetectionSpeed, scanSpacing: setScanSpacing,
            location: setLocation, well: setWell, flag: setFlag, liftMode: setLiftMode, height: setHeight,
            volume: setVolume, aspirateSpeed: setAspirateSpeed, dispenseSpeed: setDispenseSpeed, cycles: setCycles };
        for (const [key, setter] of Object.entries(textSetters)) setter(typeof value[key] === 'string' ? value[key] as string : '');
        setOverpress(value.overpress === true); setLiftZ(value.liftZ === true); setScanPrefill(value.scanPrefill === true);
        setScanPlate((typeof value.scanPlate === 'string' ? value.scanPlate : '') as typeof scanPlate);
        setChannels(Array.isArray(value.channels) ? value.channels.filter((v): v is number => typeof v === 'number') : []);
        setOperation(value.operation === 'transfer' || allOperations.includes(value.operation as Operation) ? value.operation as Operation : 'move');
        setTransfer(isDraftObject(value.transfer) ? projectTransfer(value.transfer) : emptyTransferIntent());
        const savedDeck = isDraftObject(value.deck) ? value.deck : {};
        setDeck({ station: typeof savedDeck.station === 'string' ? savedDeck.station : '',
            wells: Array.isArray(savedDeck.wells) ? savedDeck.wells.filter((w): w is string => typeof w === 'string') : [] });
        // Only this UI's own complete source editor snapshots are hydrated here.
        // Arbitrary JSON from other clients is preserved in storedEditor unchanged.
        if (isDraftObject(value.sourceDrafts)) {
            const next = { ...sourceDefaults } as typeof sourceDrafts;
            for (const op of Object.keys(sourceDefaults) as BioXpSourceStep['operation'][]) {
                const saved = value.sourceDrafts[op];
                if (isDraftObject(saved) && saved.operation === op && (op !== 'diagnostic_pipette' || isDraftObject(saved.diagnostic))) next[op] = saved as unknown as NativeDraft<BioXpSourceStep>;
            }
            setSourceDrafts(next);
        } else setSourceDrafts(sourceDefaults);
    };
    async function storage(action: () => Promise<void>) {
        if (storageLock.current) return;
        storageLock.current = true; setStorageBusy(true); setError(null); setSavedNotice('');
        try { await action(); } catch (cause) { if (mounted.current) setError(bioXpErrorText(cause)); }
        finally { storageLock.current = false; if (mounted.current) setStorageBusy(false); }
    }
    const workflowDraft = () => {
        const editor_state = mergeDraftEdits(storedEditor.current, { form: loadedForm.current }, { form: form() });
        if (editingId) { editor_state.editing_step_id = editingId; editor_state.edit_baseline = editBaseline.current; }
        else if (editingChanged.current) { delete editor_state.editing_step_id; delete editor_state.edit_baseline; }
        return schema === 'bms.bioxp-workflow-draft.v2'
            ? { schema, steps, editor_state, deck_plan: deckPlan }
            : { schema, steps, editor_state };
    };
    const requestPreview = async () => {
        if (previewLock.current) return;
        previewLock.current = true; setPreviewBusy(true); setError(null); setPreview(null);
        const snapshot = currentEditor.current;
        try {
            const result = await previewBioXpWorkflow(structuredClone(workflowDraft()), 'bms-workflow-preview');
            if (mounted.current) { setPreview({ result, snapshot }); setPreviewIndex(0); }
        } catch (cause) { if (mounted.current) setError(bioXpErrorText(cause)); }
        finally { previewLock.current = false; if (mounted.current) setPreviewBusy(false); }
    };
    const save = () => void storage(async () => {
        if (!workflowName.trim()) throw new Error('Enter a workflow name.');
        const savedSnapshot = currentEditor.current;
        const body = { name: workflowName, mode: 'bioxp_workflow', model_id: null, base_template_id: null,
            params: workflowDraft() };
        const result = workflowId ? await updateUserTemplate(workflowId, body) : await createUserTemplate(body);
        // Retain an accepted create's ID even if the verification GET fails, so
        // retrying Save updates that record rather than creating a duplicate.
        if (mounted.current) setWorkflowId(result.data.id);
        // Read the exact record, not a cached listing, before reporting durable Save.
        const readback = await fetchUserTemplate(result.data.id);
        if (readback.data.id !== result.data.id || readback.data.mode !== 'bioxp_workflow' || readback.data.name !== workflowName
            || readback.data.model_id !== null || readback.data.base_template_id !== null
            || !sameDraftValue(readback.data.params, body.params)) throw new Error('Workflow save readback differs; unsaved editor retained.');
        if (!mounted.current) return;
        setSavedWorkflow({ id: readback.data.id, name: readback.data.name, draft: structuredClone(readWorkflowDraft(readback.data.params)) });
        setSavedNotice(currentEditor.current === savedSnapshot
            ? 'Saved draft.'
            : 'Saved the earlier draft snapshot. Newer editor changes are not saved.');
    });
    const listWorkflows = () => void storage(async () => {
        const result = await fetchUserTemplates(undefined, undefined, 'bioxp_workflow');
        if (mounted.current) setOpenList(result.data.filter(row => row.mode === 'bioxp_workflow'));
    });
    const hydrateWorkflow = (saved: ReturnType<typeof readWorkflowDraft>) => {
        setSchema(saved.schema); setDeckPlan(saved.schema === 'bms.bioxp-workflow-draft.v2' ? saved.deck_plan : emptyDeckPlan());
        editingChanged.current = false;
        storedEditor.current = saved.editor_state;
        loadedForm.current = isDraftObject(saved.editor_state.form) ? saved.editor_state.form : {};
        hydrateForm(loadedForm.current); captureHydratedForm.current = true;
        setSteps(saved.steps);
        const selected = saved.editor_state.editing_step_id;
        setEditingId(typeof selected === 'string' && saved.steps.some(row => row.step_id === selected) ? selected : null);
        editBaseline.current = isDraftObject(saved.editor_state.edit_baseline) ? saved.editor_state.edit_baseline : null;
    };
    // The request's callback captures the editor it was started from. A late job
    // read/clone cannot replace newer edits or an in-flight template save.
    const cloningEditor = currentEditor.current;
    const adoptJobClone = (clone: WorkflowJobClone) => {
        if (!mounted.current) return;
        if (storageLock.current || currentEditor.current !== cloningEditor) throw new Error('Editor changed while cloning. Your draft was retained; clone again when ready.');
        if (!clone.draft) throw new Error(clone.issues.map(issue => issue.message).join('; ') || 'No authoring draft was returned.');
        hydrateWorkflow(readWorkflowDraft(structuredClone(clone.draft)));
        setWorkflowId(null); setSavedWorkflow(null); setPreview(null); setOpenList(null);
        setWorkflowName(clone.name ?? 'Cloned job'); setWorkflowView('build'); setError(null);
        setSavedNotice('Cloned job as an unsaved workflow. Save creates a new template; the original job is unchanged.');
    };
    const openWorkflow = (id: string) => void storage(async () => {
        const openingSnapshot = currentEditor.current;
        const result = await fetchUserTemplate(id);
        if (currentEditor.current !== openingSnapshot) throw new Error('Editor changed while opening. Your draft was retained; open again when ready.');
        if (result.data.id !== id || result.data.mode !== 'bioxp_workflow' || result.data.model_id !== null || result.data.base_template_id !== null) throw new Error('Not a BioXP workflow draft.');
        const saved = readWorkflowDraft(result.data.params);
        if (!mounted.current) return;
        hydrateWorkflow(saved);
        setSavedWorkflow({ id, name: result.data.name, draft: structuredClone(saved) }); setPreview(null);
        setWorkflowId(id); setWorkflowName(result.data.name); setOpenList(null);
        setSavedNotice('Opened draft from database.');
    });
    async function run(op?: Operation, explicitStep?: BioXpManualStep) {
        if (!enabled || busy.current) return;
        busy.current = true; setPending(true); setError(null);
        try {
            const document = manualPipettingDocument({ protocol_id: 'bms-manual-pipetting', steps: explicitStep ? [explicitStep] : op ? [draft(op)] : steps.map(row => nativeIntent(row.intent)) });
            const key = crypto.randomUUID();
            const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(key));
            const id = `protocol-live-${Array.from(new Uint8Array(digest), byte => byte.toString(16).padStart(2, '0')).join('')}`;
            if (!mounted.current) return;
            if (!connection.current.connected || connection.current.generation !== generation) throw new Error('Connection changed before submission.');
            setAttempt({ id, key, generation }); setAccepted(null);
            const result = await submit.mutateAsync({ source_type: 'native', document, dry_run: false,
                live_execution: { live_execution_ack: true }, idempotency_key: key, expected_connection_generation: generation });
            if (!mounted.current) return;
            if (result.job_id !== id || result.command?.idempotency_key !== key) throw new Error('Robot returned a different submission identity.');
            setAccepted(result);
        } catch (cause) { if (mounted.current) setError(bioXpErrorText(cause)); }
        finally { busy.current = false; if (mounted.current) setPending(false); }
    }
    const reorder = (index: number, delta: number) => setSteps(current => {
        const next = [...current]; [next[index], next[index + delta]] = [next[index + delta], next[index]]; return next;
    });
    const edit = (index: number, inPlace = false) => {
        const row = steps[index], step = row.intent;
        if (step.operation === 'transfer' && workflowAuthoring) {
            setOperation('transfer'); setTransfer(projectTransfer(step)); setEditingId(inPlace ? row.step_id : null); editBaseline.current = null; return;
        }
        if (!allOperations.includes(step.operation as Operation)) { setError('Unknown draft retained. Its native editor is not available.'); return; }
        setOperation(step.operation as Operation); setEditingId(inPlace ? row.step_id : null); editBaseline.current = null;
        if (workflowAuthoring) {
            const station = deckStations.find(item => step.operation === 'load_tip'
                ? item.tipTray !== null && String(item.tipTray) === String(step.tray)
                : item.locationId !== null && String(item.locationId) === String(step.location_id));
            if (station) setDeck({ station: station.id, wells: typeof step.well === 'string' ? [step.well] : [] });
        }
        const text = (value: unknown) => typeof value === 'string' || typeof value === 'number' ? String(value) : '';
        if (typeof step.operation === 'string' && Object.hasOwn(sourceDefaults, step.operation)) {
            // Keep absent fields absent. Only a malformed diagnostic container
            // needs an empty visual projection; the original row remains intact.
            updateSource({ ...step, ...(step.operation === 'diagnostic_pipette' && !isDraftObject(step.diagnostic) ? { diagnostic: {} } : {}) } as unknown as NativeDraft<BioXpSourceStep>);
            return;
        }
        if (step.operation === 'load_tip') { setTray(text(step.tray)); setTipWell(text(step.well)); setOverpress(step.overpress === true); setLiftZ(step.lift_z === true); }
        if (step.operation === 'measure_fluid_height') setDetectionSpeed(text(step.speed));
        if (step.operation === 'source_fluid_offset') { setScanPlate(text(step.plate) as typeof scanPlate); setDetectionSpeed(text(step.speed)); setScanPrefill(step.transfer_fluid === true); setScanSpacing(text(step.skip_steps)); }
        if (['move', 'lower', 'lift'].includes(String(step.operation))) setLocation(text(step.location_id));
        if (step.operation === 'move') { setWell(text(step.well)); setFlag(text(step.position_flag)); }
        if (step.operation === 'lift') { setLiftMode(step.height_steps === null ? 'high' : 'height'); setHeight(text(step.height_steps)); }
        if (['aspirate', 'dispense', 'mix'].includes(String(step.operation))) {
            setChannels(Array.isArray(step.channels) ? step.channels.filter((v): v is number => typeof v === 'number') : []); setVolume(text(step.volume_ul));
            if (step.operation === 'mix') { setCycles(text(step.cycles)); setAspirateSpeed(text(step.aspirate_speed)); setDispenseSpeed(text(step.dispense_speed)); }
            else if (step.operation === 'aspirate') setAspirateSpeed(text(step.speed));
            else setDispenseSpeed(text(step.speed));
        }
    };
    const pickedStation = deckStations.find(station => station.id === deck.station);
    const selectedRow = steps.find(row => row.step_id === editingId);
    const positionOperation = ['move', 'lower', 'lift'].includes(operation);
    const liquidOperation = ['aspirate', 'dispense', 'mix'].includes(operation);
    const sourceOperation = Object.hasOwn(sourceDefaults, operation);
    const show = (...ops: Operation[]) => !workflowAuthoring || ops.includes(operation);
    const workflowLabel = (intent: DraftObject) => {
        if (intent.operation === 'transfer') return 'Transfer';
        if (intent.operation === 'diagnostic_pipette' && isDraftObject(intent.diagnostic))
            return intent.diagnostic.action === 'eject' ? 'Eject selected tips (diagnostic)' : 'Pipette diagnostic';
        return typeof intent.operation === 'string' && allOperations.includes(intent.operation as Operation)
            ? label(intent.operation as Operation) : 'Unrecognized step';
    };
    const workflowSummary = (intent: DraftObject) => {
        if (intent.operation === 'transfer') return `${isDraftObject(intent.source) ? intent.source.station : 'Source'} → ${isDraftObject(intent.destination) ? intent.destination.station : 'Destination'} · ${intent.volume_ul === '' || intent.volume_ul == null ? 'Volume not set' : `${intent.volume_ul} µL`}`;
        const station = deckStations.find(item => String(item.locationId) === String(intent.location_id));
        if (['move', 'lower', 'lift'].includes(String(intent.operation))) return [station?.label ?? (intent.location_id !== '' && intent.location_id != null ? `Station ${intent.location_id}` : 'Choose a station'), intent.well, intent.height_steps != null ? `Height ${intent.height_steps} steps` : null, intent.position_flag != null ? `Position ${intent.position_flag}` : null].filter(Boolean).join(' · ');
        if (intent.operation === 'load_tip') return `Tip tray ${intent.tray || '—'} · ${intent.well || 'Choose a well'}`;
        if (Array.isArray(intent.channels)) return `${intent.volume_ul === '' || intent.volume_ul == null ? 'Volume not set' : `${intent.volume_ul} µL`} · ${intent.channels.length ? `pipettes ${intent.channels.map(c => Number(c) + 1).join(', ')}` : 'Choose pipettes'}`;
        if (intent.operation === 'source_load_tips') return `T${intent.tip_type ?? '—'} · ${intent.pipette === -1 ? 'all four' : typeof intent.pipette === 'number' ? `pipette ${intent.pipette + 1}` : 'Choose pipettes'}`;
        return typeof intent.operation === 'string' && !allOperations.includes(intent.operation as Operation) ? `${intent.operation} · retained draft` : 'Open step settings';
    };
    const applyDeckSelection = () => {
        if (!pickedStation) return;
        if (operation === 'load_tip' && pickedStation.tipTray !== null && deck.wells.length === 1) {
            setTray(String(pickedStation.tipTray)); setTipWell(deck.wells[0]);
        } else if (positionOperation && pickedStation.locationId !== null) {
            setLocation(String(pickedStation.locationId));
            if (operation === 'move' && deck.wells.length === 1) setWell(deck.wells[0]);
        }
        setSavedNotice('');
    };
    const description = (intent: DraftObject) => {
        if (intent.operation === 'transfer') return 'Ordered head-reference pairs; Preview shows the explicit native expansion.';
        try { return describeManualStep(nativeIntent(intent)); }
        catch { return `${typeof intent.operation === 'string' ? intent.operation : 'Unknown operation'} · incomplete or unknown fields retained`; }
    };
    const orderedSteps = workflowAuthoring ? (
        <aside className="bioxp-workflow-card bioxp-step-list" aria-label="Workflow steps">
            <div className="flex items-center justify-between gap-3">
                <h3>Steps <span className="bioxp-muted">{steps.length}</span></h3>
                <button type="button" onClick={cancelEdit} className="bioxp-secondary">New step</button>
            </div>
            {steps.length === 0 && <p className="bioxp-empty">Start with an action from the step editor. Nothing is added automatically.</p>}
            <ol className="mt-3 space-y-2">{steps.map((step, index) => <li key={step.step_id} className={`bioxp-step-row ${editingId === step.step_id ? 'is-selected' : ''}`} data-manual-step={index} data-step-id={step.step_id}>
                <button type="button" className="bioxp-step-select" aria-label={`Edit step ${index + 1}`} aria-pressed={editingId === step.step_id} onClick={() => edit(index, true)}>
                    <span className="bioxp-step-number">{index + 1}</span><span><strong>{workflowLabel(step.intent)}</strong><small>{workflowSummary(step.intent)}</small></span>
                </button>
                <div className="bioxp-step-tools">
                    <button type="button" aria-label={`Move step ${index + 1} up`} title="Move up" disabled={index === 0} onClick={() => reorder(index, -1)}>↑</button>
                    <button type="button" aria-label={`Move step ${index + 1} down`} title="Move down" disabled={index === steps.length - 1} onClick={() => reorder(index, 1)}>↓</button>
                    <button type="button" aria-label={`Clone step ${index + 1}`} onClick={() => setSteps(current => [...current, { ...step, step_id: crypto.randomUUID(), intent: structuredClone(step.intent) }])}>Copy</button>
                    <button type="button" aria-label={`Remove step ${index + 1}`} onClick={() => { setSteps(current => current.filter(row => row.step_id !== step.step_id)); if (editingId === step.step_id) cancelEdit(); }}>Remove</button>
                </div>
            </li>)}</ol>
        </aside>
    ) : (
        <details open={workflowAuthoring || undefined} className="space-y-2 rounded border border-slate-700 p-3"><summary className="cursor-pointer font-semibold">{workflowAuthoring ? "Workflow steps" : "Ordered well-to-well program"}</summary>
            <p className="text-sm">Author each step explicitly. For a transfer: Move → Lower → Aspirate → Lift, then select the destination and add Move → Lower → Dispense → Lift. Adding, copying and reordering do not move hardware.</p>
            <label>Step to append<select aria-label="Step to append" value={operation} onChange={e => setOperation(e.target.value as Operation)} className="ml-2 bg-slate-950 p-2">{allOperations.map(op => <option key={op} value={op}>{label(op)}</option>)}</select></label>
            <button type="button" onClick={append} className="ml-2 rounded border px-3 py-2">Append step</button>
            {editingId && <span className="ml-2">Editing step {steps.findIndex(row => row.step_id === editingId) + 1}
                <button type="button" onClick={updateStep}>Update step</button> <button type="button" onClick={cancelEdit}>Cancel editing</button>
            </span>}
            <ol className="space-y-2">{steps.map((step, index) => <li key={step.step_id} className="flex flex-wrap items-center gap-2" data-manual-step={index} data-step-id={step.step_id}>
                <span>{index + 1}. Draft · {description(step.intent)}</span>
                <button type="button" aria-label={`Edit step ${index + 1}`} onClick={() => edit(index, true)}>Edit step</button>
                <button type="button" aria-label={`Copy step ${index + 1} to editor`} onClick={() => edit(index)}>Copy to editor</button>
                <button type="button" aria-label={`Clone step ${index + 1}`} onClick={() => setSteps(current => [...current, { ...step, step_id: crypto.randomUUID(), intent: structuredClone(step.intent) }])}>Clone step</button>
                <button type="button" aria-label={`Move step ${index + 1} up`} disabled={index === 0} onClick={() => reorder(index, -1)}>↑</button>
                <button type="button" aria-label={`Move step ${index + 1} down`} disabled={index === steps.length - 1} onClick={() => reorder(index, 1)}>↓</button>
                <button type="button" aria-label={`Remove step ${index + 1}`} onClick={() => { setSteps(current => current.filter(row => row.step_id !== step.step_id)); if (editingId === step.step_id) cancelEdit(); }}>Remove</button>
            </li>)}</ol>
            {!workflowAuthoring && <button type="button" disabled={!enabled} onClick={() => void run()} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">Run ordered steps</button>}
        </details>
    );
    return <section onChangeCapture={() => setSavedNotice('')} aria-label={workflowAuthoring ? "Workflow editor" : "Well pipetting"} className={workflowAuthoring ? 'bioxp-workflow-editor' : 'mt-4 min-w-0 space-y-3 rounded border border-slate-700 p-4 [&_select]:max-w-full [&_select]:rounded [&_select]:bg-slate-950 [&_select]:p-2 [&_input[type=number]]:min-w-0 [&_input[type=number]]:rounded [&_input[type=number]]:bg-slate-950 [&_input[type=number]]:p-2'}>
        {!workflowAuthoring && <>
            <section aria-label="Tip ejection" className="space-y-2 rounded border border-slate-700 p-3">
                <h3 className="font-semibold">Tip ejection</h3>
                <p className="text-sm">Releases tips at the current head position. Move over waste first; these buttons do not move the head.</p>
                <fieldset><legend className="text-sm">Pipettes to eject</legend><div className="flex flex-wrap gap-4">
                    {[0, 1, 2, 3].map(channel => <label key={channel}><input type="checkbox" aria-label={`Eject pipette ${channel + 1}`} checked={channels.includes(channel)}
                        onChange={e => setChannels(current => e.target.checked ? [...current, channel].sort() : current.filter(c => c !== channel))} /> Pipette {channel + 1}</label>)}
                </div></fieldset>
                <p className="text-xs">Shared with the liquid-stroke selection below. Only tips detected on the requested pipettes receive an eject command.</p>
                <div className="flex flex-wrap gap-2">
                    <button type="button" disabled={!enabled} onClick={() => void run(undefined, { operation: 'diagnostic_pipette', diagnostic: { action: 'eject', channels: [0, 1, 2, 3] } })}
                        className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">Eject all tips</button>
                    <button type="button" disabled={!enabled || channels.length === 0} onClick={() => void run(undefined, { operation: 'diagnostic_pipette', diagnostic: { action: 'eject', channels: [...channels] } })}
                        title={channels.length === 0 ? 'Choose pipettes above, or use Eject all tips.' : undefined}
                        className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">Eject selected tips</button>
                </div>
            </section>
            <h3 className="font-semibold">Well pipetting</h3>
        </>}
        {workflowAuthoring && <div className="bioxp-workflow-toolbar" aria-label="Saved workflow">
            <label className="bioxp-workflow-name">Workflow name<input aria-label="Workflow name" placeholder="Untitled workflow" value={workflowName} onChange={e => { setWorkflowName(e.target.value); setSavedNotice(''); }} /></label>
            <div className="flex flex-wrap gap-2">
                <button type="button" className="bioxp-primary" disabled={storageBusy} onClick={save}>Save workflow</button>
                <button type="button" disabled={storageBusy} onClick={listWorkflows}>Open workflow</button>
                <button type="button" disabled={storageBusy} onClick={() => { if (workflowId) storedEditor.current = { ...storedEditor.current, cloned_from_workflow_id: workflowId }; setWorkflowId(null); setSavedWorkflow(null); setPreview(null); setWorkflowName(`${workflowName || 'Untitled workflow'} copy`); setSavedNotice('Cloned as an unsaved workflow. Save creates a new template; the original is unchanged.'); }}>Clone workflow</button>
                <button type="button" disabled={storageBusy} onClick={() => { setWorkflowId(null); setSavedWorkflow(null); setPreview(null); setSchema('bms.bioxp-workflow-draft.v1'); setDeckPlan(emptyDeckPlan()); setTransfer(emptyTransferIntent()); setWorkflowName(''); setSteps([]); cancelEdit(); storedEditor.current = {}; loadedForm.current = {}; setDeck({ station: '', wells: [] }); setWorkflowView('build'); setSavedNotice('New empty workflow.'); }}>New workflow</button>
            </div>
            <div className="bioxp-view-tabs" role="tablist" aria-label="Workflow views">
                {(['build', 'review'] as const).map(view => <button type="button" key={view} role="tab" aria-selected={workflowView === view} onClick={() => setWorkflowView(view)}>{view === 'build' ? 'Build' : 'Review'}</button>)}
            </div>
            {storageBusy && <p role="status">Accessing workflow database…</p>}
            {savedNotice && <p role="status">{savedNotice}</p>}
            {openList && <div aria-label="Workflow list"><p>Opening replaces the current editor; save any work you want to keep.</p>
                {openList.length === 0 && <p>No saved BioXP workflows.</p>}
                {openList.map(row => <button className="mr-3" type="button" key={row.id} disabled={storageBusy} onClick={() => openWorkflow(row.id)}>{row.name}</button>)}
                <button type="button" onClick={() => setOpenList(null)}>Close workflow list</button>
            </div>}
        </div>}
        {workflowAuthoring && <section hidden={workflowView !== 'review'} className="bioxp-workflow-card" aria-label="Workflow review">
            <h3>{workflowName || 'Untitled workflow'}</h3>
            <p className="bioxp-muted">Draft review only. Preview does not move hardware, measure liquid, or submit a robot job. Saved execution is a separate explicit action below.</p>
            {editingId && <p className="bioxp-notice">The selected step has a separate editing form. Use Update step to apply it to this list before saving.</p>}
            <ol className="mt-4 space-y-3">{steps.map((row, index) => <li key={row.step_id}>
                <button type="button" onClick={() => { edit(index, true); setWorkflowView('build'); }} className="bioxp-review-step">{index + 1}. {workflowLabel(row.intent)} — {workflowSummary(row.intent)}</button>
                <details><summary>Step settings</summary><p className="bioxp-muted">{description(row.intent)}</p>{Object.hasOwn(row, 'required_capability') && <p>Original controller requirement: {row.required_capability ?? 'none'}</p>}<pre className="bioxp-native-json">{JSON.stringify(row.intent, null, 2)}</pre></details>
            </li>)}</ol>
            {!steps.length && <p className="bioxp-empty">No steps yet. Return to Build to add your first action.</p>}
            <button type="button" className="mt-4" disabled={previewBusy} onClick={() => void requestPreview()}>{previewBusy ? 'Previewing…' : 'Preview workflow'}</button>
            {preview && <section aria-label="Logical native preview" className="bioxp-logical-preview">
                {preview.snapshot !== currentEditor.current && <p role="status">Preview is an earlier draft snapshot. Preview again to include current edits.</p>}
                {preview.result.issues.map((issue, index) => <p role="alert" key={index}>{issue.step_id ? `Step ${issue.step_id}: ` : ''}{issue.message}</p>)}
                {!preview.result.document && <p>No native document produced. Correct the reported fields and preview again.</p>}
                {preview.result.document && <>
                    <p>{preview.result.actions.length} native actions. Logical order only, not a physical simulation.</p>
                    {preview.result.actions.length > 0 && <>
                        <label>Native action<input aria-label="Native action scrubber" type="range" min={0} max={preview.result.actions.length - 1} value={previewIndex} onChange={event => setPreviewIndex(Number(event.target.value))} /></label>
                        <div className="bioxp-preview-navigation"><button type="button" disabled={previewIndex === 0} onClick={() => setPreviewIndex(index => index - 1)}>Previous native action</button><button type="button" disabled={previewIndex >= preview.result.actions.length - 1} onClick={() => setPreviewIndex(index => index + 1)}>Next native action</button></div>
                        <p aria-live="polite">{previewIndex + 1}. {preview.result.actions[previewIndex].label} · Step {preview.result.actions[previewIndex].step_id}{preview.result.actions[previewIndex].pair_index !== null ? ` · pair ${preview.result.actions[previewIndex].pair_index! + 1}` : ''}</p>
                        <BioXpWorkflowDeck readOnly selection={{ station: preview.result.actions[previewIndex].station ?? '', wells: preview.result.actions[previewIndex].well ? [preview.result.actions[previewIndex].well!] : [] }} onChange={() => {}} />
                        <h4>Effective native fields</h4><pre aria-label="Effective native fields" className="bioxp-native-json">{JSON.stringify({ kind: preview.result.actions[previewIndex].kind, params: preview.result.actions[previewIndex].params }, null, 2)}</pre>
                    </>}
                    <details><summary>Native document</summary><pre className="bioxp-native-json">{JSON.stringify(preview.result.document, null, 2)}</pre></details>
                </>}
            </section>}
            <BioXpSavedWorkflowRun visible={visible && workflowView === 'review'} saved={savedWorkflow} generation={generation} connected={connected} controlsEnabled={controlsEnabled} onClone={adoptJobClone} authoringBusy={storageBusy} />
            <button type="button" className="mt-4" onClick={() => setWorkflowView('build')}>Back to Build</button>
        </section>}
        <div className={workflowAuthoring ? 'bioxp-build-grid' : undefined} hidden={workflowAuthoring && workflowView !== 'build'}>
        {workflowAuthoring && <>{orderedSteps}<div className="bioxp-workflow-card bioxp-deck-column">
            <BioXpWorkflowDeck selection={deck} onChange={setDeck} />
            <details className="bioxp-materials-disclosure"><summary>Planned labware & materials</summary>
                <BioXpWorkflowMaterials plan={deckPlan} onChange={plan => { setDeckPlan(plan); setSchema('bms.bioxp-workflow-draft.v2'); setSavedNotice(''); }} selection={deck} />
            </details>
            <div className="bioxp-deck-adoption">
                {positionOperation || operation === 'load_tip' ? <>
                    <button type="button" className="bioxp-secondary" onClick={applyDeckSelection}
                        disabled={!pickedStation || (operation === 'load_tip' ? pickedStation.tipTray === null || deck.wells.length !== 1 : pickedStation.locationId === null || operation === 'move' && deck.wells.length !== 1)}>
                        {operation === 'load_tip' ? 'Use selected tip well' : operation === 'move' ? 'Use as reference well' : 'Use selected station'}
                    </button>
                    <p className="bioxp-muted">{operation === 'move' || operation === 'load_tip' ? 'Choose one well for this step. Multiple selections remain part of the deck plan.' : 'Lower and Lift act vertically in place; they do not move to a new station.'}</p>
                </> : <p className="bioxp-muted">{liquidOperation ? 'Liquid strokes act in place. Add a Move step to position the head at a selected well.' : 'Deck selection is planning only. This action uses its own settings.'}</p>}
            </div>
        </div></>}
        <section className={workflowAuthoring ? 'bioxp-workflow-card bioxp-step-inspector' : 'space-y-3'} aria-label="Step settings">
        <h3 className="font-semibold">{workflowAuthoring ? editingId ? `Step ${steps.findIndex(row => row.step_id === editingId) + 1}` : 'New step' : 'Native step editor'}</h3>
        {workflowAuthoring && <label className="bioxp-step-type">Action<select aria-label="Step to append" value={operation} onChange={e => { setOperation(e.target.value as Operation); if (e.target.value === 'transfer') setSchema('bms.bioxp-workflow-draft.v2'); }}>
            <option value="transfer">Transfer</option>
            <optgroup label="Position & liquid">{allOperations.filter(op => ['move','lower','lift','aspirate','dispense','mix'].includes(op)).map(op => <option key={op} value={op}>{label(op)}</option>)}</optgroup>
            <optgroup label="Tips & source procedures">{allOperations.filter(op => ['load_tip','source_load_tips','source_mix','source_aspirate_air','source_dispense_air','source_purge'].includes(op)).map(op => <option key={op} value={op}>{label(op)}</option>)}</optgroup>
            <optgroup label="Diagnostics & calibration">{allOperations.filter(op => ['measure_fluid_height','source_fluid_offset','diagnostic_detect_fluid','source_calwith_fluid','diagnostic_pipette'].includes(op)).map(op => <option key={op} value={op}>{label(op)}</option>)}</optgroup>
        </select></label>}
        {workflowAuthoring && operation === 'transfer' && <BioXpWorkflowTransferEditor value={transfer} onChange={setTransfer} selection={deck} onSelect={setDeck} />}
        {(!workflowAuthoring || positionOperation) && <p className="text-sm">Move positions the head at a station and reference well. Lower and Lift act vertically in place.</p>}
        {!workflowAuthoring && <p className="text-sm text-amber-200">Choose which pipettes aspirate, dispense or mix. This does not load tips or change tip alignment. Use Load selected tips for physical loading. With four tips, the selected well positions the head; the tips keep their fixed spacing.</p>}
        {workflowAuthoring && liquidOperation && <p className="text-sm">Choose which pipettes perform liquid strokes. Their spacing is fixed; this does not position the head or load tips.</p>}
        {!workflowAuthoring && <details className="text-xs text-slate-300"><summary>Position details</summary><p>Calibration revision: {positionTableRevision ?? 'unavailable'}. Not every well is usable at every station.</p></details>}
        <div hidden={!show('move', 'lower', 'lift')} className="grid gap-3 sm:grid-cols-3">
            <label>Station<select aria-label="Station" className="block w-full bg-slate-950 p-2" value={(workflowAuthoring ? deckStations.filter(s => s.locationId !== null).map(s => ({ location_id: s.locationId })) : destinations).some(d => String(d.location_id) === location) ? location : ''} onChange={e => setLocation(e.target.value)}>
                <option value="">Select a station</option>
                {workflowAuthoring ? deckStations.filter(s => s.locationId !== null).map(s => <option key={s.id} value={s.locationId!}>{s.label}</option>) : destinations.map(d => <option key={d.target} value={d.location_id}>{d.label}</option>)}
            </select></label>
            <details open={!workflowAuthoring || undefined} className={workflowAuthoring ? 'bioxp-native-detail' : undefined}><summary>Native location ID</summary>
                <label>Location number<input aria-label="Location number" className="block w-full bg-slate-950 p-2" type="number" step="1" value={location} onChange={e => setLocation(e.target.value)} /></label>
            </details>
            <label hidden={!show('move')}>Move Z position<select aria-label="Move Z position" className="block w-full bg-slate-950 p-2" value={flag} onChange={e => setFlag(e.target.value)}>
                <option value="">Select height</option><option value="0">Clearance height</option><option value="1">Calibrated high</option><option value="2">Calibrated low</option>
            </select></label>
        </div>
        <fieldset hidden={!show('move')} className="min-w-0"><legend>Reference well: {well || 'not selected'}</legend>
            {workflowAuthoring && <label>Reference well<input aria-label="Reference well" value={well} placeholder="Choose on deck or enter a well" onChange={e => setWell(e.target.value)} /></label>}
            <div hidden={workflowAuthoring} className="grid grid-cols-12 gap-1 overflow-x-auto" aria-label="Reference well grid">
                {wells.map(value => <button key={value} type="button" aria-label={`Reference well ${value}`} aria-pressed={well === value}
                    className={`min-w-7 rounded border p-1 text-xs ${well === value ? 'border-cyan-300 bg-cyan-800' : 'border-slate-700 bg-slate-950'}`} onClick={() => setWell(value)}>{value}</button>)}
            </div>
        </fieldset>
        <div hidden={!show('lift')} className="grid gap-3 sm:grid-cols-3">
            <label>Lift target<select aria-label="Lift target" className="block w-full bg-slate-950 p-2" value={liftMode} onChange={e => setLiftMode(e.target.value)}>
                <option value="">Select calibrated target</option><option value="high">Calibrated high</option><option value="height">Height above calibrated low</option>
            </select></label>
            {liftMode === 'height' && <label>Lift height (steps)<input aria-label="Lift height (steps)" type="number" step="1" value={height} onChange={e => setHeight(e.target.value)} className="block w-full bg-slate-950 p-2" /></label>}
            <p className="text-sm">Lower and Lift move vertically at the current position, not to the selected well.</p>
        </div>
        <fieldset hidden={!show('aspirate', 'dispense', 'mix')}><legend>Pipettes for liquid strokes</legend><div className="flex flex-wrap gap-4">
            {[0, 1, 2, 3].map(channel => <label key={channel}><input type="checkbox" aria-label={`Plunger ${channel + 1}`} checked={channels.includes(channel)} onChange={e => setChannels(current => e.target.checked ? [...current, channel].sort() : current.filter(c => c !== channel))} /> Pipette {channel + 1}</label>)}
        </div></fieldset>
        <div hidden={!show('aspirate', 'dispense', 'mix')} className="grid gap-3 sm:grid-cols-4">
            {([['Volume (µL)', volume, setVolume], ['Aspirate speed', aspirateSpeed, setAspirateSpeed], ['Dispense speed', dispenseSpeed, setDispenseSpeed], ['Mix cycles', cycles, setCycles]] as const).map(([name, value, setter]) =>
                <label key={name} hidden={workflowAuthoring && (name === 'Mix cycles' ? operation !== 'mix' : name === 'Aspirate speed' ? operation === 'dispense' : name === 'Dispense speed' ? operation === 'aspirate' : false)}>{name}<input aria-label={name} className="block w-full bg-slate-950 p-2" type="number" step={name === 'Mix cycles' ? '1' : 'any'} value={value} onChange={e => setter(e.target.value)} /></label>)}
        </div>
        <p hidden={!show('mix')} className="text-xs">Mix repeats the selected aspiration and dispense strokes (1–50 cycles). Speeds use controller units.</p>
        {!workflowAuthoring && <div className="flex flex-wrap gap-2">{operations.filter(op => ['move', 'lower', 'lift', 'aspirate', 'dispense', 'mix'].includes(op)).map(op => <button type="button" key={op} disabled={!enabled} onClick={() => void run(op)} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">{label(op)} now</button>)}</div>}
        <details hidden={!show('load_tip')} open={workflowAuthoring && operation === 'load_tip' || undefined} className="rounded border border-slate-700 p-3">
            <summary className="cursor-pointer font-semibold">Tips: tray pickup</summary>
            <p className="my-2 text-sm">Picks up a tip from the selected tray and well. This moves the head.</p>
            <div className="my-3 grid gap-3 sm:grid-cols-2">
            <label>Tip tray<select aria-label="Tip tray" value={tray} onChange={e => setTray(e.target.value)}>{[1,2,3,4,5].map(n => <option key={n}>{n}</option>)}</select></label>
            <label>Tip well<select aria-label="Tip well" value={tipWell} onChange={e => setTipWell(e.target.value)}>{wells.filter(w => /^[AB]/.test(w)).map(w => <option key={w}>{w}</option>)}</select></label>
            <label><input aria-label="Overpress" type="checkbox" checked={overpress} onChange={e => setOverpress(e.target.checked)} />Overpress</label>
            <label><input aria-label="Lift Z after pickup" type="checkbox" checked={liftZ} onChange={e => setLiftZ(e.target.checked)} />Lift Z after pickup</label>
            </div>
        {!workflowAuthoring && <div className="flex flex-wrap gap-2">{(['load_tip'] as Operation[]).map(op => <button type="button" key={op} disabled={!enabled} onClick={() => void run(op)} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">{label(op)} now</button>)}</div>}
        </details>
        <details hidden={!show('measure_fluid_height','source_fluid_offset','diagnostic_detect_fluid','source_calwith_fluid')} open={workflowAuthoring || undefined} className="rounded border border-slate-700 p-3">
            <summary className="cursor-pointer font-semibold">Fluid diagnostics & calibration</summary>
            <div className="my-3 grid gap-3 sm:grid-cols-2">
            <label hidden={!show('measure_fluid_height','source_fluid_offset')}>Detection speed<input aria-label="Detection speed" type="number" step="1" value={detectionSpeed} onChange={e => setDetectionSpeed(e.target.value)} /></label>
            <label hidden={!show('source_fluid_offset')}>Offset scan plate<select aria-label="Offset scan plate" value={scanPlate} onChange={e => setScanPlate(e.target.value as typeof scanPlate)}>{(['TC', 'MS', 'OC', 'RC', 'STRIP', 'OCMS'] as const).map(plate => <option key={plate}>{plate}</option>)}</select></label>
            <label hidden={!show('source_fluid_offset')}><input aria-label="Prefill scan plate" type="checkbox" checked={scanPrefill} onChange={e => setScanPrefill(e.target.checked)} />Prefill from trough (OEM)</label>
            <label hidden={!show('source_fluid_offset')}>Sample every N wells<input aria-label="Sample every N wells" type="number" min="1" step="1" value={scanSpacing} onChange={e => setScanSpacing(e.target.value)} /></label>
            </div>
            <p className="my-2 text-sm">Measure height at the current well, or scan the selected plate. Prefill transfers liquid. Detect Fluid moves the pool plate, scans five stations and parks on success. Neither diagnostic saves calibration.</p>
            <p className="my-2 text-sm">Calibrate with fluid saves and applies calibration, including partial results. Reject restores the FULL pre-run calibration, replacing later edits. These controls do not restart or home the robot.</p>
        {!workflowAuthoring && <div className="flex flex-wrap gap-2">{(['measure_fluid_height', 'source_fluid_offset', 'diagnostic_detect_fluid', 'source_calwith_fluid'] as Operation[]).map(op => <button type="button" key={op} disabled={!enabled} onClick={() => void run(op)} className="rounded bg-cyan-800 px-3 py-2 disabled:opacity-35">{label(op)} now</button>)}</div>}
        </details>
        {(!workflowAuthoring || sourceOperation) && <BioXpSourcePipettingEditor drafts={sourceDrafts} onChange={updateSource} enabled={enabled} run={workflowAuthoring ? undefined : op => void run(op)} selectedOperation={workflowAuthoring ? operation as BioXpSourceStep['operation'] : undefined} />}
        {workflowAuthoring && <div className="bioxp-inspector-actions">
            {selectedRow ? <><button type="button" className="bioxp-primary" onClick={updateStep}>Update step</button><button type="button" onClick={cancelEdit}>Cancel editing</button></> : <button type="button" className="bioxp-primary" onClick={append}>Add step</button>}
        </div>}
        </section>
        </div>
        {!workflowAuthoring && orderedSteps}
        {pending && <p role="status">Submitting pipetting program…</p>}
        {attempt && <p className="break-all text-xs">Job {attempt.id} · request {attempt.key}</p>}
        {!sameConnection && <p role="alert">Connection changed. Earlier job belongs to connection {attempt?.generation}.</p>}
        {mismatch && <p role="alert">Job identity mismatch; check robot status.</p>}
        {query.error && <p role="alert">Job readback unavailable: {bioXpErrorText(query.error)}</p>}
        {job && <><p role="status">Robot job: {job.command?.status ?? job.status} · {job.execution?.runtime_state.workflow?.phase ?? 'phase unavailable'}. Job acceptance is not physical proof.</p>
            {job.execution?.runtime_state.action_results?.map((result, index) =>
                <BioXpPipetteResults key={index} value={result} />)}</>}
        {[...new Set((job?.execution?.runtime_state.action_results ?? []).flatMap(result =>
            pipetteResults(result).flatMap(value => value.run_id ? [value.run_id] : [])))].map(runId =>
            <BioXpCalibrationRun key={`${attempt?.generation}:${runId}`} runId={runId}
                generation={attempt?.generation ?? generation} connected={connected && sameConnection} />)}
        {!workflowAuthoring && <details className="rounded border border-slate-700 p-3"><summary className="cursor-pointer font-semibold">Review a calibration run</summary><BioXpCalibrationRun key={`recover:${generation}`} generation={generation} connected={connected} /></details>}
        {error && <p role="alert">{error}</p>}
    </section>;
}
