import React, { useRef, useState } from 'react';
import { bioXpErrorText, useBioXpDeckState, useBioXpTipTrays, useRecordBioXpDeck, type BioXpDeckRecordResultV1 } from '../lib/bioxpClient';
import {
    COVER_LOCATIONS, DECK_TIP_TRAYS, OBJECT_LABELS, ROWS, coverLocation, deckRecordBody, gripperObject, locationLabel,
    objectLabel, tipCounts, wellName, type DeckRecordDraft,
} from '../lib/bioxpDeckRecord';

export type BioXpDeckRecordPanelProps = { generation: number; connected: boolean; visible?: boolean; compact?: boolean };

const emptyDraft = (): DeckRecordDraft => ({ covers: {}, tips: {}, note: '' });
const selectClass = 'rounded border border-[var(--border-secondary)] bg-[var(--bg-primary)] px-1 py-0.5 text-xs';

function WellPicker({ tray, wells, onChange }: { tray: number; wells: string[]; onChange: (next: string[]) => void }) {
    const set = new Set(wells);
    const toggle = (well: string) => onChange(Array.from({ length: 96 }, (_, i) => wellName(i)).filter(w => w === well ? !set.has(w) : set.has(w)));
    return <div role="group" aria-label={`Tip rack ${tray} wells`} className="mt-1 inline-grid gap-px text-[9px]"
        style={{ gridTemplateColumns: '14px repeat(12, 16px)' }}>
        <span />{Array.from({ length: 12 }, (_, c) => <span key={c} className="text-center text-[var(--text-secondary)]">{c + 1}</span>)}
        {ROWS.split('').map(row => <React.Fragment key={row}>
            <span className="text-[var(--text-secondary)]">{row}</span>
            {Array.from({ length: 12 }, (_, c) => {
                const well = `${row}${c + 1}`;
                return <button key={well} type="button" aria-label={`Tip ${well}`} aria-pressed={set.has(well)} title={well}
                    onClick={() => toggle(well)}
                    className={`h-4 w-4 rounded-full border border-[var(--border-secondary)] p-0 ${set.has(well) ? 'bg-[var(--accent-primary,#0f766e)]' : 'bg-transparent'}`} />;
            })}
        </React.Fragment>)}
    </div>;
}

