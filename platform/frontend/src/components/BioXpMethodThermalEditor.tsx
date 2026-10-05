import { useState } from 'react';
import type { MethodValue, MethodCatalog } from '../lib/bioxpMethods';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { isMethodNumber, methodNumber } from '../lib/bioxpMethodNumber';
import { MethodFields, object } from './BioXpMethodFields';
import { composeThermalRepeat, composeChillerTimer } from '../lib/bioxpMethodThermal';
import './BioXpMethodThermalEditor.css';

// Public integration seam keeps the action palette and editor together.
// eslint-disable-next-line react-refresh/only-export-components
export const thermalMethodActions = new Set(['chiller_setpoint', 'thermal_door', 'thermal_setpoint', 'thermal_hold', 'incubate', 'thermal_profile']);
const isObject = (v: unknown): v is MethodValue => v !== null && typeof v === 'object' && !Array.isArray(v);
const editableNumber = (v: unknown) => v === undefined || typeof v === 'number' || typeof v === 'string' || isMethodNumber(v);
const numberText = (v: unknown) => isMethodNumber(v) ? v.expr.value : typeof v === 'string' || typeof v === 'number' ? String(v) : '';
const retained = (v: unknown) => v === null ? 'null retained' : 'Expression / nonstandard value retained';
function Disclosure({ title, render }: { title: string; render: () => React.ReactNode }) {
    const [visited, setVisited] = useState(false);
    return <details onToggle={e => { if (e.currentTarget.open) setVisited(true); }}><summary>{title}</summary>{visited && render()}</details>;
}
function Controls({ value, schema, label, hold, chiller = false, profile = false, onChange }: { value: MethodValue; schema?: Schema; label: string; hold: boolean; chiller?: boolean; profile?: boolean; onChange: (next: MethodValue) => void }) {
    const set = (key: string, next: unknown) => { const result = { ...value }; if (next === undefined) delete result[key]; else result[key] = next; onChange(result); };
    const numeric = (key: string, title: string) => <label>{title}<input aria-label={`${label} ${title}`} inputMode="decimal" type="text" value={numberText(value[key])} disabled={!editableNumber(value[key])} placeholder={value[key] === undefined ? 'Not set' : !editableNumber(value[key]) ? retained(value[key]) : undefined} onChange={e => set(key, methodNumber(e.target.value))} />{Object.hasOwn(value, key) && <button type="button" aria-label={`${label} Omit ${title}`} onClick={() => set(key, undefined)}>Omit</button>}</label>;
    const choice = (key: string, title: string, options: Array<[string, string]>) => {
        const known = options.some(([v]) => v === value[key]);
        const special = value[key] !== undefined && !known;
        return <label>{title}<select aria-label={`${label} ${title}`} value={special ? '__retained' : String(value[key] ?? '')} onChange={e => { if (e.target.value !== '__retained') set(key, e.target.value || undefined); }}><option value="">Not set</option>{special && <option value="__retained">{typeof value[key] === 'string' ? value[key] as string : retained(value[key])}</option>}{options.map(([v, text]) => <option key={v} value={v}>{text}</option>)}</select></label>;
    };
    const options = <>
        <div className="bioxp-thermal-fields">{choice('bank', chiller ? 'Deck block' : 'Thermal bank', chiller ? [['rc', 'RC chiller'], ['oc', 'OC chiller']] : [['nest', 'TC sample nest'], ['lid', 'TC lid'], ['pedestal', 'TC pedestal']])}{!profile && numeric('target_temp_c', 'Target temperature (°C)')}
            {hold && <>{!profile && numeric('duration_s', 'Hold time (seconds)')}{choice('start', 'Start hold timer', [['dispatch', 'When command is sent'], ['attainment', 'When target is reached']])}</>}
        </div>
        {!chiller && <Disclosure title="Ramp, fan and attainment settings" render={() => <>
            <div className="bioxp-thermal-fields">{numeric('fan_speed', 'Fan speed (0–255)')}
                {value.bank !== 'pedestal' && <>{numeric('heat_rate_c_s', 'Heating rate (°C/s)')}{numeric('cool_rate_c_s', 'Cooling rate (°C/s)')}</>}
                {hold && (value.start === 'attainment' || Object.hasOwn(value, 'tolerance_c') || Object.hasOwn(value, 'timeout_s')) && <>{numeric('tolerance_c', 'Target tolerance (°C)')}{numeric('timeout_s', 'Target timeout (seconds)')}</>}
            </div><p>Ramp rates are a pair: heating 0 to 2 °C/s; cooling −2 to 0 °C/s. Pedestal does not support ramp rates. Attainment timing requires tolerance and timeout. Switching bank or timing keeps existing fields; remove unwanted values explicitly.</p>
        </>} />}
        <Disclosure title="Advanced controls / expressions and retained fields" render={() => <MethodFields label={`${label} inputs`} schema={schema} value={value} onChange={v => onChange(object(v))} />} />
    </>;
    return profile ? <><div className="bioxp-thermal-exact">{numeric('target_temp_c', 'Target temperature (°C)')}{numeric('duration_s', 'Hold time (seconds)')}</div><details className="bioxp-thermal-step-options"><summary>{label} options</summary>{options}</details></> : options;
}

