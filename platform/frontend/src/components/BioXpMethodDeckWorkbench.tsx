import { useRef, useState } from 'react';
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
    const [selectedId, setSelectedId] = useState('');
    const [transferMode, setTransferMode] = useState('class');
    const [sequenceOnly, setSequenceOnly] = useState(false);
    const [setupOpen, setSetupOpen] = useState(false);
    const [plateId, setPlateId] = useState('');
    const [carryPlate, setCarryPlate] = useState<string | null>(null);
    const [carryNotice, setCarryNotice] = useState('');
    const [editorOpen, setEditorOpen] = useState(false);
    const [expanded, setExpanded] = useState(false);
    const [editorHost, setEditorHost] = useState<HTMLDivElement | null>(null);
    const returnFocus = useRef<HTMLElement | SVGElement | null>(null);
    const [occurrence, setOccurrence] = useState('');
    const [waitTimer, setWaitTimer] = useState('');
    const [connecting, setConnecting] = useState(false);
    const [connectionSource, setConnectionSource] = useState<BioXpDeckSelection | null>(null);
    const occurrenceState = occurrence ? methodStateAfter(preview?.simulation, initialState, occurrence) : undefined;
    const openEditor = () => { returnFocus.current = document.activeElement as HTMLElement; setEditorOpen(true); };
    const closeEditor = () => { setEditorOpen(false); returnFocus.current?.focus(); };
    const selectStep = (id: string) => { setSelectedId(id); setSetupOpen(false); openEditor(); };
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
    const properties = (node: MethodValue, change: (next: MethodValue) => void) => {
        if (node.type === 'group' && isThermalProgram(node)) return <BioXpMethodThermalEditor node={node} onChange={change} catalog={catalog} compact />;
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
        const editor = actionProperties?.(projected, edit) ?? (thermalMethodActions.has(String(node.action)) ? <BioXpMethodThermalEditor node={projected} onChange={edit} catalog={catalog} compact /> : ['move', 'transfer', 'lower', 'lift', 'mix'].includes(String(node.action === 'native_intent' ? object(bound.inputs).operation : node.action)) ? <BioXpMethodPipettingEditor node={projected} onChange={edit} catalog={catalog} selection={selection} onSelect={setSelection} /> : custodyMethodActions.has(String(node.action)) ? <BioXpMethodCustodyEditor node={projected} onChange={edit} catalog={catalog} plan={plan} selection={selection} /> : undefined);
        const bindingSchema = bound.id ? methodBindingsSchema(method, catalog) : undefined;
        const content = editor ?? (bound.id ? <MethodFields label={bound.label || 'Step settings'} schema={bindingSchema?.properties?.[bound.id]} rootSchema={bindingSchema} value={bound.inputs} onChange={inputs => edit({ ...projected, inputs })} /> : undefined);
        return content ? <>{bound.id && <small>{bound.inherited ? 'Inherited parameter default; editing creates a binding.' : 'Edits are saved in this experiment’s bindings.'}</small>}{content}{node.action === 'transfer' && <details><summary>Planned labware associations</summary><fieldset><legend>Named source & destination</legend>{['source', 'destination'].map(key => {
            const endpoint = object(object(bound.inputs)[key]), retained = Object.hasOwn(endpoint, 'labware_id') && !plan.labware.some(l => l.id === endpoint.labware_id);
            return <label key={key}>{key === 'source' ? 'Source labware' : 'Destination labware'}<select aria-label={key === 'source' ? 'Source labware' : 'Destination labware'} disabled={Object.hasOwn(object(bound.inputs), key) && (object(bound.inputs)[key] === null || typeof object(bound.inputs)[key] !== 'object' || Array.isArray(object(bound.inputs)[key]) || Object.hasOwn(endpoint, 'expr'))} value={retained ? '__retained' : String(endpoint.labware_id ?? '')} onChange={e => { if (e.target.value === '__retained') return; const next = { ...endpoint }; if (e.target.value) next.labware_id = e.target.value; else delete next.labware_id; edit({ ...projected, inputs: { ...object(bound.inputs), [key]: next } }); }}><option value="">Not associated</option>{retained && <option value="__retained">Retained identity / null / expression</option>}{plan.labware.map(l => <option key={l.id} value={l.id}>{l.name || l.id}</option>)}</select></label>;
        })}<small>Stable planned identities can follow a carried plate between stations; associations do not verify inventory or custody.</small></fieldset></details>}{node.action === 'transfer' && <small>Ordinary Transfer lowers to calibrated depth. Move-position flags and post-stroke lift heights do not set aspiration depth or a lateral pellet offset; pellet avoidance is not automatic.</small>}</> : undefined;
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
    const preparePlate = (id: string, station: string) => { setPlateId(id); setSelection({ station, wells: [] }); setSetupOpen(true); setCarryPlate(null); setCarryNotice(''); openEditor(); };
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
        // Picking wells while editing a transfer changes selection, never the saved endpoint.
        if (editorOpen && (setupOpen || next.wells.length)) return;
        const candidates = allNodes.filter(n => {
            const i = object(methodInputBinding(method, n, bindings).inputs);
            return next.station === 'LOC_TC' ? thermalMethodActions.has(String(n.action)) && n.action !== 'chiller_setpoint'
                : ['LOC_RC', 'LOC_OC'].includes(next.station) && !next.wells.length ? n.action === 'chiller_setpoint' && i.bank === (next.station === 'LOC_RC' ? 'rc' : 'oc')
                : n.action === 'transfer' && [i.source, i.destination].some(e => object(e).station === next.station);
        });
        if (candidates.length) { if (!candidates.some(n => n.step_id === selected?.step_id)) setSelectedId(nodePath(candidates[0])); setSetupOpen(false); }
        else setSetupOpen(true);
        openEditor();
    };
    const links = allNodes.filter(n => n.action === 'transfer').map(n => {
        const i = object(methodInputBinding(method, n, bindings).inputs);
        const point = (endpoint: unknown) => {
            const e = object(endpoint), resource = deckResources.find(r => r.locationId != null && String(r.locationId) === String(e.location_id));
            return resource?.points.find(p => Array.isArray(e.wells) && p.well === e.wells[0]);
        };
        return { node: n, source: point(i.source), destination: point(i.destination) };
    });
    return <><div className="bioxp-method-view" role="group" aria-label="Authoring view"><button type="button" aria-pressed={!sequenceOnly} onClick={() => setSequenceOnly(false)}>Deck & sequence</button><button type="button" aria-pressed={sequenceOnly} onClick={() => setSequenceOnly(true)}>Sequence only</button></div><div className={`bioxp-method-workbench${sequenceOnly ? ' is-sequence-only' : ''}`}>
        <section aria-label="Method deck" className="bioxp-method-map" hidden={sequenceOnly}>
            <h3>Experiment deck</h3><button type="button" aria-pressed={connecting} onClick={() => { setConnecting(!connecting); setConnectionSource(null); setEditorOpen(false); }}>{connecting ? 'Cancel connection' : 'Connect wells'}</button>{connecting && <p role="status">{connectionSource ? 'Choose destination wells. The new connection is inserted at the selected position.' : 'Choose source wells, then destination wells. No action is run.'}</p>}<button type="button" aria-expanded={setupOpen} onClick={() => { setSetupOpen(true); openEditor(); }}>Set up labware & reagents</button>
            <div className={`bioxp-method-canvas${connecting ? ' is-connecting' : ''}`} onContextMenu={e => { const target = (e.target as Element).closest('[data-station]'); if (target) { e.preventDefault(); chooseObject({ station: target.getAttribute('data-station')!, wells: target.getAttribute('data-well') ? [target.getAttribute('data-well')!] : [] }); } }}>
            <BioXpWorkflowDeck compact selection={selection} onChange={chooseObject} overlay={<g aria-label="Ordered liquid connections">{links.map(({ node, source, destination }, index) => source && destination && <g key={nodePath(node)} role="button" tabIndex={0} aria-label={`Edit connection ${String(node.label || node.step_id)}`} className={`bioxp-method-link${selectedPath === nodePath(node) ? ' is-selected' : ''}`} onClick={() => selectStep(nodePath(node))} onKeyDown={e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); selectStep(nodePath(node)); } }}>
                <path d={`M ${source.x} ${source.y} Q ${(source.x + destination.x) / 2} ${Math.min(source.y, destination.y) - 60 - index % 4 * 18} ${destination.x} ${destination.y}`} />
                <title>{String(node.label || 'Add reagent')} — ordered action, not concurrent flow</title>
            </g>)}<BioXpMethodPlateLayer plan={plan} stations={Object.fromEntries(plan.labware.map(l => [l.id, String(plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station ?? '')]))} selectedPlate={plateId} selectedPath={selectedPath} links={carryLinks} onPrepare={preparePlate} onCarry={carry} onSelect={selectStep} /></g>} />
            <section className={`bioxp-method-local-editor${expanded ? ' is-expanded' : ''}`} aria-label="On-deck editor" hidden={!editorOpen} onKeyDown={e => { if (e.key === 'Escape') { e.stopPropagation(); closeEditor(); } }}>
                <header><strong>{setupOpen ? station?.label || 'Labware & reagents' : String(selected?.label || selected?.action || 'Selected step').replaceAll('_', ' ')}</strong><button type="button" aria-pressed={expanded} onClick={() => setExpanded(!expanded)}>{expanded ? 'Compact editor' : 'Expand editor'}</button><button type="button" onClick={closeEditor}>Close editor</button></header>
                <button type="button" aria-pressed={setupOpen} onClick={() => setSetupOpen(!setupOpen)}>{setupOpen ? 'Step settings' : 'Plate, tips & contents'}</button>
                {setupOpen && <section aria-label="Prepare selected plate"><h4>Prepare this plate</h4><ol>{allNodes.filter(n => { const i = object(methodInputBinding(method, n, bindings).inputs); return n.action === 'transfer' && (localPlate && object(i.destination).labware_id ? object(i.destination).labware_id === localPlate.id : object(i.destination).station === selection.station); }).map(n => { const i = object(methodInputBinding(method, n, bindings).inputs); return <li key={nodePath(n)}><button type="button" onClick={() => selectStep(nodePath(n))}>{String(n.label || 'Add reagent')} · {String(object(i.source).station || 'Choose source')} → {Array.isArray(object(i.destination).wells) ? (object(i.destination).wells as string[]).join(', ') : 'Choose wells'} · {typeof i.volume_ul === 'string' || typeof i.volume_ul === 'number' ? String(i.volume_ul) : 'Unspecified'} µL / channel</button></li>; })}</ol></section>}
                <div hidden={!setupOpen}><BioXpWorkflowMaterials displayStations={Object.fromEntries(plan.labware.map(l => [l.id, String(plateBoundEndpoint({}, l.id, plan, occurrenceState)?.station ?? '')]))} selectedLabwareId={plateId} contextual methodAuthoring plan={plan} selection={selection} profiles={Array.isArray(dependencies.labware_profiles) ? dependencies.labware_profiles as MethodValue[] : []} onChange={deck_plan => onChange({ ...method, deck_plan })} /></div>
                <div ref={setEditorHost} hidden={setupOpen} />
                {setupOpen && <div className="bioxp-method-object-actions"><button type="button" onClick={() => append('transfer', plateAddition(), 'Add reagent')}>Add reagent</button>{station?.id === 'LOC_TC' && <button type="button" onClick={() => append('thermal_profile', { segments: [] }, 'Temperature program')}>Temperature program</button>}{['LOC_RC', 'LOC_OC'].includes(station?.id ?? '') && <button type="button" onClick={() => append('chiller_setpoint', { bank: station?.id === 'LOC_RC' ? 'rc' : 'oc' })}>Temperature & timing</button>}<button type="button" aria-expanded={carryPlate !== null} onClick={() => { setCarryPlate(localPlate?.id ?? ''); setCarryNotice(''); }}>Move plate</button></div>}
                {carryPlate !== null && <section aria-label="Choose plate destination"><h4>Move {plan.labware.find(l => l.id === carryPlate)?.name || 'plate'} to…</h4>{destinations.map(d => <button key={String(d.token)} type="button" onClick={() => carry(carryPlate, String(d.station))}>Move to {String(d.label)}</button>)}<button type="button" onClick={() => { setCarryPlate(null); setCarryNotice(''); }}>Cancel plate move</button><p>Or select a destination on the deck. Adds one ordered custody step only; choose native object and handling details in its editor.</p></section>}
            </section></div>
            {carryNotice && <p role="status">{carryNotice}</p>}
            <p className="bioxp-method-link-key"><span>Solid: liquid transfer</span> · <span>Dashed arrow: plate carry</span> · Sequence numbers give execution order. Drag a named plate to add a carry step, or open Move plate.</p>
            {carryLinks.some(l => !l.source || !l.destination) && <details><summary>Carry steps with unresolved endpoints</summary>{carryLinks.filter(l => !l.source || !l.destination).map(l => <p key={l.path}><button type="button" onClick={() => selectStep(l.path)}>{String(l.node.label || l.node.step_id)}</button> {l.source || 'Unknown source'} → {l.destination || 'Unknown destination'}. Use compiled occurrence preview for conditional or repeated placement.</p>)}</details>}
            <div className="bioxp-method-adopt" aria-label="Use deck selection">
                <p>{station?.label ?? 'Select a station or well'}{selection.wells.length ? ` · ${selection.wells.join(', ')}` : ''}</p>
                <label>Insert step<select aria-label="Insert step" value={insertion} onChange={e => setInsertion(e.target.value)}><option value="before">Before selected step</option><option value="after">After selected step</option><option value="end">At end</option></select></label>
                {timers.length > 0 && <fieldset className="bioxp-method-later-wait"><legend>Wait for an earlier timer</legend><label>Elapsed timer<select aria-label="Elapsed timer to wait for" value={waitTimer} onChange={e => setWaitTimer(e.target.value)}><option value="">Choose timer…</option>{timers.map(t => <option key={String(t.inputs.timer_id)} value={String(t.inputs.timer_id)}>{String(t.inputs.timer_id)} · {String(t.node.label || 'Elapsed conditioning')}</option>)}</select></label><button type="button" disabled={!timers.some(t => t.inputs.timer_id === waitTimer)} onClick={() => { const timer = timers.find(t => t.inputs.timer_id === waitTimer)!; const added = thermalTimerWait(String(timer.inputs.timer_id)); onChange({ ...method, steps: insertMethodAction(nodes, added, selectedPath, insertion) }); selectStep(String(added.step_id)); }}>Insert timer wait</button><small>Uses the selected insertion position and waits only for this timer’s remaining time. No automatic target reset.</small></fieldset>}
                <details><summary>Add deck action</summary><button type="button" disabled={!moveReady || !hasAction('move')} onClick={() => append('move', { location_id: station!.locationId, well: selection.wells[0] })}>Add Move</button>
                <label>Transfer settings<select aria-label="New Transfer settings" value={transferMode} onChange={e => setTransferMode(e.target.value)}><option value="class">Liquid class & recipe</option><option value="manual">Manual speeds</option></select></label>
                <button type="button" disabled={!hasAction('transfer')} onClick={() => append('transfer', transferInputs())}>Add Transfer</button>
                {(station?.id === 'LOC_RC' || station?.id === 'LOC_OC') && <button type="button" disabled={!hasAction('chiller_setpoint')} onClick={() => append('chiller_setpoint', { bank: station.id === 'LOC_RC' ? 'rc' : 'oc' })}>Add temperature step</button>}
                {station?.id === 'LOC_TC' && <><button type="button" disabled={!hasAction('thermal_setpoint')} onClick={() => append('thermal_setpoint', {})}>Add temperature step</button><button type="button" disabled={!hasAction('thermal_hold')} onClick={() => append('thermal_hold', {})}>Add hold</button><button type="button" disabled={!hasAction('thermal_profile')} onClick={() => append('thermal_profile', { segments: [] })}>Add temperature program</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DO' })}>Add door Open</button><button type="button" disabled={!hasAction('thermal_door')} onClick={() => append('thermal_door', { door_command: 'DC' })}>Add door Close</button><small>Door Open also initializes pipettes when run.</small></>}
                </details>
                <details><summary>Add placement or operator stage</summary>{[['plate_move', 'Add plate placement'], ['move_cover', 'Add cover placement'], ['plate_prepare', 'Add plate preparation'], ['plate_catch', 'Add plate pickup'], ['plate_release', 'Add plate release'], ['plate_press', 'Add plate press'], ['wait', 'Add timed wait'], ['checkpoint', 'Add operator checkpoint'], ['note', 'Add note']].map(([action, label]) => <button type="button" key={action} disabled={!hasAction(action)} onClick={() => append(action, {}, label.replace('Add ', ''))}>{label}</button>)}<small>Placement carries physical labware. Add Move positions the pipette head only. External work remains an explicit checkpoint.</small></details>
                {boundInputs && <p>Retained expression or non-object input: edit Advanced action fields or Bindings. The original value is unchanged.</p>}
                {['plate_move', 'move_cover'].includes(String(action)) && !boundInputs && <button type="button" disabled={!custodyMetadata(catalog).destinations.some(d => d.station === selection.station && d[action === 'move_cover' ? 'cover_destination' : 'plate_destination'] != null)} onClick={() => updateInputs({ ...inputs, target_location: custodyMetadata(catalog).destinations.find(d => d.station === selection.station && d[action === 'move_cover' ? 'cover_destination' : 'plate_destination'] != null)!.token })}>Use as plate / cover destination</button>}
                {action === 'move' && !boundInputs && <button type="button" disabled={!moveReady} onClick={() => updateInputs({ ...inputs, location_id: station!.locationId, well: selection.wells[0] })}>Use as Move target</button>}
                {action === 'transfer' && !boundInputs && <><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('source')}>Use as source</button><button type="button" disabled={!endpointReady} onClick={() => adoptEndpoint('destination')}>Use as destination</button><p>Source: {endpointText('source')}<br />Destination: {endpointText('destination')}</p></>}
                <button type="button" onClick={() => { setSetupOpen(false); openEditor(); }}>Edit selected step</button>
                <label>Endpoint placement context<select aria-label="Endpoint placement context" value={occurrence} onChange={e => setOccurrence(e.target.value)}><option value="">Starting assignments</option>{(preview?.provenance ?? []).map(p => <option key={String(p.occurrence_id)} value={String(p.occurrence_id)}>After {String(p.step_id)} · {String(p.occurrence_id)}</option>)}</select></label>
                <small>{occurrence ? 'Planned after the selected compiled occurrence, not observed custody.' : 'Starting placement; Preview exposes occurrence-specific placement.'} Retained targets change only when explicitly updated.</small>
                {action === 'transfer' && !boundInputs && ['source', 'destination'].map(key => {
                    const endpoint = object(inputs[key]), next = plateBoundEndpoint(inputs[key], String(endpoint.labware_id ?? ''), plan, occurrenceState);
                    return next && (next.location_id !== endpoint.location_id || next.station !== endpoint.station) ? <button type="button" key={key} onClick={() => updateInputs({ ...inputs, [key]: next })}>Update {key} to planned plate location</button> : null;
                })}
            </div>

        </section>
        <aside aria-label="Method sequence and Properties" className="bioxp-method-inspector">
            {allNodes.some(n => n.type === 'group' || n.type === 'repeat') && <details className="bioxp-method-process-stages"><summary>Process stages & generated children</summary><ol>{allNodes.map((n, index) => <li key={`${index}:${String(n.step_id)}`}><button type="button" onClick={() => selectStep(nodePath(n))}>{String(n.label || n.action || n.type)}{n.type === 'repeat' ? ' · repeated group' : ''}{n.enabled === false ? ' · disabled' : ''}</button></li>)}</ol><small>Source-owned ordered stages. Repeats enclose their authored children; external work remains an operator checkpoint.</small></details>}

            <details className="bioxp-method-addition-tools"><summary>Add reagent / aliquot</summary><section aria-label="Reagent additions"><label>Addition name<input aria-label="Addition name" value={additionName} onChange={e => setAdditionName(e.target.value)} placeholder="e.g. Primer mix" /></label><button type="button" disabled={!hasAction('transfer')} onClick={() => { append('transfer', transferInputs(), additionName || 'Reagent addition'); setAdditionName(''); }}>Add reagent / aliquot</button><small>Choose source, reaction wells and volume in Properties. Each addition has its own settings.</small></section></details>
            <details><summary>Add temperature program or hold</summary><section aria-label="Cycling setup"><button type="button" disabled={!hasAction('thermal_profile')} onClick={() => append('thermal_profile', { segments: [] }, 'Temperature program')}>Add repeated cycles</button><button type="button" disabled={!hasAction('thermal_hold')} onClick={() => append('thermal_hold', {}, 'Initial / final hold')}>Insert initial / final hold</button><small>Initial and final holds are separate steps, not repeated stages. Use Insert step to place them.</small></section></details>
            <MethodOutline editorHost={sequenceOnly ? undefined : editorHost} onDuplicate={duplicate} onRemove={remove} actionProperties={properties} nodes={nodes} onChange={steps => onChange({ ...method, steps })} catalog={catalog} rootSchema={rootSchema} nodeSchema={rootSchema.properties?.steps?.items} procedures={Array.isArray(method.procedures) ? method.procedures as MethodValue[] : []} findings={findings} flat={flat} selection={{ id: selectedPath, onSelect: selectStep }} />
        </aside>
    </div></>;
}
