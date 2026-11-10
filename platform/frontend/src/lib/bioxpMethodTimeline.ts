import type { MethodValue } from './bioxpMethods';
import { holderChannelFootprint, nativeChannelFootprint, type ManipulatedWell } from './bioxpMethodManipulations';

/** Display-only stage timeline: lanes share one column grid built from the page's compiled
 * actions in document order. Waits/holds take width by duration; nothing here is a run input. */
export type TimelineTone = 'heat' | 'hot' | 'ramp' | 'wait' | 'mag' | 'liquid' | 'operator' | 'tick' | 'cover';
export type TimelineBlock = { tone: TimelineTone; title: string; detail?: string; from: number; to: number };
export type TimelineLane = { id: string; label: string; blocks: TimelineBlock[] };
export type DeckMarker = { station: string; tone: 'heat' | 'mag' | 'cover' | 'wait' | 'cold'; title: string; detail?: string; wells?: string[] };
export type TraceSpan = { from: number; to: number; c: number; ramp: boolean };
export type StageTimeline = { title: string; total: string; weights: number[]; lanes: TimelineLane[]; trace?: TraceSpan[]; markers: DeckMarker[] };

const obj = (v: unknown): MethodValue => v && typeof v === 'object' && !Array.isArray(v) ? v as MethodValue : {};
const arr = (v: unknown): MethodValue[] => Array.isArray(v) ? v.map(obj) : [];
export const clock = (s: number) => `${Math.floor(s / 60)}:${String(Math.round(s % 60)).padStart(2, '0')}`;
const PLATE_AT: Record<string, string> = { LOC_MS: 'LOC_MS', LOC_P_MS: 'LOC_MS', LOC_OC: 'LOC_OC', LOC_P_OC: 'LOC_OC', LOC_TC: 'LOC_TC', LOC_P_TC: 'LOC_TC' };
const COVER_ON_OC = new Set(['LOC_OC', 'LOC_OC_COVER', 'LOC_P_OC']);

function endpoint(p: MethodValue, channels: number[]): ManipulatedWell | undefined {
    if (typeof p.target === 'string') return holderChannelFootprint(p.target, channels).wells[0];
    const location = Number(p.location_id);
    if (p.location_id == null || !Number.isInteger(location)) return undefined;
    return nativeChannelFootprint(location, p.well, channels, -1).wells[0];
}
function firstMove(hooks: unknown): MethodValue | undefined {
    return arr(hooks).map(h => obj(h.positioning ?? h)).find(p => p.operation === 'move');
}

type Group = { slot: number; src: ManipulatedWell[]; dst: ManipulatedWell; volumes: number[]; mix: boolean; srcStation: string };
type Event =
    | { kind: 'liquid'; group: Group }
    | { kind: 'wait'; slot: number; seconds: number; label: string }
    | { kind: 'pause'; slot: number; message: string }
    | { kind: 'temp'; at: number; c: number }
    | { kind: 'plate'; slot: number; to: string }
    | { kind: 'cover'; slot: number; on: boolean }
    | { kind: 'segment'; slot: number; c: number; seconds: number; ramp: boolean; attainment: boolean };