/** Authoring only: no discovery requests, compilation, or hardware effects. */
export function BioXpMethodThermalEditor({ node, onChange, catalog, compact = false, onCompose, profileStep = false }: { node: MethodValue; onChange: (next: MethodValue) => void; catalog: MethodCatalog; compact?: boolean; profileStep?: boolean; onCompose?: (next: MethodValue) => void }) {
    const [expanded, setExpanded] = useState(!compact);
    const [first, setFirst] = useState('');
    const [last, setLast] = useState('');
    const [passes, setPasses] = useState('');
    const [seconds, setSeconds] = useState('');
    const [timerId, setTimerId] = useState('');
    const [waitHere, setWaitHere] = useState(true);
    const compose = onCompose ?? onChange;
    const action = String(node.action ?? '');
    const entry = catalog.actions?.find(a => (a.action ?? a.id) === action);
    const source = entry?.input_schema ?? entry?.inputs;
    const schema: Schema | undefined = source ? { ...source, $defs: { ...object(catalog.native_definitions) as Record<string, Schema>, ...source.$defs } } : undefined;
    const inputs = object(node.inputs);
    const setInputs = (next: unknown) => onChange({ ...node, inputs: next });
    const plainInputs = node.inputs === undefined || (isObject(node.inputs) && !Object.hasOwn(inputs, 'expr'));
    const segments = Array.isArray(inputs.segments) ? inputs.segments : [];
    const plainSegments = inputs.segments === undefined || Array.isArray(inputs.segments);
    const segmentSchema = schema?.properties?.segments?.items as Schema | undefined;
    const setSegments = (next: unknown[]) => setInputs({ ...inputs, segments: next });
    const targets = segments.map(s => { const text = numberText(object(s).target_temp_c); return text.trim() !== '' && Number.isFinite(Number(text)) ? Number(text) : null; });
    const known = targets.filter((n): n is number => n !== null);
    const low = known.reduce((a, b) => Math.min(a, b), 0), high = known.reduce((a, b) => Math.max(a, b), 1);
    const height = (index: number) => targets[index] === null ? 50 : 15 + ((high / 2 - targets[index]! / 2) / (high / 2 - low / 2)) * 85;
    const move = (from: number, to: number) => { const next = [...segments]; next.splice(to, 0, next.splice(from, 1)[0]); setSegments(next); };
    if ((node.type === 'group' || node.type === 'repeat') && Array.isArray(node.steps)) return <section className="bioxp-method-thermal bioxp-thermal-program-group" aria-label="Temperature program group">
        <p>{node.type === 'repeat' ? 'Nested method repeat — scope retained' : 'Temperature program · ordered steps'}</p>
        {node.type === 'repeat' && <label>Total passes<input aria-label="Method repeat total passes" value={numberText(node.count)} disabled={!editableNumber(node.count)} onChange={e => onChange({ ...node, count: methodNumber(e.target.value) })} /></label>}
        <div className="bioxp-thermal-program-children">
        {(node.steps as unknown[]).map((child, i) => isObject(child) ? <BioXpMethodThermalEditor key={String(child.step_id ?? i)} node={child} catalog={catalog} compact={compact} profileStep onChange={next => onChange({ ...node, steps: (node.steps as MethodValue[]).map((old, j) => i === j ? next : old) })} /> : <Disclosure key={i} title="Retained group child" render={() => <MethodFields label="Retained child" value={child} onChange={next => onChange({ ...node, steps: (node.steps as unknown[]).map((old, j) => i === j ? next : old) })} />} />)}
        </div><div className="bioxp-thermal-stage-actions"><button type="button" onClick={() => onChange({ ...node, steps: [...node.steps as unknown[], { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_hold', inputs: {} }] })}>Add hold</button><button type="button" onClick={() => onChange({ ...node, steps: [...node.steps as unknown[], { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_profile', inputs: {} }] })}>Add temperature sequence</button><button type="button" onClick={() => onChange({ ...node, steps: [...node.steps as unknown[], { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_setpoint', inputs: {} }] })}>Add keep-target continuation</button></div><Disclosure title="Advanced group / scope and extensions" render={() => <MethodFields label="Thermal group" value={node} onChange={v => onChange(object(v))} />} />
    </section>;
    return <section className={`bioxp-method-thermal ${expanded ? 'expanded' : 'compact'}`} aria-label="Thermal workflow editor">
        {action === 'thermal_profile' && <header><strong>Thermal cycler · temperature program</strong><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? 'Compact program' : 'Edit full program'}</button></header>}
        <p className="bioxp-thermal-hint">Draft step. No live command is sent.</p>
        {!plainInputs || !thermalMethodActions.has(action) ? <><p>Retained input requires advanced editing.</p><Disclosure title="Advanced thermal inputs / expressions" render={() => <MethodFields label="Thermal inputs" schema={schema} value={node.inputs} onChange={setInputs} />} /></> : action === 'thermal_door' ? <>
            <div className="bioxp-thermal-fields"><label>Thermal door<select aria-label="Thermal door" value={(inputs.door_command === 'DO' || inputs.door_command === 'DC') ? String(inputs.door_command) : inputs.door_command === undefined ? '' : '__retained'} onChange={e => { const next = { ...inputs }; if (e.target.value === '__retained') return; if (e.target.value) next.door_command = e.target.value; else delete next.door_command; setInputs(next); }}><option value="">Not set</option>{inputs.door_command !== undefined && !(inputs.door_command === 'DO' || inputs.door_command === 'DC') && <option value="__retained">Retained nonstandard value</option>}<option value="DO">Open</option><option value="DC">Close</option></select></label></div>
            <p>Open uses the native door-open step, including pipette initialization when executed.</p>
            <Disclosure title="Advanced controls / expressions and retained fields" render={() => <MethodFields label="Door inputs" schema={schema} value={inputs} onChange={setInputs} />} />
        </> : action === 'thermal_profile' ? <>
            <label>Total passes through this profile<input aria-label="Cycle count" title="Native repeat count; 0 is supported. Initial and final holds belong outside this repeated profile." type="text" inputMode="numeric" value={numberText(inputs.repeat)} disabled={!editableNumber(inputs.repeat)} placeholder={inputs.repeat === undefined ? 'Not set' : !editableNumber(inputs.repeat) ? retained(inputs.repeat) : undefined} onChange={e => setInputs({ ...inputs, repeat: methodNumber(e.target.value) })} /></label>
            {Object.hasOwn(inputs, 'repeat') && <button type="button" onClick={() => { const next = { ...inputs }; delete next.repeat; setInputs(next); }}>Omit cycle count</button>}
            <p>Programmed targets · step order → · seconds, not an elapsed-time scale. No measured temperature is shown.</p>
            {expanded && plainSegments && segments.length > 0 && <div className="bioxp-thermal-fields">
                <label>First step<select aria-label="Repeat first step" value={first} onChange={e => setFirst(e.target.value)}><option value="">Select</option>{segments.map((_, i) => <option key={i} value={i}>{i + 1}</option>)}</select></label>
                <label>Last step<select aria-label="Repeat last step" value={last} onChange={e => setLast(e.target.value)}><option value="">Select</option>{segments.map((_, i) => <option key={i} value={i}>{i + 1}</option>)}</select></label>
                <label>Total passes<input aria-label="Selected group total passes" value={passes} onChange={e => setPasses(e.target.value)} /></label>
                <button type="button" disabled={first === '' || last === '' || Number(first) > Number(last)} onClick={() => compose(composeThermalRepeat(node, Number(first), Number(last), methodNumber(passes)))}>Repeat selected steps</button>
            </div>}
            {plainSegments ? <><button type="button" onClick={() => setInputs({ ...inputs, repeat: methodNumber('1') })}>Run sequence once</button><ol className="bioxp-thermal-stages"><svg className="bioxp-thermal-target-line" aria-hidden="true" viewBox={`0 0 ${Math.max(1, segments.length) * 100} 120`} style={{ width: `${segments.length * 15.6}rem` }} preserveAspectRatio="none">{targets.map((target, i) => target === null ? null : <path key={i} d={`${i > 0 && targets[i - 1] !== null && object(segments[i - 1]).bank === object(segments[i]).bank ? `M${(i - 1) * 100 + 95} ${height(i - 1)} L` : 'M'}${i * 100 + 5} ${height(i)} L${i * 100 + 95} ${height(i)}`} />)}</svg>{segments.map((segment, index) => <li key={index} style={{ paddingTop: `${height(index)}px` }}><fieldset><legend>Stage {index + 1}</legend>
                <div className="bioxp-thermal-stage-actions"><button type="button" aria-label={`Stage ${index + 1} Move up`} disabled={index === 0} onClick={() => move(index, index - 1)}>↑ Up</button><button type="button" aria-label={`Stage ${index + 1} Move down`} disabled={index === segments.length - 1} onClick={() => move(index, index + 1)}>↓ Down</button><button type="button" aria-label={`Stage ${index + 1} Duplicate`} onClick={() => setSegments([...segments.slice(0, index + 1), structuredClone(segment), ...segments.slice(index + 1)])}>Duplicate</button><button type="button" aria-label={`Stage ${index + 1} Remove`} onClick={() => setSegments(segments.filter((_, i) => i !== index))}>Remove</button></div>
                {isObject(segment) && !Object.hasOwn(segment, 'expr') ? <Controls profile label={`Stage ${index + 1}`} schema={segmentSchema ? { ...segmentSchema, $defs: schema?.$defs } : undefined} hold value={segment} onChange={next => setSegments(segments.map((old, i) => i === index ? next : old))} /> : <Disclosure title="Advanced stage / retained value" render={() => <MethodFields label={`Stage ${index + 1}`} schema={segmentSchema} rootSchema={schema} value={segment} onChange={next => setSegments(segments.map((old, i) => i === index ? next : old))} />} />}
            </fieldset></li>)}</ol><button type="button" onClick={() => setSegments([...segments, {}])}>Add stage</button></> : <p>Nonstandard stages value retained; edit it in Advanced controls.</p>}
            <Disclosure title="Advanced profile controls / expressions and retained fields" render={() => <MethodFields label="Profile inputs" schema={schema} value={inputs} onChange={setInputs} />} />
        </> : <Controls profile={profileStep && (action === 'thermal_hold' || action === 'incubate')} label="Step" schema={schema} value={inputs} chiller={action === 'chiller_setpoint'} hold={action === 'thermal_hold' || action === 'incubate'} onChange={setInputs} />}
        {action === 'chiller_setpoint' && plainInputs && <Disclosure title="Elapsed conditioning timer" render={() => <>
            <div className="bioxp-thermal-fields"><label>Timer identity<input aria-label="Chiller timer identity" value={timerId} onChange={e => setTimerId(e.target.value)} /></label><label>Elapsed seconds<input aria-label="Chiller elapsed seconds" value={seconds} onChange={e => setSeconds(e.target.value)} /></label><label>Timing<select aria-label="Chiller timing" value={waitHere ? 'here' : 'later'} onChange={e => setWaitHere(e.target.value === 'here')}><option value="here">Wait here</option><option value="later">Continue other steps; wait later</option></select></label></div>
            <p>Elapsed from dispatch, not attainment. Target remains set; no automatic Off. For continue, insert timer_wait with this identity at the desired later position.</p>
            <button type="button" onClick={() => compose(composeChillerTimer(node, methodNumber(seconds), timerId, waitHere))}>Add elapsed timer</button>
        </>} />}
        {action === 'thermal_setpoint' && <p>Keep target; continue method. No infinite hold; persistence across reset/disconnect is not guaranteed.</p>}
        <Disclosure title="Advanced full step / extensions" render={() => <MethodFields label="Thermal step" value={node} onChange={v => onChange(object(v))} />} />
    </section>;
}
