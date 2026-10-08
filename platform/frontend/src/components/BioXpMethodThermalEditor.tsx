import { useState, useLayoutEffect, useRef, type CSSProperties } from 'react';
import type { MethodValue, MethodCatalog } from '../lib/bioxpMethods';
import type { BioXpOperatorJsonSchema as Schema } from '../lib/bioxpClient';
import { isMethodNumber, methodNumber } from '../lib/bioxpMethodNumber';
import { MethodFields, object } from './BioXpMethodFields';
import { canUngroupCanvasNode } from '../lib/bioxpMethodCanvas';
import { composeThermalRepeat, composeChillerTimer } from '../lib/bioxpMethodThermal';
import './BioXpMethodThermalEditor.css';

// Public integration seam keeps the action palette and editor together.
// eslint-disable-next-line react-refresh/only-export-components
export const thermalMethodActions = new Set(['chiller_setpoint', 'thermal_door', 'thermal_setpoint', 'thermal_hold', 'incubate', 'thermal_profile']);
const isObject = (v: unknown): v is MethodValue => v !== null && typeof v === 'object' && !Array.isArray(v);
const editableNumber = (v: unknown) => v === undefined || typeof v === 'number' || typeof v === 'string' || isMethodNumber(v);
const numberText = (v: unknown) => isMethodNumber(v) ? v.expr.value : typeof v === 'string' || typeof v === 'number' ? String(v) : '';
const retained = (v: unknown) => v === null ? 'null retained' : 'Expression / nonstandard value retained';
function Disclosure({ title, render, initiallyOpen = false }: { title: string; render: () => React.ReactNode; initiallyOpen?: boolean }) {
    const [visited, setVisited] = useState(initiallyOpen);
    return <details open={initiallyOpen || undefined} onToggle={e => { if (e.currentTarget.open) setVisited(true); }}><summary>{title}</summary>{visited && render()}</details>;
}
function Controls({ value, schema, label, hold, chiller = false, profile = false, onChange }: { value: MethodValue; schema?: Schema; label: string; hold: boolean; chiller?: boolean; profile?: boolean; onChange: (next: MethodValue) => void }) {
    const set = (key: string, next: unknown) => { const result = { ...value }; if (next === undefined) delete result[key]; else result[key] = next; onChange(result); };
    const numeric = (key: string, title: string) => <label className={`bioxp-thermal-value bioxp-thermal-value-${key}`}><span className="bioxp-thermal-value-title">{title}</span><input aria-label={`${label} ${title}`} inputMode="decimal" type="text" value={numberText(value[key])} disabled={!editableNumber(value[key])} placeholder={value[key] === undefined ? 'Not set' : !editableNumber(value[key]) ? retained(value[key]) : undefined} onChange={e => set(key, methodNumber(e.target.value))} />{profile && <span className="bioxp-thermal-value-unit">{key === 'target_temp_c' ? '°C' : key === 'duration_s' ? 's' : ''}</span>}{Object.hasOwn(value, key) && <button type="button" aria-label={`${label} Omit ${title}`} onClick={() => set(key, undefined)}>Omit</button>}</label>;
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
    return profile ? <><div className="bioxp-thermal-exact">{numeric('target_temp_c', 'Target temperature (°C)')}{hold ? numeric('duration_s', 'Hold time (seconds)') : <span className="bioxp-thermal-continuation">Keep target · continue</span>}</div><span className="bioxp-thermal-timer-cue">{hold ? value.start === 'dispatch' ? 'At step start' : value.start === 'attainment' ? 'At target temperature' : 'Timer start not set' : 'Continue method'}</span><details className="bioxp-thermal-step-options"><summary>{label} options</summary><p>{hold ? value.start === 'dispatch' ? 'At step start' : value.start === 'attainment' ? 'At target temperature' : 'Timer start not set' : 'Continue method'}</p>{options}</details></> : options;
}

type DuplicateScope = (source: MethodValue, replace: (copy: MethodValue) => MethodValue) => void;
type BindingProjection = (node: MethodValue, change: (node: MethodValue) => void) => { node: MethodValue; onChange: (node: MethodValue) => void };
type DragSlot = { owner: string; index: number; move: (to: number) => void };
type ProgramLeaf = { drag?: DragSlot; key: string; value: unknown; label: string; hold: boolean; schema?: Schema; onChange: (value: unknown) => void; actions?: React.ReactNode; retainedNode?: MethodValue; updateNode?: (node: MethodValue) => void };
type ProgramFrame = { drag?: DragSlot; key: string; from: number; size: number; depth: number; node: MethodValue; onChange: (value: MethodValue) => void; actions?: React.ReactNode };
type ProgramScope = { key: string; from: number; end: number; children: Array<{ from: number; end: number }>; apply: (first: number, last: number, passes: unknown) => void };


