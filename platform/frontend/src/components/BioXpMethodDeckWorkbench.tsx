import { useEffect, useRef, useState } from 'react';
import { insertMethodAction, methodCanvasEntries, editCanvasNode, plateBoundEndpoint, methodCarryLinks, duplicateCanvasNode, isThermalProgram } from '../lib/bioxpMethodCanvas';
import { thermalTimerWait } from '../lib/bioxpMethodThermal';
import { methodStateAfter } from '../lib/bioxpMethodSimulation';
import type { MethodCompile } from '../lib/bioxpMethods';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import type { MethodCatalog, MethodFinding, MethodValue } from '../lib/bioxpMethods';
import { methodInputBinding, removeMethodInputParameters } from '../lib/bioxpMethodInputBinding';
import { deckResources, deckStations, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import type { WorkflowDeckPlan } from '../lib/bioxpWorkflowPlan';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { BioXpMethodPreparation } from './BioXpMethodPreparation';
import { BioXpMethodPlateLayer } from './BioXpMethodPlateLayer';
import { BioXpWorkflowMaterials } from './BioXpWorkflowMaterials';
import { BioXpMethodPipettingEditor } from './BioXpMethodPipettingEditor';
import { BioXpMethodCustodyEditor, custodyMethodActions, custodyMetadata } from './BioXpMethodCustodyEditor';
import { isMethodNumber, methodNumber } from '../lib/bioxpMethodNumber';
import { BioXpMethodThermalEditor, thermalMethodActions } from './BioXpMethodThermalEditor';
import { MethodOutline, MethodFields, methodBindingsSchema, object, findMethodNode } from './BioXpMethodFields';

/** Selection and setup are draft-only. Bound edits stay with the existing binding owner. */
export function BioXpMethodDeckWorkbench({ method, onChange, catalog, rootSchema, findings, flat = false, actionProperties, bindings = {}, onBindingsChange, preview, initialState, dependencies = {} }: {
    method: MethodValue; onChange: (method: MethodValue) => void; catalog: MethodCatalog;
    rootSchema: Schema; findings?: MethodFinding[]; flat?: boolean;
    bindings?: MethodValue; onBindingsChange?: (bindings: MethodValue) => void;
    preview?: MethodCompile; initialState?: unknown; dependencies?: MethodValue;
    actionProperties?: (node: MethodValue, onChange: (node: MethodValue) => void) => React.ReactNode;
}) {
    const [selection, setSelection] = useState<BioXpDeckSelection>({ station: '', wells: [] });
    const [selectedId, setSelectedId] = useState(() => String(object(method.editor_state).instrument_path ?? ''));
    const [transferMode, setTransferMode] = useState('class');
    const [sequenceOnly, setSequenceOnly] = useState(false);
    const [setupOpen, setSetupOpen] = useState(false);
    const [plateId, setPlateId] = useState('');
    const [wellContents, setWellContents] = useState(false);
    const [carryPlate, setCarryPlate] = useState<string | null>(null);
    const [carryNotice, setCarryNotice] = useState('');
    const [editorOpen, setEditorOpen] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [emptyInstrument, setEmptyInstrument] = useState('');
    const sheetRef = useRef<HTMLElement>(null);
    const [editorHost, setEditorHost] = useState<HTMLDivElement | null>(null);
    const returnFocus = useRef<HTMLElement | SVGElement | null>(null);
    const activationOrigin = useRef<HTMLElement | SVGElement | null>(null);
    const [occurrence, setOccurrence] = useState('');
    const [waitTimer, setWaitTimer] = useState('');
    const [connecting, setConnecting] = useState(false);
    const [toolsOpen, setToolsOpen] = useState(false);
    const [addHost, setAddHost] = useState<HTMLDivElement | null>(null);
    const [connectionSource, setConnectionSource] = useState<BioXpDeckSelection | null>(null);
    const occurrenceState = occurrence ? methodStateAfter(preview?.simulation, initialState, occurrence) : undefined;
    const openEditor = () => { const active = activationOrigin.current ?? document.activeElement as HTMLElement; if (!sheetRef.current?.contains(active)) returnFocus.current = active; activationOrigin.current = null; setEditorOpen(true); };
    const closeEditor = () => { setEditorOpen(false); const origin = returnFocus.current; if (origin?.isConnected) origin.focus(); else document.querySelector<HTMLElement>('[aria-label="Authoring view"] button')?.focus(); };
    // React portal events follow the outline tree, not the visual sheet ancestor.
    useEffect(() => {
        const sheet = sheetRef.current;
        const dismiss = (event: KeyboardEvent) => { if (event.key === 'Escape' && editorOpen) { event.preventDefault(); event.stopPropagation(); closeEditor(); } };
        sheet?.addEventListener('keydown', dismiss);
        return () => sheet?.removeEventListener('keydown', dismiss);
    }, [editorOpen]);
    const selectStep = (id: string) => { setEmptyInstrument(''); setSelectedId(id); setSetupOpen(false); openEditor(); };
    const [additionName, setAdditionName] = useState('');
    const [insertion, setInsertion] = useState('after');
    const nodes = Array.isArray(method.steps) ? method.steps as MethodValue[] : [];
    const entries = methodCanvasEntries(nodes);
    const selected = entries.find(e => e.path === selectedId)?.node ?? findMethodNode(nodes, selectedId) ?? findMethodNode(nodes, String(object(method.editor_state).selected_step_id ?? '')) ?? nodes[0];
    const selectedPath = entries.find(e => e.node === selected)?.path ?? '';
    const station = deckStations.find(s => s.id === selection.station);
    const endpointReady = station?.locationId != null && selection.wells.length > 0;
    const moveReady = endpointReady && selection.wells.length === 1;
    const binding = methodInputBinding(method, selected ?? {}, bindings);
    const inputs = object(binding.inputs);
    const boundInputs = !binding.editable || !!binding.id && !onBindingsChange;
    const action = selected?.action === 'native_intent' ? inputs.operation : selected?.action;
    const hasAction = (name: string) => (catalog.actions ?? []).some(a => (a.action ?? a.id) === name);
    const append = (action: string, inputs: MethodValue, label?: string) => {
        const step_id = crypto.randomUUID();
        const added = { step_id, type: 'action', action, ...(label ? { label } : {}), inputs };
        onChange({ ...method, steps: insertMethodAction(nodes, added, selectedPath, insertion) });
        setToolsOpen(false);
        selectStep(step_id);
    };
    const updateInputs = (next: MethodValue) => {
        if (binding.id && onBindingsChange) onBindingsChange({ ...bindings, [binding.id]: next });
        else onChange({ ...method, steps: editCanvasNode(nodes, selectedPath, n => ({ ...n, inputs: next })) });
    };
    const adoptEndpoint = (key: 'source' | 'destination') => {
        if (!endpointReady || action !== 'transfer') return;
        const labware = plan.labware.filter(l => plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station === station!.id);
        updateInputs({ ...inputs, [key]: { ...object(inputs[key]), station: station!.id, location_id: station!.locationId, wells: [...selection.wells], ...(labware.length === 1 ? { labware_id: labware[0].id } : {}) } });
    };
    const endpointText = (key: string) => {
        const endpoint = object(inputs[key]);
        const labware = plan.labware.find(l => l.id === endpoint.labware_id);
        const label = labware?.name || deckStations.find(s => s.id === endpoint.station)?.label || endpoint.station;
        return label ? `${label} · ${Array.isArray(endpoint.wells) ? endpoint.wells.join(', ') : 'no wells'}` : 'Not selected';
    };
    const rawPlan = object(method.deck_plan);
    const plan = { ...rawPlan, labware: Array.isArray(rawPlan.labware) ? rawPlan.labware : [], materials: Array.isArray(rawPlan.materials) ? rawPlan.materials : [], assignments: Array.isArray(rawPlan.assignments) ? rawPlan.assignments : [] } as WorkflowDeckPlan;
    const thermalBindingProjection = (node: MethodValue, change: (next: MethodValue) => void) => {
        const binding = methodInputBinding(method, node, bindings);
        if (node.type !== 'action' || !binding.id || !binding.editable || !onBindingsChange) return { node, onChange: change };
        return { node: { ...node, inputs: binding.inputs }, onChange: (next: MethodValue) => {
            if (next.type !== node.type || next.action !== node.action) { change(next); return; }
            if (next.inputs !== binding.inputs) onBindingsChange({ ...bindings, [binding.id!]: next.inputs });
            // Bindings own input edits; keep the exact original expression on the AST.
            if (Object.keys(next).some(key => key !== 'inputs' && next[key] !== node[key])) change({ ...next, inputs: node.inputs });
        } };
    };
    const properties = (node: MethodValue, change: (next: MethodValue) => void) => {
        const here = entries.find(e => e.node === node)?.path;
        const sibling = here ? here.replace(/\d+$/, n => String(Number(n) + 1)) : '';
        const parent = here?.replace(/\d+$/, '') ?? '';
        const siblings = entries.filter(e => e.path.startsWith(parent) && !e.path.slice(parent.length).includes('/'));
        const starts = siblings.filter(e => e.node.action === 'timer_start');
        const setpoints = siblings.filter(e => e.node.action === 'chiller_setpoint');
        const start = entries.find(e => e.path === sibling && e.node.action === 'timer_start') ?? (parent !== '/steps/' && starts.length === 1 && setpoints.length === 1 ? starts[0] : undefined);
        const timerBinding = start ? methodInputBinding(method, start.node, bindings) : undefined;
        const timerInputs = object(timerBinding?.inputs);
        const waits = entries.filter(e => e.node.action === 'timer_wait' && object(methodInputBinding(method, e.node, bindings).inputs).timer_id === timerInputs.timer_id);
        const timing = node.action === 'chiller_setpoint' && start ? <section aria-label="Existing chiller timing task"><h3>Elapsed conditioning timer</h3><p>Timer {String(timerInputs.timer_id ?? 'not reported')} · {waits.some(w => w.path === start.path.replace(/\d+$/, n => String(Number(n) + 1))) ? 'Wait here' : 'Continue other steps; wait later'}</p><label>Elapsed seconds<input aria-label="Existing chiller elapsed seconds" disabled={!timerBinding?.editable || !!timerBinding?.id && !onBindingsChange || !(timerInputs.seconds === undefined || typeof timerInputs.seconds === 'string' || typeof timerInputs.seconds === 'number' || isMethodNumber(timerInputs.seconds))} value={isMethodNumber(timerInputs.seconds) ? timerInputs.seconds.expr.value : typeof timerInputs.seconds === 'string' || typeof timerInputs.seconds === 'number' ? String(timerInputs.seconds) : ''} onChange={e => { const inputs = { ...timerInputs, seconds: methodNumber(e.target.value) }; if (timerBinding?.id) onBindingsChange?.({ ...bindings, [timerBinding.id]: inputs }); else onChange({ ...method, steps: editCanvasNode(nodes, start.path, n => ({ ...n, inputs })) }); }} /></label><p>Elapsed from dispatch, not target attainment. No automatic Off.</p>{waits.map(w => <button key={w.path} type="button" onClick={() => selectStep(w.path)}>Edit linked wait · {w.path}</button>)}{!waits.length && <p>No authored wait for this timer. Insert a later wait explicitly.</p>}</section> : node.action === 'chiller_setpoint' && starts.length ? <section aria-label="Retained chiller timer links"><p>No unique authored task association. These timer nodes remain separate; a new elapsed timer adds new nodes, not an inferred replacement.</p>{starts.map(entry => <button type="button" key={entry.path} onClick={() => selectStep(entry.path)}>Edit timer {String(object(methodInputBinding(method, entry.node, bindings).inputs).timer_id ?? 'not reported')} · {entry.path}</button>)}</section> : null;

        if (['group', 'repeat'].includes(String(node.type)) && isThermalProgram(node)) return <BioXpMethodThermalEditor embedded onDuplicateScope={(source, replace) => { const result = duplicateCanvasNode(method, source, bindings, !!onBindingsChange); const path = entries.find(e => e.node === node)!.path; onChange({ ...method, ...(result.changed ? { parameters: result.parameters } : {}), steps: editCanvasNode(nodes, path, () => replace(result.node)) }); if (result.changed) onBindingsChange?.(result.bindings); }} bindingProjection={thermalBindingProjection} node={node} onChange={change} catalog={catalog} compact expanded={expanded} onExpandedChange={setExpanded} />;
        if (node.type === 'repeat' && !Object.hasOwn(node, 'items')) {
            const expr = object(object(node.count).expr);
            const parameter = expr.version === 1 && expr.op === 'param' ? (Array.isArray(method.parameters) ? method.parameters as MethodValue[] : []).find(p => p.id === expr.id && p.type === 'integer') : undefined;
            const id = parameter ? String(parameter.id) : undefined;
            const value = id ? Object.hasOwn(bindings, id) ? bindings[id] : parameter?.default : node.count;
            const editable = value === undefined || typeof value === 'number' || typeof value === 'string' || isMethodNumber(value);
            return <section aria-label="Repeated sequence settings"><label>Repeat count<input aria-label="Repeat count" inputMode="numeric" type="text" disabled={!editable || !!id && !onBindingsChange || !id && !!expr.op} value={isMethodNumber(value) ? value.expr.value : editable && value !== undefined ? String(value) : ''} placeholder={editable ? 'Not set' : 'Retained expression / null'} onChange={e => { const next = isMethodNumber(value) ? methodNumber(e.target.value) : e.target.value; if (id) onBindingsChange?.({ ...bindings, [id]: next }); else change({ ...node, count: next }); }} /></label><p>{id ? 'Count edits use this experiment’s binding; the parameter expression is retained.' : 'Only this ordered sequence is repeated.'} Count is total passes; zero omits these steps.</p></section>;
        }
        const bound = methodInputBinding(method, node, bindings);
        if (!bound.editable || bound.id && !onBindingsChange) return actionProperties?.(node, change);
        const projected = { ...node, inputs: bound.inputs };
        const edit = (next: MethodValue) => {
            // Structural lowering owns its children; retain the former parameter and binding.
            if (next.type !== node.type || next.action !== node.action) { change(next); return; }
            if (bound.id && onBindingsChange) { if (next.inputs !== bound.inputs) onBindingsChange({ ...bindings, [bound.id]: next.inputs }); change({ ...next, inputs: node.inputs }); }
            else change(next);
        };
        const editor = actionProperties?.(projected, edit) ?? (thermalMethodActions.has(String(node.action)) ? <BioXpMethodThermalEditor embedded existingTimer={!!start} node={projected} onChange={edit} catalog={catalog} compact expanded={expanded} onExpandedChange={setExpanded} /> : ['move', 'transfer', 'lower', 'lift', 'mix'].includes(String(node.action === 'native_intent' ? object(bound.inputs).operation : node.action)) ? <BioXpMethodPipettingEditor node={projected} onChange={edit} catalog={catalog} selection={selection} onSelect={setSelection} endpointLabels={Object.fromEntries((['source', 'destination'] as const).flatMap(key => { const name = plan.labware.find(l => l.id === object(object(bound.inputs)[key]).labware_id)?.name; return name ? [[key, name]] : []; }))} /> : custodyMethodActions.has(String(node.action)) ? <BioXpMethodCustodyEditor node={projected} onChange={edit} catalog={catalog} plan={plan} selection={selection} /> : undefined);
        const bindingSchema = bound.id ? methodBindingsSchema(method, catalog) : undefined;
        const content = editor ?? (bound.id ? <MethodFields label={bound.label || 'Step settings'} schema={bindingSchema?.properties?.[bound.id]} rootSchema={bindingSchema} value={bound.inputs} onChange={inputs => edit({ ...projected, inputs })} /> : undefined);
        return content ? <>{timing}{bound.id && <small className="bioxp-binding-note">{bound.inherited ? 'Inherited parameter default; editing creates a binding.' : 'Edits are saved in this experiment’s bindings.'}</small>}{content}{node.action === 'transfer' && <details><summary>Planned labware associations</summary><fieldset><legend>Named source & destination</legend>{['source', 'destination'].map(key => {
            const endpoint = object(object(bound.inputs)[key]), retained = Object.hasOwn(endpoint, 'labware_id') && !plan.labware.some(l => l.id === endpoint.labware_id);
            return <label key={key}>{key === 'source' ? 'Source labware' : 'Destination labware'}<select aria-label={key === 'source' ? 'Source labware' : 'Destination labware'} disabled={Object.hasOwn(object(bound.inputs), key) && (object(bound.inputs)[key] === null || typeof object(bound.inputs)[key] !== 'object' || Array.isArray(object(bound.inputs)[key]) || Object.hasOwn(endpoint, 'expr'))} value={retained ? '__retained' : String(endpoint.labware_id ?? '')} onChange={e => { if (e.target.value === '__retained') return; const next = { ...endpoint }; if (e.target.value) next.labware_id = e.target.value; else delete next.labware_id; edit({ ...projected, inputs: { ...object(bound.inputs), [key]: next } }); }}><option value="">Not associated</option>{retained && <option value="__retained">Retained identity / null / expression</option>}{plan.labware.map(l => <option key={l.id} value={l.id}>{l.name || l.id}</option>)}</select></label>;
        })}<small>Stable planned identities can follow a carried plate between stations; associations do not verify inventory or custody.</small></fieldset></details>}{node.action === 'transfer' && <details><summary>Depth & handling</summary><p>Ordinary Transfer lowers to calibrated depth. Move-position flags and post-stroke lift heights do not set aspiration depth or a lateral pellet offset; pellet avoidance is not automatic.</p></details>}</> : undefined;
    };
    const duplicate = (original: MethodValue) => {
        const duplicated = duplicateCanvasNode(method, original, bindings, !!onBindingsChange);
        const copied = duplicated.node;
        const insert = (rows: MethodValue[], path = '/steps'): MethodValue[] => rows.flatMap((node, index) => `${path}/${index}` === selectedPath ? [node, copied] : [{ ...node, ...Object.fromEntries(['steps', 'then', 'else'].filter(k => Array.isArray(node[k])).map(k => [k, insert(node[k] as MethodValue[], `${path}/${index}/${k}`)])) }]);
        onChange({ ...method, ...(duplicated.changed ? { parameters: duplicated.parameters } : {}), steps: insert(nodes) });
        if (duplicated.changed) onBindingsChange?.(duplicated.bindings);
        setSelectedId(String(copied.step_id));
    };
    const remove = (removed: MethodValue) => {
        const without = (rows: MethodValue[], path = '/steps'): MethodValue[] => rows.flatMap((node, index) => `${path}/${index}` === selectedPath ? [] : [{ ...node, ...Object.fromEntries(['steps', 'then', 'else'].filter(key => Array.isArray(node[key])).map(key => [key, without(node[key] as MethodValue[], `${path}/${index}/${key}`)])) }]);
        const next = removeMethodInputParameters({ ...method, steps: without(nodes) }, removed, bindings);
        onChange(next.method);
        if (next.changed) onBindingsChange?.(next.bindings);
        setSelectedId('');
    };
    const localPlate = plan.labware.find(l => l.id === plateId && plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station === selection.station) ?? (plan.labware.filter(l => plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station === selection.station).length === 1 ? plan.labware.find(l => plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station === selection.station) : undefined);
    const plateAddition = () => ({ ...(transferMode === 'class' ? { liquid: {}, recipe: {} } : {}), ...(endpointReady ? { destination: { station: station!.id, location_id: station!.locationId, wells: [...selection.wells], ...(localPlate ? { labware_id: localPlate.id } : {}) } } : localPlate ? { destination: plateBoundEndpoint({}, localPlate.id, plan, occurrenceState) } : {}) });
    const transferInputs = () => ({ ...(endpointReady ? { source: { station: station!.id, location_id: station!.locationId, wells: [...selection.wells] } } : {}), ...(transferMode === 'class' ? { liquid: {}, recipe: {} } : {}) });
    const allNodes = entries.map(e => e.node);
    const nodePath = (node: MethodValue) => entries.find(e => e.node === node)?.path ?? String(node.step_id);
    const carryLinks = methodCarryLinks(method, bindings, plan, catalog);
    const timers = entries.filter(e => e.node.action === 'timer_start').map(e => ({ ...e, inputs: object(methodInputBinding(method, e.node, bindings).inputs) })).filter(e => typeof e.inputs.timer_id === 'string' && e.inputs.timer_id !== '').filter((e, index, all) => all.findIndex(other => other.inputs.timer_id === e.inputs.timer_id) === index);
    const destinations = custodyMetadata(catalog).destinations.filter(d => d.plate_destination != null);
    const carry = (id: string, target: string) => {
        const destination = destinations.find(d => d.station === target);
        if (!destination) { setCarryNotice('No published plate placement for this station. Choose a listed destination or retain a native target in the placement editor.'); return; }
        const plate = object(plan.labware.find(l => l.id === id));
        append('plate_move', { ...(id ? { labware_id: id } : {}), ...(typeof plate.native_plate_id === 'string' && plate.native_plate_id !== '' ? { plate_id: plate.native_plate_id } : {}), target_location: destination.token }, 'Move plate');
        setCarryPlate(null); setCarryNotice('');
    };
    const preparePlate = (id: string, station: string) => { setWellContents(false); setPlateId(id); setSelection({ station, wells: [] }); setSetupOpen(true); setCarryPlate(null); setCarryNotice(''); openEditor(); };
    const chooseObject = (next: BioXpDeckSelection) => {
        if (carryPlate !== null) { carry(carryPlate, next.station); return; }
        setSelection(next);
        if (next.station !== selection.station) setPlateId('');
        if (connecting && next.wells.length) {
            if (!connectionSource) { setConnectionSource(next); return; }
            const endpoint = (s: BioXpDeckSelection) => {
                const location = deckStations.find(d => d.id === s.station);
                const labware = plan.labware.filter(l => plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station === s.station);
                return { station: s.station, location_id: location?.locationId, wells: [...s.wells], ...(labware.length === 1 ? { labware_id: labware[0].id } : {}) };
            };
            append('transfer', { ...transferInputs(), source: endpoint(connectionSource), destination: endpoint(next) }, 'Add reagent');
            setConnecting(false); setConnectionSource(null); return;
        }
        setEmptyInstrument('');
        if (next.wells.length) { setWellContents(true); setSetupOpen(true); openEditor(); return; }
        setWellContents(false);
        if (next.station === 'LOC_TC') {
            const programs = entries.filter(e => isThermalProgram(e.node)).filter(e => !entries.some(parent => e.path.startsWith(parent.path + '/') && isThermalProgram(parent.node)));
            const retained = programs.find(e => e.path === selectedPath || selectedPath.startsWith(e.path + '/'));
            if (programs.length) setSelectedId((retained ?? programs[0]).path);
            else setEmptyInstrument('LOC_TC');
            setSetupOpen(false); openEditor(); return;
        }
        const candidates = allNodes.filter(n => {
            const i = object(methodInputBinding(method, n, bindings).inputs);
            return next.station === 'LOC_TC' ? thermalMethodActions.has(String(n.action)) && n.action !== 'chiller_setpoint'
                : ['LOC_RC', 'LOC_OC'].includes(next.station) && !next.wells.length ? n.action === 'chiller_setpoint' && i.bank === (next.station === 'LOC_RC' ? 'rc' : 'oc')
                : n.action === 'transfer' && [i.source, i.destination].some(e => object(e).station === next.station);
        });
        if (candidates.length) { if (!candidates.some(n => nodePath(n) === selectedPath)) setSelectedId(nodePath(candidates[0])); setSetupOpen(false); }
        else if (['LOC_RC', 'LOC_OC'].includes(next.station)) { setEmptyInstrument(next.station); setSetupOpen(false); }
        else setSetupOpen(true);
        openEditor();
    };
    const additions = entries.filter(({ node }) => {
        const i = object(methodInputBinding(method, node, bindings).inputs);
        return node.action === 'transfer' && (localPlate && object(i.destination).labware_id ? object(i.destination).labware_id === localPlate.id : object(i.destination).station === selection.station);
    }).map(({ node, path }) => {
        const b = methodInputBinding(method, node, bindings), i = object(b.inputs), source = object(i.source), destination = object(i.destination);
        const sourceName = plan.labware.find(l => l.id === source.labware_id)?.name || deckStations.find(s => s.id === source.station)?.label || 'Choose source';
        const editable = b.editable && (!b.id || !!onBindingsChange);
        return { path, name: String(node.label || 'Add reagent'), source: `${sourceName}${Array.isArray(source.wells) ? ` · ${source.wells.join(', ')}` : ''}`, wells: Array.isArray(destination.wells) ? destination.wells as string[] : [], amount: i.volume_ul, settings: `${Array.isArray(i.channels) ? i.channels.join(', ') : 'Channels not set'} · ${i.liquid !== undefined ? 'Liquid class & recipe' : 'Manual speeds'}`, editable, wellsEditable: editable && (i.destination === undefined || i.destination !== null && typeof i.destination === 'object' && !Array.isArray(i.destination) && !destination.expr) };
    });
    const editAddition = (path: string, change: (inputs: MethodValue) => MethodValue) => {
        const n = entries.find(e => e.path === path)?.node;
        if (!n) return;
        const b = methodInputBinding(method, n, bindings);
        if (!b.editable || b.id && !onBindingsChange) return;
        const inputs = change(object(b.inputs));
        if (b.id) onBindingsChange?.({ ...bindings, [b.id]: inputs });
        else onChange({ ...method, steps: editCanvasNode(nodes, path, n => ({ ...n, inputs })) });
    };
    const links = allNodes.filter(n => n.action === 'transfer').map(n => {
        const i = object(methodInputBinding(method, n, bindings).inputs);
        const point = (endpoint: unknown) => {
            const e = object(endpoint), resource = deckResources.find(r => r.locationId != null && String(r.locationId) === String(e.location_id));
            return resource?.points.find(p => Array.isArray(e.wells) && p.well === e.wells[0]);
        };
        return { node: n, source: point(i.source), destination: point(i.destination) };
    });
    return <><div className="bioxp-method-view" role="group" aria-label="Authoring view"><button type="button" aria-pressed={!sequenceOnly} onClick={() => setSequenceOnly(false)}>Deck & sequence</button><button type="button" aria-pressed={sequenceOnly} onClick={() => setSequenceOnly(true)}>Sequence only</button></div><div className={`bioxp-method-workbench${sequenceOnly ? ' is-sequence-only' : ''}`} onClickCapture={e => { const origin = (e.target as Element).closest<HTMLElement>('[role="button"],button'); activationOrigin.current = origin && !sheetRef.current?.contains(origin) ? origin : null; }}>
        <section aria-label="Method deck" className="bioxp-method-map" hidden={sequenceOnly}>
            <div className="bioxp-canvasbar"><strong>Deck & method</strong><span>Click an object to edit · drag a plate to carry it · right-click for actions</span><button type="button" aria-pressed={connecting} onClick={() => { setConnecting(!connecting); setConnectionSource(null); setEditorOpen(false); }}>{connecting ? 'Cancel connection' : 'Connect wells'}</button>{connecting && <p role="status">{connectionSource ? 'Choose destination wells. The new connection is inserted at the selected position.' : 'Choose source wells, then destination wells. No action is run.'}</p>}<button type="button" aria-expanded={setupOpen} onClick={() => { setSetupOpen(true); openEditor(); }}>Set up labware & reagents</button></div>
            <div className={`bioxp-method-canvas${connecting ? ' is-connecting' : ''}`} onContextMenu={e => { const target = (e.target as Element).closest('[data-station]'); if (target) { e.preventDefault(); chooseObject({ station: target.getAttribute('data-station')!, wells: target.getAttribute('data-well') ? [target.getAttribute('data-well')!] : [] }); } }}>
            <BioXpWorkflowDeck compact canvas selection={selection} onChange={chooseObject} overlay={<g aria-label="Ordered liquid connections">{links.map(({ node, source, destination }, index) => source && destination && <g key={nodePath(node)} role="button" tabIndex={0} aria-label={`Edit connection ${String(node.label || node.step_id)}`} className={`bioxp-method-link${selectedPath === nodePath(node) ? ' is-selected' : ''}`} onClick={() => selectStep(nodePath(node))} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectStep(nodePath(node)); } }}>
                <path d={`M ${source.x} ${source.y} Q ${(source.x + destination.x) / 2} ${Math.min(source.y, destination.y) - 60 - index % 4 * 18} ${destination.x} ${destination.y}`} />
                <title>{String(node.label || 'Add reagent')} — ordered action, not concurrent flow</title>
            </g>)}<BioXpMethodPlateLayer plan={plan} stations={Object.fromEntries(plan.labware.map(l => [l.id, String(plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station ?? '')]))} selectedPlate={plateId} selectedPath={selectedPath} links={carryLinks} onPrepare={preparePlate} onCarry={carry} onSelect={selectStep} /></g>} />
            <section ref={sheetRef} className={`bioxp-method-local-editor${expanded ? ' is-expanded' : ''}${setupOpen ? ' is-setup' : ''}${action === 'transfer' ? ' is-transfer' : ''}`} aria-label="On-deck editor" hidden={!editorOpen} onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); closeEditor(); } }}>
                <header><div className="bioxp-editor-heading"><h2>{!setupOpen && (emptyInstrument === 'LOC_TC' || !emptyInstrument && selected && isThermalProgram(selected)) ? 'Thermal cycler' : setupOpen ? station?.label || 'Labware & reagents' : String(selected?.label || selected?.action || 'Selected step').replaceAll('_', ' ')}</h2><p>{setupOpen ? 'Choose a plate, its contents and next action' : action === 'plate_move' ? 'Same plate · next location' : action === 'transfer' ? 'Source → destination · exact amounts' : 'Edit this step in your method'}</p></div><button type="button" aria-pressed={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Compact editor' : 'Expand editor'}</button><button className="bioxp-editor-close" type="button" aria-label="Close editor" onClick={closeEditor}>×</button></header><div className="bioxp-editor-body">
                <button className="bioxp-editor-switch" type="button" aria-pressed={setupOpen} onClick={() => setSetupOpen(!setupOpen)}>{setupOpen ? 'Step settings' : 'Plate, tips & contents'}</button>
                {setupOpen && !wellContents && localPlate && <BioXpMethodPreparation name={localPlate.name} station={selection.station} additions={additions} selected={selectedPath} onSelect={setSelectedId} onEdit={selectStep} onAdd={() => append('transfer', plateAddition(), 'Add reagent')} onMove={() => { setCarryPlate(localPlate.id); setCarryNotice(''); }} onAmount={(path, raw) => editAddition(path, i => ({ ...i, volume_ul: isMethodNumber(i.volume_ul) ? methodNumber(raw) : raw }))} onWells={(path, wells) => { setSelectedId(path); setSelection({ station: selection.station, wells }); editAddition(path, i => ({ ...i, destination: { ...object(i.destination), wells } })); }} />}
                <div hidden={!setupOpen}><details className="bioxp-plate-setup-details" open={!localPlate || selection.wells.length > 0}><summary>Plate, profile & starting contents</summary><BioXpWorkflowMaterials displayStations={Object.fromEntries(plan.labware.map(l => [l.id, String(plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station ?? '')]))} selectedLabwareId={plateId} contextual methodAuthoring plan={plan} selection={selection} profiles={Array.isArray(dependencies.labware_profiles) ? dependencies.labware_profiles as MethodValue[] : []} onChange={deck_plan => onChange({ ...method, deck_plan })} /></details></div>
                {setupOpen && selection.wells.length > 0 && <section aria-label="Actions touching selected well">{entries.filter(e => { const i = object(methodInputBinding(method, e.node, bindings).inputs); return e.node.action === 'transfer' && [i.source, i.destination].some(value => { const endpoint = object(value); return endpoint.station === selection.station && (!plateId || !endpoint.labware_id || endpoint.labware_id === plateId) && Array.isArray(endpoint.wells) && endpoint.wells.some(w => selection.wells.includes(String(w))); }); }).map(e => <button key={e.path} type="button" onClick={() => selectStep(e.path)}>Edit {String(e.node.label || e.node.step_id)} · {e.path}</button>)}</section>}
                {!setupOpen && emptyInstrument && <BioXpMethodThermalEditor embedded node={{ type: 'action', step_id: 'uncommitted-instrument', action: emptyInstrument === 'LOC_TC' ? 'thermal_profile' : 'chiller_setpoint', inputs: emptyInstrument === 'LOC_TC' ? { segments: [] } : { bank: emptyInstrument === 'LOC_RC' ? 'rc' : 'oc' } }} catalog={catalog} compact expanded={expanded} onExpandedChange={setExpanded} onChange={next => { const step_id = crypto.randomUUID(); onChange({ ...method, steps: [...nodes, { ...next, step_id }] }); setEmptyInstrument(''); setSelectedId(`/steps/${nodes.length}`); }} />}
                {!setupOpen && selection.station === 'LOC_TC' && <label>Temperature program<select aria-label="Temperature program" value={selectedPath} onChange={e => { selectStep(e.target.value); onChange({ ...method, editor_state: { ...object(method.editor_state), instrument_path: e.target.value } }); }}>{entries.filter(e => isThermalProgram(e.node) && !entries.some(parent => e.path.startsWith(parent.path + '/') && isThermalProgram(parent.node))).map(e => <option key={e.path} value={e.path}>{String(e.node.label || e.node.step_id)} · {e.path}</option>)}</select></label>}
                {!setupOpen && ['LOC_RC', 'LOC_OC'].includes(selection.station) && <label>Chiller task<select aria-label="Chiller task" value={selectedPath} onChange={e => selectStep(e.target.value)}>{entries.filter(e => e.node.action === 'chiller_setpoint' && object(methodInputBinding(method, e.node, bindings).inputs).bank === (selection.station === 'LOC_RC' ? 'rc' : 'oc')).map(e => <option key={e.path} value={e.path}>{String(e.node.label || e.node.step_id)} · {e.path}</option>)}</select></label>}
                <div ref={setEditorHost} hidden={setupOpen || !!emptyInstrument} />
                {!setupOpen && !emptyInstrument && selected && isThermalProgram(selected) && <details><summary>Program name, order & behavior</summary><label>Program name<input aria-label="Temperature program name" value={String(selected.label ?? '')} onChange={e => onChange({ ...method, steps: editCanvasNode(nodes, selectedPath, n => ({ ...n, label: e.target.value })) })} /></label><button type="button" onClick={() => duplicate(selected)}>Duplicate program</button><button type="button" onClick={() => remove(selected)}>Remove program</button><small>Reorder the sequence with drag or Alt+Arrow keys. Full retained behavior is available in Advanced.</small></details>}
                {setupOpen && <div className="bioxp-method-object-actions"><button type="button" onClick={() => append('transfer', plateAddition(), 'Add reagent')}>Add reagent</button>{station?.id === 'LOC_TC' && <button type="button" onClick={() => append('thermal_profile', { segments: [] }, 'Temperature program')}>Temperature program</button>}{['LOC_RC', 'LOC_OC'].includes(station?.id ?? '') && <button type="button" onClick={() => append('chiller_setpoint', { bank: station?.id === 'LOC_RC' ? 'rc' : 'oc' })}>Temperature & timing</button>}<button type="button" aria-expanded={carryPlate !== null} onClick={() => { setCarryPlate(localPlate?.id ?? ''); setCarryNotice(''); }}>Move plate</button></div>}
                {(setupOpen || action === 'transfer' || action === 'plate_move') && <section aria-label="Planned plate endpoint"><label>Endpoint placement context<select aria-label="Endpoint placement context" value={occurrence} onChange={e => setOccurrence(e.target.value)}><option value="">Starting assignments</option>{(preview?.provenance ?? []).map(p => <option key={String(p.occurrence_id)} value={String(p.occurrence_id)}>After {String(p.step_id)} · {String(p.occurrence_id)}</option>)}</select></label>
                <small>{occurrence ? 'Planned after the selected compiled occurrence, not observed custody.' : 'Starting placement; Preview exposes occurrence-specific placement.'} Retained targets change only when explicitly updated.</small>
                {action === 'transfer' && !boundInputs && ['source', 'destination'].map(key => {
                    const endpoint = object(inputs[key]), next = plateBoundEndpoint(inputs[key], String(endpoint.labware_id ?? ''), plan, occurrenceState);
                    return next && (next.location_id !== endpoint.location_id || next.station !== endpoint.station) ? <button type="button" key={key} onClick={() => updateInputs({ ...inputs, [key]: next })}>Update {key} to planned plate location</button> : null;
                })}
                </section>}
                {carryPlate !== null && <section aria-label="Choose plate destination"><h4>Move {plan.labware.find(l => l.id === carryPlate)?.name || 'plate'} to…</h4>{destinations.map(d => <button key={String(d.token)} type="button" onClick={() => carry(carryPlate, String(d.station))}>Move to {String(d.label)}</button>)}<button type="button" onClick={() => { setCarryPlate(null); setCarryNotice(''); }}>Cancel plate move</button><p>Or select a destination on the deck. Adds one ordered custody step only; choose native object and handling details in its editor.</p></section>}
            </div><footer className="bioxp-editor-footer"><span>{localPlate?.name || String(method.name || 'Method draft')}</span><button type="button" className="primary" onClick={closeEditor}>Done</button></footer></section></div>
            {carryNotice && <p role="status">{carryNotice}</p>}
            <p className="bioxp-method-link-key"><span>Solid: liquid transfer</span> · <span>Dashed arrow: plate carry</span> · Ordered method, not live movement.</p>
            {carryLinks.some(l => !l.source || !l.destination) && <details><summary>Carry steps with unresolved endpoints</summary>{carryLinks.filter(l => !l.source || !l.destination).map(l => <p key={l.path}><button type="button" onClick={() => selectStep(l.path)}>{String(l.node.label || l.node.step_id)}</button> {l.source || 'Unknown source'} → {l.destination || 'Unknown destination'}. Use compiled occurrence preview for conditional or repeated placement.</p>)}</details>}


        </section>
        <aside aria-label="Method sequence and Properties" className="bioxp-method-inspector">
            <details className="bioxp-workbench-tools" open={toolsOpen} onToggle={e => setToolsOpen(e.currentTarget.open)}><summary>＋ Add next action</summary><div className="bioxp-tools-popover">
                <header><h4>Add an ordered action</h4><label>Insert step<select aria-label="Insert step" value={insertion} onChange={e => setInsertion(e.target.value)}><option value="before">Before selected step</option><option value="after">After selected step</option><option value="end">At end</option></select></label></header>
            <section aria-label="Reagent additions" className="bioxp-tools-group"><h5>Reagent / aliquot</h5><label>Addition name<input aria-label="Addition name" value={additionName} onChange={e => setAdditionName(e.target.value)} placeholder="e.g. Primer mix" /></label><button type="button" disabled={!hasAction('transfer')} onClick={() => { append('transfer', transferInputs(), additionName || 'Reagent addition'); setAdditionName(''); }}>Add reagent / aliquot</button><small>Choose source, reaction wells and volume in Properties. Each addition has its own settings.</small></section>
            <section aria-label="Cycling setup" className="bioxp-tools-group"><h5>Temperature program or hold</h5><button type="button" disabled={!hasAction('thermal_profile')} onClick={() => append('thermal_profile', { segments: [] }, 'Temperature program')}>Add repeated cycles</button><button type="button" disabled={!hasAction('thermal_hold')} onClick={() => append('thermal_hold', {}, 'Initial / final hold')}>Insert initial / final hold</button><small>Initial and final holds are separate steps, not repeated stages. Use Insert step to place them.</small></section>
                <section className="bioxp-tools-group" aria-label="Deck actions"><h5>Deck action</h5><button type="button" disabled={!moveReady || !hasAction('move')} onClick={() => append('move', { location_id: station!.locationId, well: selection.wells[0] })}>Add Move</button>
                <label>Transfer settings<select aria-label="New Transfer settings" value={transferMode} onChange={e => setTransferMode(e.target.value)}><option value="class">Liquid class & recipe</option><option value="manual">Manual speeds</option></select></label>
                <button type="button" disabled={!hasAction('transfer')} onClick={() => append('transfer', transferInputs())}>Add Transfer</button>
                {(station?.id === 'LOC_RC' || station?.id === 'LOC_OC') && <button type="button" disabled={!hasAction('chiller_setpoint')} onClick={() => append('chiller_setpoint', { bank: station.id === 'LOC_RC' ? 'rc' : 'oc' })}>Add temperature step</button>}
                {station?.id === 'LOC_TC' && <><button type="button" disabled={!hasAction('thermal_setpoint')} onClick={() => append('thermal_setpoint', {})}>Add temperature step</button><button type="button" disabled={!hasAction('thermal_hold')} onClick={() => append('thermal_hold', {})}>Add hold</button><button type="button" disabled={!hasAction('thermal_profile')} onClick={() => append('thermal_profile', { segments: [] })}>Add temperature program</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DO' })}>Add door Open</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DC' })}>Add door Close</button><small>Door Open also initializes pipettes when run.</small></>}
                </section>
                <section className="bioxp-tools-group" aria-label="Placement and operator stages"><h5>Placement or operator stage</h5>{[['plate_move', 'Add plate placement'], ['move_cover', 'Add cover placement'], ['plate_prepare', 'Add plate preparation'], ['plate_catch', 'Add plate pickup'], ['plate_release', 'Add plate release'], ['plate_press', 'Add plate press'], ['wait', 'Add timed wait'], ['checkpoint', 'Add operator checkpoint'], ['note', 'Add note']].map(([action, label]) => <button type="button" key={action} disabled={!hasAction(action)} onClick={() => append(action, {}, label.replace('Add ', ''))}>{label}</button>)}<small>Placement carries physical labware. Add Move positions the pipette head only. External work remains an explicit checkpoint.</small></section>
                {timers.length > 0 && <fieldset className="bioxp-method-later-wait"><legend>Wait for an earlier timer</legend><label>Elapsed timer<select aria-label="Elapsed timer to wait for" value={waitTimer} onChange={e => setWaitTimer(e.target.value)}><option value="">Choose timer…</option>{timers.map(t => <option key={String(t.inputs.timer_id)} value={String(t.inputs.timer_id)}>{String(t.inputs.timer_id)} · {String(t.node.label || 'Elapsed conditioning')}</option>)}</select></label><button type="button" disabled={!timers.some(t => t.inputs.timer_id === waitTimer)} onClick={() => { const timer = timers.find(t => t.inputs.timer_id === waitTimer)!; const added = thermalTimerWait(String(timer.inputs.timer_id)); onChange({ ...method, steps: insertMethodAction(nodes, added, selectedPath, insertion) }); selectStep(String(added.step_id)); }}>Insert timer wait</button><small>Uses the selected insertion position and waits only for this timer’s remaining time. No automatic target reset.</small></fieldset>}
                <div className="bioxp-tools-generic" ref={setAddHost} />
                <section className="bioxp-method-adopt" aria-label="Use deck selection"><h4>Deck selection</h4>
                <p>{station?.label ?? 'Select a station or well'}{selection.wells.length ? ` · ${selection.wells.join(', ')}` : ''}</p>
                {boundInputs && <p>Retained expression or non-object input: edit Advanced action fields or Bindings. The original value is unchanged.</p>}
                {['plate_move', 'move_cover'].includes(String(action)) && !boundInputs && <button type="button" disabled={!custodyMetadata(catalog).destinations.some(d => d.station === selection.station && d[action === 'move_cover' ? 'cover_destination' : 'plate_destination'] != null)} onClick={() => updateInputs({ ...inputs, target_location: custodyMetadata(catalog).destinations.find(d => d.station === selection.station && d[action === 'move_cover' ? 'cover_destination' : 'plate_destination'] != null)!.token })}>Use as plate / cover destination</button>}
                {action === 'move' && !boundInputs && <button type="button" disabled={!moveReady} onClick={() => updateInputs({ ...inputs, location_id: station!.locationId, well: selection.wells[0] })}>Use as Move target</button>}
                {action === 'transfer' && !boundInputs && <><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('source')}>Use as source</button><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('destination')}>Use as destination</button><p>Source: {endpointText('source')}<br />Destination: {endpointText('destination')}</p></>}
                <button type="button" onClick={() => { setSetupOpen(false); openEditor(); }}>Edit selected step</button>


                </section>
            </div></details>
            {allNodes.some(n => n.type === 'group' || n.type === 'repeat') && <details className="bioxp-method-process-stages"><summary>Process stages & generated children</summary><ol>{allNodes.map((n, index) => <li key={`${index}:${String(n.step_id)}`}><button type="button" onClick={() => selectStep(nodePath(n))}>{String(n.label || n.action || n.type)}{n.type === 'repeat' ? ' · repeated group' : ''}{n.enabled === false ? ' · disabled' : ''}</button></li>)}</ol><small>Source-owned ordered stages. Repeats enclose their authored children; external work remains an operator checkpoint.</small></details>}
            <MethodOutline addHost={sequenceOnly ? undefined : addHost} onInsertAfter={sequenceOnly ? undefined : path => { setSelectedId(path); setInsertion('after'); setToolsOpen(true); }} editorHost={sequenceOnly ? undefined : editorHost} onDuplicate={duplicate} onRemove={remove} actionProperties={properties} nodes={nodes} onChange={steps => { const moved = methodCanvasEntries(steps).find(e => e.node === selected)?.path; onChange({ ...method, steps, ...(object(method.editor_state).instrument_path === selectedPath && moved && moved !== selectedPath ? { editor_state: { ...object(method.editor_state), instrument_path: moved } } : {}) }); }} catalog={catalog} rootSchema={rootSchema} nodeSchema={rootSchema.properties?.steps?.items} procedures={Array.isArray(method.procedures) ? method.procedures as MethodValue[] : []} findings={findings} flat={flat} selection={{ id: selectedPath, onSelect: selectStep }} />
        </aside>
    </div></>;
}