/** Returns null for pages without waits, holds or operator pauses. */
export function stageTimeline(pageLabel: string, actions: MethodValue[], before: MethodValue[],
    labelOf: (action: MethodValue) => string, nameOf: (well: ManipulatedWell) => string | undefined): StageTimeline | null {
    if (!actions.some(a => ['wait', 'thermal_profile', 'pause_review'].includes(String(a.kind)))) return null;
    const weights: number[] = [], events: Event[] = [];
    const slot = (w: number) => { weights.push(w); return weights.length - 1; };
    let group: Group | null = null;
    for (const action of actions) {
        const kind = String(action.kind), p = obj(action.params);
        if (kind === 'pipette_manual_physical' && p.operation === 'cavro_liquid_recipe') {
            const r = obj(p.recipe), channels = Array.isArray(r.channels) ? r.channels as number[] : [0];
            const src = endpoint(firstMove(r.before_leading_air) ?? firstMove(r.before_liquid) ?? {}, channels);
            const dst = endpoint(firstMove(r.before_dispense) ?? {}, channels);
            if (!src || !dst) continue;
            const volume = Number(r.target_liquid_ul);
            if (group && group.dst.station === dst.station && group.dst.well === dst.well && group.srcStation === src.station && !group.mix) {
                group.src.push(src); group.volumes.push(volume);
            } else {
                group = { slot: slot(1), src: [src], dst, volumes: [volume], mix: false, srcStation: src.station };
                events.push({ kind: 'liquid', group });
            }
        } else if (kind === 'pipette_aspirate' || kind === 'pipette_dispense') {
            if (group) group.mix = true; // in-place strokes after a transfer are its mix
        } else if (kind === 'wait') {
            group = null; events.push({ kind: 'wait', slot: slot(Math.max(1, Number(p.seconds) / 30)), seconds: Number(p.seconds), label: labelOf(action) });
        } else if (kind === 'pause_review') {
            group = null; events.push({ kind: 'pause', slot: slot(2), message: String(p.message ?? action.pause_message ?? '') });
        } else if (kind === 'chiller_setpoint' && p.bank === 'oc') {
            events.push({ kind: 'temp', at: slot(0.6), c: Number(p.target_temp_c) });
        } else if (kind === 'plate_move') {
            group = null; events.push({ kind: 'plate', slot: slot(0.6), to: PLATE_AT[String(p.target_location)] ?? String(p.target_location) });
        } else if (kind === 'move_cover' && p.cover_id === 'CV_OUTPUT') {
            group = null; events.push({ kind: 'cover', slot: slot(0.6), on: COVER_ON_OC.has(String(p.target_location)) });
        } else if (kind === 'thermal_profile') {
            group = null;
            const repeat = Math.max(1, Number(p.repeat) || 1);
            for (let i = 0; i < repeat; i++) for (const s of arr(p.segments)) {
                events.push({ kind: 'segment', slot: slot(1), c: Number(s.target_temp_c), seconds: 0, ramp: true, attainment: false });
                events.push({ kind: 'segment', slot: slot(Math.max(1, Number(s.duration_s) / 30)), c: Number(s.target_temp_c), seconds: Number(s.duration_s), ramp: false, attainment: s.start === 'attainment' });
            }
        }
    }
    const end = weights.length, markers: DeckMarker[] = [], lanes: TimelineLane[] = [];

    // Thermal cycler page: cycler + door lanes and a temperature trace.
    const segments = events.filter((e): e is Extract<Event, { kind: 'segment' }> => e.kind === 'segment');
    if (segments.length) {
        const thermal = actions.find(a => a.kind === 'thermal_profile')!;
        const repeat = Math.max(1, Number(obj(thermal.params).repeat) || 1);
        const holds = segments.filter(s => !s.ramp);
        const doorClosed = [...before, ...actions.slice(0, actions.indexOf(thermal))].filter(a => a.kind === 'thermal_door').pop();
        const closed = obj(doorClosed?.params).door_command === 'DC';
        lanes.push({ id: 'cycler', label: 'Cycler', blocks: segments.map(s => s.ramp
            ? { tone: 'ramp', title: 'Ramp', from: s.slot, to: s.slot + 1 }
            : { tone: s.c >= 60 ? 'hot' : 'heat', title: `Hold ${s.c} °C`, detail: `${clock(s.seconds)}${s.attainment ? ', timed once at temp' : ''}`, from: s.slot, to: s.slot + 1 }) });
        lanes.push({ id: 'door', label: 'Door', blocks: [{ tone: 'tick', title: closed ? 'Closed throughout · heated lid down' : 'Door open', from: segments[0].slot, to: segments[segments.length - 1].slot + 1 }] });
        markers.push({ station: 'LOC_TC', tone: 'heat', title: holds.map(h => `${h.c} °C ${clock(h.seconds)}`).join(' → ') + (closed ? ' · lid down' : '') });
        return { title: `Cycler program · ${repeat} pass`, total: `${clock(holds.reduce((t, h) => t + h.seconds, 0))} of holds, plus ramp time`, weights, lanes,
            trace: segments.map(s => ({ from: s.slot, to: s.slot + 1, c: s.c, ramp: s.ramp })), markers };
    }

    // Plate location and OC temperature through the page.
    const plateBefore = before.filter(a => a.kind === 'plate_move' && obj(a.params).plate_id === 'PL_OUTPUT').pop();
    let plateAt = plateBefore ? PLATE_AT[String(obj(plateBefore.params).target_location)] ?? 'LOC_OC' : 'LOC_OC';
    const tempBefore = before.filter(a => a.kind === 'chiller_setpoint' && obj(a.params).bank === 'oc').pop();
    const t0 = tempBefore ? Number(obj(tempBefore.params).target_temp_c) : null;
    const liquids = events.filter((e): e is Extract<Event, { kind: 'liquid' }> => e.kind === 'liquid');
    const removals = liquids.filter(e => e.group.dst.station === 'WASTE_BIN');
    const washWell = removals.length > 1 && removals.every(e => e.group.src[0].well === removals[0].group.src[0].well && e.group.src[0].station === removals[0].group.src[0].station) ? removals[0].group.src[0] : null;

    // Liquid lane: transfer groups and waits.
    const liquidBlocks: TimelineBlock[] = [];
    let outs = 0;
    const volumeText = (v: number[]) => v.length > 1 && v.every(x => x === v[0]) ? `${v.length} × ${v[0]} µL` : `${v.reduce((t, x) => t + x, 0)} µL`;
    for (const e of events) {
        if (e.kind === 'liquid') {
            const g = e.group, wash = washWell && (g.dst.station === 'WASTE_BIN' || g.dst.well === washWell.well && g.dst.station === washWell.station);
            let title: string;
            if (g.dst.station === 'WASTE_BIN') title = wash ? `Out ${++outs}` : 'Remove';
            else if (wash) title = `In ${outs + 1}`;
            else if (new Set(g.src.map(s => `${s.station}:${s.well}`)).size > 1) title = `Pool → ${g.dst.well}`;
            else title = `${nameOf(g.src[0]) ?? g.src[0].well} → ${g.dst.well}`;
            liquidBlocks.push({ tone: 'liquid', title, detail: wash ? undefined : volumeText(g.volumes) + (g.mix ? ' + mix' : ''), from: g.slot, to: g.slot + 1 });
        } else if (e.kind === 'wait') {
            const word = e.label.trim().split(/\s+/)[0] || 'Wait', short = e.seconds < 60 || /^Wash\b/i.test(e.label);
            liquidBlocks.push(short ? { tone: 'wait', title: clock(e.seconds), from: e.slot, to: e.slot + 1 }
                : { tone: 'wait', title: word, detail: clock(e.seconds) + (/room temp/i.test(e.label) ? ' · room temp' : ''), from: e.slot, to: e.slot + 1 });
        }
    }
    const pauses = events.filter((e): e is Extract<Event, { kind: 'pause' }> => e.kind === 'pause');
    const plateEvents = events.filter(e => e.kind === 'plate' || e.kind === 'cover' || e.kind === 'pause');

    // Plate lane: location segments split by operator pauses and cover moves.
    if (events.some(e => e.kind === 'plate')) {
        const blocks: TimelineBlock[] = [];
        let from = (events.find(e => e.kind === 'plate') as { slot: number }).slot, covered = false, uncovered = false, at = plateAt;
        const close = (to: number) => {
            if (to <= from) return;
            const waits = events.filter((e): e is Extract<Event, { kind: 'wait' }> => e.kind === 'wait' && e.slot >= from && e.slot < to);
            const temp = tempAt(to - 1);
            if (at === 'LOC_MS') {
                const segRemovals = removals.filter(e => e.group.slot >= from && e.group.slot < to);
                const detail = washWell ? `pellet, then washes 1–${segRemovals.length}` : segRemovals.length ? 'pellet, remove supernatant' : waits.length ? `pellet ${clock(waits[0].seconds)}` : undefined;
                blocks.push({ tone: 'mag', title: `On magnet${washWell ? ` · ${washWell.well} pellet` : ''}`, detail, from, to });
                if (waits.length) {
                    const rest = waits.slice(1);
                    markers.push({ station: 'LOC_MS', tone: 'mag', title: washWell ? `Magnet · ${segRemovals.length} washes` : 'Magnet',
                        detail: `pellet ${clock(waits[0].seconds)}${rest.length ? `, ${rest.every(w => w.seconds === rest[0].seconds) ? `${clock(rest[0].seconds)} each` : rest.map(w => clock(w.seconds)).join(', ')}` : ''}` });
                }
            } else if (at === 'LOC_OC' && covered) {
                const dry = waits.reduce((t, w) => t + w.seconds, 0);
                blocks.push({ tone: temp !== null && temp >= 30 ? 'heat' : 'cover', title: 'OC, covered', detail: `dry ${clock(dry)}${temp !== null ? ` · ${temp} °C` : ''}`, from, to });
                const later = events.find((e): e is Extract<Event, { kind: 'temp' }> => e.kind === 'temp' && e.at >= to);
                markers.push({ station: 'LOC_OC', tone: 'cover', title: `Covered dry ${clock(dry)}${temp !== null ? ` at ${temp} °C` : ''}`, detail: `then uncover${later ? `, OC to ${later.c} °C` : ''}` });
            } else blocks.push({ tone: 'tick', title: uncovered ? 'Uncover' : at === 'LOC_OC' ? 'On OC' : at === 'LOC_TC' ? 'In cycler' : 'Moved', from, to });
        };
        for (const e of plateEvents) {
            if (e.slot < from) continue;
            if (e.kind === 'pause') { close(e.slot); blocks.push({ tone: 'operator', title: 'Operator', detail: pauseText(e.message), from: e.slot, to: e.slot + 1 }); from = e.slot + 1; continue; }
            const coverAfterMove = e.kind === 'cover' && e.on && from === e.slot - 1;
            if (e.slot > from && !coverAfterMove) close(e.slot);
            if (e.kind === 'plate') at = e.to;
            if (e.kind === 'cover') { covered = e.on; uncovered = !e.on; }
            if (!(e.kind === 'cover' && e.on && blocks.length && from === e.slot - 1 && at === 'LOC_OC')) from = e.slot;
        }
        close(end);
        lanes.push({ id: 'plate', label: 'Plate', blocks: merge(blocks) });
    }
    const dryBlocks = (lanes.find(l => l.id === 'plate')?.blocks ?? []).filter(x => x.title === 'OC, covered');
    const shownLiquid = liquidBlocks.filter(x => !(x.tone === 'wait' && dryBlocks.some(d => x.from >= d.from && x.to <= d.to)));
    if (shownLiquid.length && shownLiquid[shownLiquid.length - 1].to < end) shownLiquid.push({ tone: 'tick', title: '', from: shownLiquid[shownLiquid.length - 1].to, to: end });
    lanes.push({ id: 'liquid', label: 'Liquid', blocks: shownLiquid.concat(lanes.length ? [] : pauses.map(e => ({ tone: 'operator' as const, title: 'Operator', detail: pauseText(e.message), from: e.slot, to: e.slot + 1 }))) });

    // OC temperature lane.
    function tempAt(s: number): number | null {
        let t = t0;
        for (const e of events) if (e.kind === 'temp' && e.at <= s) t = e.c;
        return t;
    }
    const temps = events.filter((e): e is Extract<Event, { kind: 'temp' }> => e.kind === 'temp');
    const plateSlots = (s: number) => { let p = plateAt; for (const e of events) if (e.kind === 'plate' && e.slot <= s) p = e.to; return p; };
    if (temps.length || (t0 !== null && liquidBlocks.some(b => b.tone === 'wait' && plateSlots(b.from) === 'LOC_OC'))) {
        const blocks: TimelineBlock[] = [];
        let t = t0, from = 0, warming = false;
        const push = (to: number) => {
            if (to <= from || t === null) return;
            const onOc = plateSlots(from) === 'LOC_OC';
            if (warming && !onOc) blocks.push({ tone: 'heat', title: `OC warming to ${t} °C${plateSlots(from) === 'LOC_MS' ? ' while the washes run' : ''}`, from, to });
            else if (t >= 30) blocks.push({ tone: 'hot', title: `${t} °C`, from, to });
            else blocks.push({ tone: 'tick', title: blocks.length ? `→ ${t} °C` : `${t} °C held`, from, to });
        };
        const cuts = [...temps.map(e => e.at), ...events.filter(e => e.kind === 'plate' && e.to === 'LOC_OC').map(e => (e as { slot: number }).slot)].sort((a, b) => a - b);
        for (const cut of cuts) {
            push(cut); if (cut > from) from = cut;
            const set = temps.filter(e => e.at === cut).pop();
            if (set) { warming = t !== null && set.c > t; t = set.c; }
            if (plateSlots(cut) === 'LOC_OC') warming = false;
        }
        push(end);
        lanes.push({ id: 'oc', label: 'OC temp', blocks: merge(blocks.filter((b, i) => !(i === 0 && b.title.endsWith('held') && temps.some(e => e.at <= b.to)))) });
        // Timed waits with the plate resting on the OC (bind, elute, cool): highlight the wells.
        const restWaits = liquidBlocks.filter(b => b.tone === 'wait' && b.detail && plateSlots(b.from) === 'LOC_OC');
        if (restWaits.length && !markers.some(m => m.station === 'LOC_OC')) {
            const w = events.find((e): e is Extract<Event, { kind: 'wait' }> => e.kind === 'wait' && e.slot === restWaits[0].from)!;
            const wells = [...new Set(liquids.filter(e => e.group.slot < w.slot && e.group.dst.station === 'LOC_OC').map(e => e.group.dst.well))];
            markers.push({ station: 'LOC_OC', tone: 'cold', title: `OC ${tempAt(w.slot)} °C` });
            markers.push({ station: 'LOC_OC', tone: 'wait', title: `${restWaits[0].title} ${clock(w.seconds)}${/room temp/i.test(w.label) ? ' at RT' : ''}`, wells });
        }
    }
    const timed = events.reduce((t, e) => t + (e.kind === 'wait' ? e.seconds : 0), 0);
    return { title: pageLabel, total: `${clock(timed)} timed${pauses.length ? ` + ${pauses.length} operator pause${pauses.length > 1 ? 's' : ''}` : ''}`, weights, lanes, markers };
}

function pauseText(message: string) {
    const m = /remove (residual|the last|last) ([^ ]+)/i.exec(message);
    return m ? `remove last ${m[2].replace(/^ethanol$/i, 'EtOH')}` : message.split(/[.,]/)[0].slice(0, 28);
}
/** Join adjacent blocks with identical text so a lane reads as segments, not slots. */
function merge(blocks: TimelineBlock[]): TimelineBlock[] {
    return blocks.reduce<TimelineBlock[]>((out, b) => {
        const last = out[out.length - 1];
        if (last && last.to === b.from && last.title === b.title && last.detail === b.detail && last.tone === b.tone) last.to = b.to; else out.push({ ...b });
        return out;
    }, []);
}