// This is a read-only projection of authored scope. Edits merge back through the
// exact parent path; display geometry never becomes AST or native input data.
function ProgramGraph({ node, catalog, onChange, expanded, onSelectRange, bindingProjection, onDuplicateScope }: { onDuplicateScope?: DuplicateScope; bindingProjection?: BindingProjection; node: MethodValue; catalog: MethodCatalog; onChange: (node: MethodValue) => void; expanded: boolean; onSelectRange?: (first: number, last: number) => void }) {
    const [selected, setSelected] = useState<number | null>(null);
    const [range, setRange] = useState<[number, number] | null>(null);
    const [scopeKey, setScopeKey] = useState('');
    const [rangePasses, setRangePasses] = useState('');
    const drag = useRef<DragSlot | null>(null);
    const dragProps = (slot?: DragSlot) => ({
        draggable: !!slot,
        onDragStart: (event: React.DragEvent) => { if (!slot || (event.target as HTMLElement).closest('input,button,summary')) { event.preventDefault(); return; } event.stopPropagation(); drag.current = slot; event.dataTransfer?.setData('text/plain', `${slot.owner}/${slot.index}`); },
        onDragOver: (event: React.DragEvent) => { if (slot && drag.current?.owner === slot.owner) event.preventDefault(); },
        onDrop: (event: React.DragEvent) => { event.preventDefault(); event.stopPropagation(); if (slot && drag.current?.owner === slot.owner) drag.current.move(slot.index); drag.current = null; },
        onDragEnd: () => { drag.current = null; },
    });

    const trackRef = useRef<HTMLDivElement>(null);
    const [optionsSpace, setOptionsSpace] = useState<number>();
    useLayoutEffect(() => {
        const track = trackRef.current;
        if (!track) return;
        const measure = () => {
            const row = track.querySelector('.bioxp-thermal-stages');
            const bottom = row?.getBoundingClientRect().bottom ?? 0;
            const panels = [...track.querySelectorAll<HTMLDetailsElement>('.bioxp-thermal-step-options[open], .bioxp-thermal-step-menu[open] > div, .bioxp-thermal-repeat-menu[open] > div')];
            setOptionsSpace(Math.max(0, ...panels.map(panel => panel.getBoundingClientRect().bottom - bottom + 12)));
        };
        const toggle = (event: Event) => {
            const details = event.target as HTMLDetailsElement;
            if (details.matches('.bioxp-thermal-step-options') && details.open) {
                track.querySelectorAll<HTMLDetailsElement>('.bioxp-thermal-step-options[open]').forEach(other => { if (other !== details) other.open = false; });
            }
            if (details.matches('.bioxp-thermal-step-menu, .bioxp-thermal-repeat-menu') && details.open) {
                track.querySelectorAll<HTMLDetailsElement>('.bioxp-thermal-step-menu[open], .bioxp-thermal-repeat-menu[open]').forEach(other => { if (other !== details) other.open = false; });
            }
            measure();
        };
        track.addEventListener('toggle', toggle, true);
        const observer = typeof ResizeObserver === 'undefined' ? undefined : new ResizeObserver(measure);
        track.querySelectorAll('.bioxp-thermal-step-options').forEach(panel => observer?.observe(panel));
        measure();
        return () => { observer?.disconnect(); track.removeEventListener('toggle', toggle, true); };
    }, [node, expanded]);
    const leaves: ProgramLeaf[] = [];
    const frames: ProgramFrame[] = [];
    const scopes: ProgramScope[] = [];
    const project = (current: MethodValue, update: (next: MethodValue) => void, path: string, depth: number, build: (next: MethodValue) => MethodValue = next => next) => {
        const bound = bindingProjection?.(current, update);
        if (bound) { current = bound.node; update = bound.onChange; }
        const from = leaves.length;
        const input = object(current.inputs);
        const entry = catalog.actions?.find(a => (a.action ?? a.id) === current.action);
        const source = entry?.input_schema ?? entry?.inputs;
        const schema = source ? { ...source, $defs: { ...object(catalog.native_definitions) as Record<string, Schema>, ...source.$defs } } : undefined;
        if ((current.type === 'group' || current.type === 'repeat' && !Object.hasOwn(current, 'items')) && Array.isArray(current.steps)) {
            const steps = current.steps;
            const children: ProgramScope['children'] = [];
            steps.forEach((child, i) => {
                const childFrom = leaves.length;
                const change = (next: unknown) => update({ ...current, steps: steps.map((old, j) => i === j ? next : old) });
                if (isObject(child)) {
                    const before = leaves.length;
                    project(child, change, `${path}/${i}`, depth + (current.type === 'repeat' || path !== 'program' ? 1 : 0), next => build({ ...current, steps: steps.map((old, j) => i === j ? next : old) }));
                    const frame = frames.find(f => f.key === `${path}/${i}`);
                    const childEnd = leaves.length - 1;
                    if (frame) {
                        const move = (to: number) => { const next = [...steps]; next.splice(to, 0, next.splice(i, 1)[0]); update({ ...current, steps: next }); };
                        frame.drag = { owner: path, index: i, move };
                        frame.actions = <>{child.type === 'group' && (canUngroupCanvasNode(child) ? <button type="button" onClick={() => update({ ...current, steps: [...steps.slice(0, i), ...child.steps as MethodValue[], ...steps.slice(i + 1)] })}>Ungroup ordered children</button> : <small>Group behavior and extensions retained. Edit them explicitly in Advanced before removing this wrapper.</small>)}<button type="button" onClick={() => { setRange([before, childEnd]); setScopeKey(`${path}/${i}`); }}>Select group scope</button>{onDuplicateScope && <button type="button" onClick={() => onDuplicateScope(child, copy => build({ ...current, steps: [...steps.slice(0, i + 1), copy, ...steps.slice(i + 1)] }))}>Duplicate group</button>}<button type="button" disabled={i === 0} onClick={() => move(i - 1)}>Move group earlier</button><button type="button" disabled={i === steps.length - 1} onClick={() => move(i + 1)}>Move group later</button><button type="button" onClick={() => update({ ...current, steps: steps.filter((_, j) => j !== i) })}>Remove group</button></>;
                    }
                    if (leaves.length === before + 1 && !leaves[before].actions) {
                        const move = (to: number) => { const next = [...steps]; next.splice(to, 0, next.splice(i, 1)[0]); update({ ...current, steps: next }); };
                        leaves[before].drag = { owner: path, index: i, move };
                        leaves[before].actions = <>{onDuplicateScope && <button type="button" aria-label={`Step ${before + 1} Duplicate`} onClick={() => onDuplicateScope(child, copy => build({ ...current, steps: [...steps.slice(0, i + 1), copy, ...steps.slice(i + 1)] }))}>Duplicate</button>}<button type="button" aria-label={`Step ${before + 1} Move earlier`} disabled={i === 0} onClick={() => move(i - 1)}>Move earlier</button><button type="button" aria-label={`Step ${before + 1} Move later`} disabled={i === steps.length - 1} onClick={() => move(i + 1)}>Move later</button><button type="button" aria-label={`Step ${before + 1} Remove`} onClick={() => update({ ...current, steps: steps.filter((_, j) => j !== i) })}>Remove</button><button type="button" aria-label={`Insert after step ${before + 1}`} onClick={() => update({ ...current, steps: [...steps.slice(0, i + 1), { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_hold', inputs: {} }, ...steps.slice(i + 1)] })}>Insert after</button></>;
                    }
                }
                else leaves.push({ key: `${path}/${i}`, value: child, label: 'Retained child', hold: false, onChange: change });
                children.push({ from: childFrom, end: leaves.length - 1 });
            });
            scopes.push({ key: path, from, end: leaves.length - 1, children, apply: (first, last, passes) => update({ ...current, steps: [...steps.slice(0, first), { type: 'repeat', step_id: crypto.randomUUID(), count: passes, steps: steps.slice(first, last + 1) }, ...steps.slice(last + 1)] }) });
            if (current.type === 'repeat' || path !== 'program') frames.push({ key: path, from, size: leaves.length - from, depth, node: current, onChange: update });
        } else if (current.action === 'thermal_profile' && (input.segments === undefined || Array.isArray(input.segments)) && (current.inputs === undefined || (isObject(current.inputs) && !Object.hasOwn(input, 'expr')))) {
            const segments = Array.isArray(input.segments) ? input.segments : [];
            const changeSegments = (next: unknown[]) => update({ ...current, inputs: { ...input, segments: next } });
            segments.forEach((segment, i) => {
                const move = (to: number) => { const next = [...segments]; next.splice(to, 0, next.splice(i, 1)[0]); changeSegments(next); };
                leaves.push({ drag: { owner: `${path}/segments`, index: i, move }, key: `${path}/segments/${i}`, value: segment, label: `Stage ${i + 1}`, hold: true,
                    schema: schema?.properties?.segments?.items ? { ...schema.properties.segments.items as Schema, $defs: schema.$defs } : undefined,
                    onChange: next => changeSegments(segments.map((old, j) => i === j ? next : old)),
                    actions: <><button type="button" aria-label={`Stage ${i + 1} Move up`} disabled={i === 0} onClick={() => move(i - 1)}>Move earlier</button><button type="button" aria-label={`Stage ${i + 1} Move down`} disabled={i === segments.length - 1} onClick={() => move(i + 1)}>Move later</button><button type="button" aria-label={`Stage ${i + 1} Duplicate`} onClick={() => changeSegments([...segments.slice(0, i + 1), structuredClone(segment), ...segments.slice(i + 1)])}>Duplicate</button><button type="button" aria-label={`Stage ${i + 1} Remove`} onClick={() => changeSegments(segments.filter((_, j) => i !== j))}>Remove</button><button type="button" aria-label={`Insert after stage ${i + 1}`} onClick={() => changeSegments([...segments.slice(0, i + 1), {}, ...segments.slice(i + 1)])}>Insert after</button></> });
            });
            scopes.push({ key: path, from, end: leaves.length - 1, children: segments.map((_, i) => ({ from: from + i, end: from + i })), apply: (first, last, passes) => update(composeThermalRepeat(current, first, last, passes)) });
            frames.push({ key: path, from, size: leaves.length - from, depth, node: current, onChange: update });
        } else {
            leaves.push({ key: path, value: current.inputs === undefined ? {} : current.inputs, label: 'Step', hold: current.action === 'thermal_hold' || current.action === 'incubate', schema,
                onChange: next => update({ ...current, inputs: next }), ...(!['thermal_hold', 'incubate', 'thermal_setpoint'].includes(String(current.action)) ? { retainedNode: current, updateNode: update } : {}) });
        }
    };
    project(node, onChange, 'program', 0);
    if (['thermal_hold', 'incubate', 'thermal_setpoint'].includes(String(node.action))) scopes.push({ key: 'program', from: 0, end: 0, children: [{ from: 0, end: 0 }], apply: (_first, _last, passes) => onChange({ type: 'repeat', step_id: crypto.randomUUID(), count: passes, steps: [node] }) });
    const rootFrame = frames.find(frame => frame.key === 'program');
    if (rootFrame) rootFrame.actions = <><button type="button" onClick={() => { setRange([0, leaves.length - 1]); setScopeKey('program'); }}>Select group scope</button>{onDuplicateScope && <button type="button" onClick={() => onDuplicateScope(node, copy => ({ type: 'group', step_id: crypto.randomUUID(), steps: [node, copy] }))}>Duplicate group</button>}</>;
    const candidates = range ? scopes.filter(scope => scope.from <= range[0] && scope.end >= range[1]) : [];
    const chosen = candidates.find(scope => scope.key === scopeKey);
    const firstChild = chosen && range ? chosen.children.findIndex(child => child.end >= range[0]) : -1;
    const lastChild = chosen && range ? chosen.children.reduce((last, child, index) => child.from <= range[1] ? index : last, -1) : -1;
    const targets = leaves.map(leaf => { const text = numberText(object(leaf.value).target_temp_c); return text.trim() !== '' && Number.isFinite(Number(text)) ? Number(text) : null; });
    const known = targets.filter((v): v is number => v !== null);
    // Bounded display arithmetic also handles very large retained literals.
    const low = known.length ? Math.min(...known) : 0;
    const high = known.length ? Math.max(...known) : 80;
    const extent = Math.max(20, high / 2 - low / 2);
    const y = (i: number) => targets[i] === null ? 145 : 75 + ((high / 2 - targets[i]! / 2) / extent) * 130;
    const ticks = [high, high - extent, high - extent * 2];
    const count = Math.max(1, leaves.length);
    const depth = frames.reduce((a, frame) => Math.max(a, frame.depth), 0);
    const heading = 40 + depth * 34;
    const updateFrame = (frame: ProgramFrame, key: string, next: unknown) => {
        if (frame.node.type === 'repeat') { const value = { ...frame.node }; if (next === undefined) delete value[key]; else value[key] = next; frame.onChange(value); }
        else { const inputs = { ...object(frame.node.inputs) }; if (next === undefined) delete inputs[key]; else inputs[key] = next; frame.onChange({ ...frame.node, inputs }); }
    };
    return <div className="bioxp-thermal-program" data-program-view={expanded ? 'expanded' : 'compact'}>
        {expanded && leaves.length > 0 && <button type="button" onClick={() => { setRange([selected ?? 0, selected ?? 0]); setScopeKey(''); }}>Repeat plotted steps</button>}
        {range && <div className="bioxp-thermal-repeat-builder" aria-label="Plotted range repeat">
            <strong>Selected plotted steps {range[0] + 1}–{range[1] + 1}</strong>
            <label>First plotted step<select aria-label="First plotted step" value={range[0]} onChange={e => { const first = Number(e.target.value); setRange([first, Math.max(first, range[1])]); setScopeKey(''); }}>{leaves.map((leaf, index) => <option key={leaf.key} value={index}>{index + 1}</option>)}</select></label>
            <label>Last plotted step<select aria-label="Last plotted step" value={range[1]} onChange={e => { const last = Number(e.target.value); setRange([Math.min(range[0], last), last]); setScopeKey(''); }}>{leaves.map((leaf, index) => <option key={leaf.key} value={index}>{index + 1}</option>)}</select></label>
            <label>Selected scope<select aria-label="Plotted repeat scope" value={scopeKey} onChange={e => setScopeKey(e.target.value)}><option value="">Choose scope explicitly</option>{candidates.map(scope => <option key={scope.key} value={scope.key}>{scope.key} · steps {scope.from + 1}–{scope.end + 1}</option>)}</select></label>
            {chosen && firstChild >= 0 && lastChild >= firstChild && <p>Repeat whole authored children covering plotted steps {chosen.children[firstChild].from + 1}–{chosen.children[lastChild].end + 1}. Existing nested scopes and their total passes stay intact; no flattening. Within a native profile, this replaces its repeat with a selected subgroup and outside holds.</p>}
            <label>Total passes<input aria-label="Plotted range total passes" value={rangePasses} onChange={e => setRangePasses(e.target.value)} /></label>
            <button type="button" disabled={!chosen || firstChild < 0 || lastChild < firstChild} onClick={() => { chosen!.apply(firstChild, lastChild, methodNumber(rangePasses)); setRange(null); setScopeKey(''); }}>Group plotted selection</button>
            <button type="button" onClick={() => { setRange(null); setScopeKey(''); }}>Cancel plotted selection</button>
        </div>}
        {frames.filter(frame => frame.size === 0 && frame.node.type === 'repeat').map(frame => <label key={frame.key}>Method repeat total passes<input aria-label="Method repeat total passes" value={numberText(frame.node.count)} disabled={!editableNumber(frame.node.count)} onChange={e => updateFrame(frame, 'count', methodNumber(e.target.value))} /></label>)}
        {!leaves.length && <div className="bioxp-thermal-empty"><strong>Build a temperature program</strong><p>Add a hold. Add more steps or repeat a group only when needed.</p></div>}
        {leaves.length > 0 && <div className="bioxp-thermal-track"><div ref={trackRef} className="bioxp-thermal-track-inner" style={{ '--options-space': optionsSpace === undefined ? undefined : `${optionsSpace}px`, '--step-count': count, '--heading-height': `${heading}px`, minWidth: expanded ? Math.max(660, count * 165) : count * 104 } as CSSProperties}>
            {expanded && <div className="bioxp-thermal-temperature-scale" aria-label="Programmed temperature scale"><small>°C</small>{ticks.map((tick, index) => <span key={index} style={{ top: heading + 75 + index * 65 }}>{Number(tick.toPrecision(4))}</span>)}</div>}
            <svg className="bioxp-thermal-target-line" aria-hidden="true" viewBox={`0 0 ${count * 100} 270`} preserveAspectRatio="none">
                {[75, 140, 205].map(tick => <line className="gridline" key={tick} x1="0" x2={count * 100} y1={tick} y2={tick} />)}
                {targets.map((target, i) => target === null ? null : <path data-bank={String(object(leaves[i].value).bank ?? '')} key={i} d={`${i > 0 && targets[i - 1] !== null && object(leaves[i - 1].value).bank === object(leaves[i].value).bank ? `M${(i - 1) * 100 + 80} ${y(i - 1)} L` : 'M'}${i * 100 + 20} ${y(i)} L${i * 100 + 80} ${y(i)}`} />)}
            </svg>
            {frames.filter(frame => frame.size > 0).map(frame => {
                const methodRepeat = frame.node.type === 'repeat';
                const plainGroup = frame.node.type === 'group';
                const key = methodRepeat ? 'count' : 'repeat';
                const value = methodRepeat ? frame.node.count : object(frame.node.inputs).repeat;
                return <div key={frame.key} data-frame-path={frame.key} className={`${plainGroup ? 'bioxp-thermal-scope-frame' : 'bioxp-thermal-repeat-frame'} ${selected !== null && selected >= frame.from && selected < frame.from + frame.size ? 'selected' : ''}`} style={{ left: `${frame.from / count * 100}%`, width: `${frame.size / count * 100}%`, top: frame.depth * 34, '--frame-offset': `${frame.depth * 34}px` } as CSSProperties}>
                    <div className="bioxp-thermal-repeat-heading" {...dragProps(frame.drag)} title="Drag to reorder within the authored parent scope"><span>{plainGroup ? 'Ordered group' : methodRepeat ? '↻ Method repeat' : '↻ Repeat this group'}</span>{!plainGroup && <label><input aria-label={methodRepeat ? 'Method repeat total passes' : 'Cycle count'} title="Native repeat count; 0 is supported" value={numberText(value)} disabled={!editableNumber(value)} placeholder={value === undefined ? '—' : retained(value)} onChange={e => updateFrame(frame, key, methodNumber(e.target.value))} /> total passes</label>}
                        <details className="bioxp-thermal-repeat-menu"><summary aria-label="Repeat group actions">⋯</summary><div onClick={e => { if ((e.target as HTMLElement).closest('button')) e.currentTarget.parentElement?.removeAttribute('open'); }}>{frame.actions}{!plainGroup && value !== undefined && <button type="button" onClick={() => updateFrame(frame, key, undefined)}>{methodRepeat ? 'Omit repeat count' : 'Omit cycle count'}</button>}{methodRepeat && !Object.hasOwn(frame.node, 'items') && <button type="button" onClick={() => { const next: MethodValue = { ...frame.node, type: 'group' }; delete next.count; frame.onChange(next); }}>Ungroup repeat; keep steps</button>}{!methodRepeat && !plainGroup && <><button type="button" title="Keep native profile fields and bindings; one pass through these steps" onClick={() => updateFrame(frame, key, methodNumber('1'))}>Ungroup repeat; keep native steps</button><button type="button" onClick={() => updateFrame(frame, key, methodNumber('1'))}>Run sequence once</button></>}</div></details>
                    </div>
                </div>;
            })}
            <ol className="bioxp-thermal-stages">{leaves.map((leaf, i) => <li key={leaf.key} className={`${selected === i ? 'selected' : ''} ${range && i >= range[0] && i <= range[1] ? 'range-selected' : ''} ${chosen && firstChild >= 0 && lastChild >= firstChild && i >= chosen.children[firstChild].from && i <= chosen.children[lastChild].end ? 'scope-selected' : ''}`} style={{ '--value-top': `${y(i) - 40}px`, '--time-top': `${y(i) + 7}px`, '--step-index': i } as CSSProperties} onClick={e => {
                    if ((e.target as HTMLElement).closest('input,select,button,summary,details')) return;
                    if (e.shiftKey && selected !== null) { const bounds: [number, number] = [Math.min(selected, i), Math.max(selected, i)]; setRange(bounds); setScopeKey(''); onSelectRange?.(...bounds); }
                    else { setSelected(i); setRange(null); }
                }}>
                <div className="bioxp-thermal-step-name" {...dragProps(leaf.drag)} title="Drag to reorder within the authored parent scope"><span>Step {i + 1}</span>{object(leaf.value).bank !== undefined && object(leaf.value).bank !== 'nest' && <small>{String(object(leaf.value).bank)}</small>}{leaf.actions && <details className="bioxp-thermal-step-menu"><summary aria-label={`Actions for step ${i + 1}`}>⋯</summary><div>{leaf.actions}</div></details>}</div>
                {leaf.retainedNode ? <Disclosure title="Retained thermal step" render={() => <BioXpMethodThermalEditor node={leaf.retainedNode!} catalog={catalog} onChange={leaf.updateNode!} />} /> : isObject(leaf.value) && !Object.hasOwn(leaf.value, 'expr') ? <Controls profile label={leaf.label} schema={leaf.schema} value={leaf.value} hold={leaf.hold} onChange={leaf.onChange} /> : <Disclosure title="Advanced stage / retained value" render={() => <MethodFields label={leaf.label} schema={leaf.schema} value={leaf.value} onChange={leaf.onChange} />} />}
            </li>)}</ol>
        </div></div>}
        <div className="bioxp-thermal-axis-foot"><span>Step order → · duration in seconds</span><span>Programmed targets · equal step widths, not elapsed-time scale</span></div>
        {frames.filter(frame => frame.key !== 'program').map(frame => <Disclosure key={frame.key} title={`${frame.node.type === 'repeat' ? 'Method repeat' : 'Temperature sequence'} ${frame.from + 1}–${frame.from + frame.size} / scope and extensions`} render={() => <BioXpMethodThermalEditor node={frame.node} catalog={catalog} expanded={expanded} onChange={frame.onChange} />} />)}
    </div>;
}

/** Authoring only: no discovery requests, compilation, or hardware effects. */
export function BioXpMethodThermalEditor({ node, onChange, catalog, compact = false, expanded: controlledExpanded, onExpandedChange, onCompose, profileStep = false, existingTimer = false, bindingProjection, embedded = false, onDuplicateScope }: { onDuplicateScope?: DuplicateScope; embedded?: boolean; bindingProjection?: BindingProjection; node: MethodValue; onChange: (next: MethodValue) => void; catalog: MethodCatalog; compact?: boolean; expanded?: boolean; onExpandedChange?: (expanded: boolean) => void; profileStep?: boolean; existingTimer?: boolean; onCompose?: (next: MethodValue) => void }) {
    const [uncontrolledExpanded, setUncontrolledExpanded] = useState(!compact);
    const expanded = controlledExpanded ?? uncontrolledExpanded;
    const setExpanded = (next: boolean) => {
        if (controlledExpanded === undefined) setUncontrolledExpanded(next);
        onExpandedChange?.(next);
    };
    const [repeatBuilder, setRepeatBuilder] = useState(false);
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
    const setSegments = (next: unknown[]) => setInputs({ ...inputs, segments: next });
    if (node.type === 'repeat' && Object.hasOwn(node, 'items')) return <section className="bioxp-method-thermal" aria-label="Retained ordered-item thermal scope"><p>Ordered-item iteration is retained in its original scope, not converted to total-pass repetition. Edit its complete structure below.</p><MethodFields label="Ordered-item thermal scope" value={node} onChange={value => onChange(object(value))} /></section>;
    if ((node.type === 'group' || node.type === 'repeat') && Array.isArray(node.steps)) return <section className={`bioxp-method-thermal bioxp-thermal-program-group ${expanded ? 'expanded' : 'compact'}`} aria-label="Temperature program group">
        {!embedded && <header><div><strong>Thermal cycler</strong><p>Temperature program · ordered steps</p></div><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? 'Compact program' : 'Edit full program'}</button></header>}
        {expanded && <details className="bioxp-thermal-repeat-builder" open={repeatBuilder} onToggle={e => setRepeatBuilder(e.currentTarget.open)}><summary>↻ Repeat ordered children</summary><div className="bioxp-thermal-fields"><label>First child<select aria-label="Repeat first child" value={first} onChange={e => setFirst(e.target.value)}><option value="">Select</option>{node.steps.map((_, i) => <option key={i} value={i}>{i + 1}</option>)}</select></label><label>Last child<select aria-label="Repeat last child" value={last} onChange={e => setLast(e.target.value)}><option value="">Select</option>{node.steps.map((_, i) => <option key={i} value={i}>{i + 1}</option>)}</select></label><label>Total passes<input aria-label="Selected children total passes" value={passes} onChange={e => setPasses(e.target.value)} /></label><button type="button" disabled={first === '' || last === '' || Number(first) > Number(last)} onClick={() => { const steps = node.steps as MethodValue[]; onChange({ ...node, steps: [...steps.slice(0, Number(first)), { type: 'repeat', step_id: crypto.randomUUID(), count: methodNumber(passes), steps: steps.slice(Number(first), Number(last) + 1) }, ...steps.slice(Number(last) + 1)] }); }}>Repeat selected children</button></div></details>}
        <ProgramGraph onDuplicateScope={onDuplicateScope} bindingProjection={bindingProjection} node={node} catalog={catalog} onChange={onChange} expanded={expanded} />
        <div className="bioxp-thermal-stage-actions"><button type="button" onClick={() => onChange({ ...node, steps: [...node.steps as unknown[], { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_hold', inputs: {} }] })}>Add hold</button><button type="button" onClick={() => onChange({ ...node, steps: [...node.steps as unknown[], { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_profile', inputs: {} }] })}>Add temperature sequence</button><button type="button" onClick={() => onChange({ ...node, steps: [...node.steps as unknown[], { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_setpoint', inputs: {} }] })}>Add keep-target continuation</button></div><Disclosure title="Advanced group / scope and extensions" render={() => <MethodFields label="Thermal group" value={node} onChange={v => onChange(object(v))} />} />
    </section>;
    return <section className={`bioxp-method-thermal ${expanded ? 'expanded' : 'compact'}`} aria-label="Thermal workflow editor" data-profile-step={profileStep || undefined}>
        {!embedded && ['thermal_profile', 'thermal_hold', 'incubate', 'thermal_setpoint'].includes(action) && <header><div><strong>Thermal cycler</strong><p>Temperature program</p></div><button type="button" onClick={() => setExpanded(!expanded)}>{expanded ? 'Compact program' : 'Edit full program'}</button></header>}
        <p className="bioxp-thermal-hint">Draft step. No live command is sent.</p>
        {!plainInputs || !thermalMethodActions.has(action) ? <><p>Retained input requires advanced editing.</p><Disclosure title="Advanced thermal inputs / expressions" render={() => <MethodFields label="Thermal inputs" schema={schema} value={node.inputs} onChange={setInputs} />} /></> : action === 'thermal_door' ? <>
            <div className="bioxp-thermal-fields"><label>Thermal door<select aria-label="Thermal door" value={(inputs.door_command === 'DO' || inputs.door_command === 'DC') ? String(inputs.door_command) : inputs.door_command === undefined ? '' : '__retained'} onChange={e => { const next = { ...inputs }; if (e.target.value === '__retained') return; if (e.target.value) next.door_command = e.target.value; else delete next.door_command; setInputs(next); }}><option value="">Not set</option>{inputs.door_command !== undefined && !(inputs.door_command === 'DO' || inputs.door_command === 'DC') && <option value="__retained">Retained nonstandard value</option>}<option value="DO">Open</option><option value="DC">Close</option></select></label></div>
            <p>Open uses the native door-open step, including pipette initialization when executed.</p>
            <Disclosure title="Advanced controls / expressions and retained fields" render={() => <MethodFields label="Door inputs" schema={schema} value={inputs} onChange={setInputs} />} />
        </> : action === 'thermal_profile' ? <>
            {!segments.length && <><label>Total passes through this profile<input aria-label="Cycle count" title="Native repeat count; 0 is supported. Initial and final holds belong outside this repeated profile." type="text" inputMode="numeric" value={numberText(inputs.repeat)} disabled={!editableNumber(inputs.repeat)} placeholder={inputs.repeat === undefined ? 'Not set' : !editableNumber(inputs.repeat) ? retained(inputs.repeat) : undefined} onChange={e => setInputs({ ...inputs, repeat: methodNumber(e.target.value) })} /></label>
            {Object.hasOwn(inputs, 'repeat') && <button type="button" onClick={() => { const next = { ...inputs }; delete next.repeat; setInputs(next); }}>Omit cycle count</button>}</>}
            {expanded && plainSegments && segments.length > 0 && <details className="bioxp-thermal-repeat-builder" open={repeatBuilder} onToggle={e => setRepeatBuilder(e.currentTarget.open)}><summary>↻ Repeat steps</summary><div className="bioxp-thermal-fields">
                <label>First step<select aria-label="Repeat first step" value={first} onChange={e => setFirst(e.target.value)}><option value="">Select</option>{segments.map((_, i) => <option key={i} value={i}>{i + 1}</option>)}</select></label>
                <label>Last step<select aria-label="Repeat last step" value={last} onChange={e => setLast(e.target.value)}><option value="">Select</option>{segments.map((_, i) => <option key={i} value={i}>{i + 1}</option>)}</select></label>
                <label>Total passes<input aria-label="Selected group total passes" value={passes} onChange={e => setPasses(e.target.value)} /></label>
                <button type="button" disabled={first === '' || last === '' || Number(first) > Number(last)} onClick={() => compose(composeThermalRepeat(node, Number(first), Number(last), methodNumber(passes)))}>Repeat selected steps</button>
            </div></details>}
            {plainSegments ? <><ProgramGraph onDuplicateScope={onDuplicateScope} bindingProjection={bindingProjection} node={node} catalog={catalog} onChange={onChange} expanded={expanded} onSelectRange={(a, b) => { setFirst(String(a)); setLast(String(b)); setRepeatBuilder(true); }} /><div className="bioxp-thermal-stage-actions"><button type="button" onClick={() => setSegments([...segments, {}])}>Add stage</button>{!segments.length && <button type="button" onClick={() => setInputs({ ...inputs, repeat: methodNumber('1') })}>Run sequence once</button>}</div></> : <p>Nonstandard stages value retained; edit it in Advanced controls.</p>}
            <Disclosure title="Advanced profile controls / expressions and retained fields" render={() => <MethodFields label="Profile inputs" schema={schema} value={inputs} onChange={setInputs} />} />
        </> : ['thermal_hold', 'incubate', 'thermal_setpoint'].includes(action) ? <ProgramGraph onDuplicateScope={onDuplicateScope} bindingProjection={bindingProjection} node={node} catalog={catalog} onChange={onChange} expanded={expanded} /> : <Controls label="Step" schema={schema} value={inputs} chiller={action === 'chiller_setpoint'} hold={action === 'thermal_hold' || action === 'incubate'} onChange={setInputs} />}
        {action === 'chiller_setpoint' && plainInputs && !existingTimer && <Disclosure initiallyOpen={embedded} title="Elapsed conditioning timer" render={() => <>
            <div className="bioxp-thermal-fields"><label>Timer identity<input aria-label="Chiller timer identity" value={timerId} onChange={e => setTimerId(e.target.value)} /></label><label>Elapsed seconds<input aria-label="Chiller elapsed seconds" value={seconds} onChange={e => setSeconds(e.target.value)} /></label><label>Timing<select aria-label="Chiller timing" value={waitHere ? 'here' : 'later'} onChange={e => setWaitHere(e.target.value === 'here')}><option value="here">Wait here</option><option value="later">Continue other steps; wait later</option></select></label></div>
            <p>Elapsed from dispatch, not attainment. Target remains set; no automatic Off. For continue, insert timer_wait with this identity at the desired later position.</p>
            <button type="button" onClick={() => compose(composeChillerTimer(node, methodNumber(seconds), timerId, waitHere))}>Add elapsed timer</button>
        </>} />}
        {plainInputs && ['thermal_hold', 'incubate', 'thermal_setpoint'].includes(action) && <button type="button" onClick={() => { const { action: _action, inputs: _inputs, required_capability: _capability, ...scope } = node; compose({ ...scope, type: 'group', steps: [{ ...node, step_id: crypto.randomUUID() }, { type: 'action', step_id: crypto.randomUUID(), action: 'thermal_hold', inputs: {} }] }); }}>Add hold</button>}
        {action === 'thermal_setpoint' && <p>Keep target; continue method. No infinite hold; persistence across reset/disconnect is not guaranteed.</p>}
        <Disclosure title="Advanced full step / extensions" render={() => <MethodFields label="Thermal step" value={node} onChange={v => onChange(object(v))} />} />
    </section>;
}