/** What the robot believes is on the deck, with a record-only correction. Nothing moves. */
export function BioXpDeckRecordPanel({ generation, connected, visible = true, compact = false }: BioXpDeckRecordPanelProps) {
    const enabled = connected && visible;
    const state = useBioXpDeckState(generation, enabled);
    const trays = useBioXpTipTrays(generation, enabled);
    const record = useRecordBioXpDeck();
    const [draft, setDraft] = useState<DeckRecordDraft>(emptyDraft);
    const [result, setResult] = useState<BioXpDeckRecordResultV1 | null>(null);
    // One POST per click; mutation state updates a render late.
    const sending = useRef(false);
    const deck = state.data, tipData = trays.data;
    const held = gripperObject(deck);
    const body = deckRecordBody(generation, draft, deck, tipData);
    const occupancyWells = (tray: number) => {
        const occupancy = tipData?.trays.find(row => row.tray === tray)?.occupancy;
        return Array.isArray(occupancy) ? occupancy.flatMap((tip, i) => tip ? [wellName(i)] : []) : [];
    };
    const setTips = (tray: number, value: string) => setDraft(d => ({ ...d, tips: { ...d.tips,
        [tray]: value === '' ? undefined : value === 'pick' ? (Array.isArray(d.tips[tray]) ? d.tips[tray] : occupancyWells(tray)) : value as 'full' | 'empty' } }));
    const refresh = () => { void state.refetch(); void trays.refetch(); };
    const save = () => {
        if (!body || record.isPending || sending.current) return;
        sending.current = true;
        setResult(null);
        record.mutate(body, {
            onSuccess: data => { setResult(data); setDraft(emptyDraft()); refresh(); },
            onSettled: () => { sending.current = false; },
        });
    };
    const cell = compact ? 'px-1 py-0.5' : 'px-2 py-1';

    return <section aria-label="Deck record" data-testid="deck-record-panel"
        className={`rounded-lg border border-[var(--border-secondary)] bg-[var(--bg-secondary)] ${compact ? 'p-2 text-xs' : 'p-3 text-sm'}`}>
        <header className="mb-2 flex flex-wrap items-baseline justify-between gap-2">
            <h3 className="font-semibold">Robot believes</h3>
            <button type="button" className="text-xs underline" onClick={refresh} disabled={!enabled}>Refresh</button>
        </header>
        {!connected && <p role="status" className="text-xs text-[var(--text-secondary)]">Connect to read the deck record.</p>}
        {(state.isError || trays.isError) && <p role="alert" className="text-xs">
            Deck record unavailable: {bioXpErrorText(state.error ?? trays.error)}</p>}
        <table className="w-full border-collapse text-left">
            <thead><tr className="text-[var(--text-secondary)]"><th className={cell}>Item</th><th className={cell}>Now</th><th className={cell}>Set to</th></tr></thead>
            <tbody>
                {(['OUTPUT_COVER', 'REAGENT_COVER'] as const).map(name => <tr key={name} data-row={name}>
                    <th scope="row" className={`${cell} font-normal`}>{objectLabel(name)}</th>
                    <td className={cell}>{deck ? locationLabel(coverLocation(deck, name)) : '—'}</td>
                    <td className={cell}><select aria-label={`Set ${objectLabel(name)} to`} className={selectClass} value={draft.covers[name] ?? ''}
                        onChange={e => setDraft(d => ({ ...d, covers: { ...d.covers, [name]: e.target.value || undefined } }))}>
                        <option value="">No change</option>
                        {COVER_LOCATIONS[name].map(location => <option key={location} value={location}>{locationLabel(location)}</option>)}
                    </select></td>
                </tr>)}
                <tr data-row="GRIPPER">
                    <th scope="row" className={`${cell} font-normal`}>Gripper</th>
                    <td className={cell}>{held === undefined ? '—' : held === null ? 'Empty' : `Holding ${objectLabel(held)}`}</td>
                    <td className={cell}><select aria-label="Set Gripper to" className={selectClass} value={draft.gripper ?? ''}
                        onChange={e => setDraft(d => ({ ...d, gripper: e.target.value || undefined }))}>
                        <option value="">No change</option>
                        <option value="empty">Empty</option>
                        {Object.keys(OBJECT_LABELS).map(name => <option key={name} value={name}>Holding {objectLabel(name)}</option>)}
                    </select></td>
                </tr>
                {DECK_TIP_TRAYS.map(tray => {
                    const row = tipData?.trays.find(r => r.tray === tray);
                    const { tips, pickups } = tipCounts(row);
                    const want = draft.tips[tray];
                    return <tr key={tray} data-row={`TIPS_${tray}`}>
                        <th scope="row" className={`${cell} align-top font-normal`}>Tips {tray}</th>
                        <td className={`${cell} align-top`} data-testid={`tips-${tray}`}>{row?.unavailable ? 'Unavailable'
                            : tips === null ? '—' : `${tips}/96 tips · ${pickups ?? '?'} pickups`}</td>
                        <td className={`${cell} align-top`}>
                            <select aria-label={`Set Tips ${tray} to`} className={selectClass} value={Array.isArray(want) ? 'pick' : want ?? ''}
                                onChange={e => setTips(tray, e.target.value)}>
                                <option value="">No change</option><option value="full">Full</option>
                                <option value="empty">Empty</option><option value="pick">Pick wells</option>
                            </select>
                            {Array.isArray(want) && <WellPicker tray={tray} wells={want}
                                onChange={next => setDraft(d => ({ ...d, tips: { ...d.tips, [tray]: next } }))} />}
                        </td>
                    </tr>;
                })}
            </tbody>
        </table>
        <label className="mt-2 block text-xs">Note (optional)
            <input type="text" maxLength={500} value={draft.note} onChange={e => setDraft(d => ({ ...d, note: e.target.value }))}
                className="mt-1 block w-full rounded border border-[var(--border-secondary)] bg-[var(--bg-primary)] px-2 py-1" />
        </label>
        <div className="mt-2 flex flex-wrap items-center gap-3">
            <button type="button" className="rounded bg-[var(--surface-control,var(--bg-tertiary))] px-3 py-1.5 font-semibold disabled:opacity-40"
                disabled={!connected || !body || record.isPending} onClick={save}>{record.isPending ? 'Saving…' : 'Save record'}</button>
            <span className="text-xs text-[var(--text-secondary)]">Record only. Nothing moves.</span>
        </div>
        {record.isError && <p role="alert" className="mt-1 text-xs">{bioXpErrorText(record.error)}</p>}
        {result && <div role="status" className="mt-1 text-xs">
            <p>Record saved.</p>
            {(result.warnings ?? []).map((warning, i) => <p key={i}>⚠ {warning.message ?? warning.code}{warning.objects?.length ? ` (${warning.objects.map(objectLabel).join(', ')})` : ''}</p>)}
        </div>}
    </section>;
}
