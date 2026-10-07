import { Fragment, useRef } from 'react';
import { deckStations } from '../lib/bioxpWorkflowDeck';
import { isMethodNumber } from '../lib/bioxpMethodNumber';

export type PlateAddition = {
    path: string; name: string; source: string; wells: string[]; amount: unknown; settings?: string; editable: boolean; wellsEditable: boolean;
};

/** A presentation of the existing plate/Transfer owners, never a second preparation model. */
export function BioXpMethodPreparation({ name, station, additions, selected, onSelect, onAmount, onWells, onEdit, onAdd, onMove }: {
    name: string; station: string; additions: PlateAddition[]; selected: string;
    onSelect: (path: string) => void; onAmount: (path: string, raw: string) => void;
    onWells: (path: string, wells: string[]) => void; onEdit: (path: string) => void;
    onAdd: () => void; onMove: () => void;
}) {
    const anchor = useRef<string | null>(null);
    const resource = deckStations.find(s => s.id === station);
    const current = additions.find(a => a.path === selected) ?? additions[0];
    const wells = resource?.wells ?? [];
    const columns = [...new Set(wells.map(w => w.slice(1)))];
    const rows = [...new Set(wells.map(w => w[0]))];
    const choose = (well: string, shift: boolean) => {
        if (!current?.wellsEditable) return;
        let next = current.wells.includes(well) ? current.wells.filter(w => w !== well) : [...current.wells, well];
        if (shift && anchor.current) {
            const first = anchor.current;
            next = wells.filter(w => w[0] >= (first[0] < well[0] ? first[0] : well[0]) && w[0] <= (first[0] > well[0] ? first[0] : well[0]) && Number(w.slice(1)) >= Math.min(Number(first.slice(1)), Number(well.slice(1))) && Number(w.slice(1)) <= Math.max(Number(first.slice(1)), Number(well.slice(1))));
        }
        anchor.current = well;
        onWells(current.path, next);
    };
    return <section className="bioxp-prepare-sheet" aria-label="Plate preparation">
        <div>
            <h3>{name || 'Reaction plate'}</h3>
            <div className="bioxp-prepare-plate"><div className="bioxp-prepare-wells" style={{ gridTemplateColumns: `18px repeat(${columns.length || 12}, minmax(0,1fr))` }} role="group" aria-label="Preparation destination wells">
                <span />{columns.map(c => <span key={c}>{c}</span>)}
                {rows.map(row => <Fragment key={row}><span>{row}</span>{columns.map(c => {
                    const well = row + c;
                    return <button key={well} type="button" aria-label={`Preparation well ${well}`} aria-pressed={current?.wells.includes(well) ?? false} disabled={!current?.wellsEditable || !wells.includes(well)} onClick={e => choose(well, e.shiftKey)} title={well} />;
                })}</Fragment>)}
            </div></div>
            <div className="bioxp-prepare-welltools"><span>{current?.wells.join(', ') || 'Choose an addition, then its wells'}</span><button type="button" disabled={!current?.wellsEditable} onClick={() => current && onWells(current.path, [])}>Clear wells</button></div>
            <p>Choose an addition, then its destination wells. Shift-click selects a rectangle.</p>
            <div className="bioxp-prepare-actions"><button type="button" onClick={onAdd}>＋ Add reagent</button><button type="button" onClick={onMove}>Move this plate</button></div>
        </div>
        <div><h3>Add to selected wells <span>µL per plunger</span></h3>
            {additions.map((addition, i) => {
                const amount = isMethodNumber(addition.amount) ? addition.amount.expr.value : typeof addition.amount === 'string' || typeof addition.amount === 'number' ? String(addition.amount) : '';
                const amountEditable = addition.amount === undefined || typeof addition.amount === 'string' || typeof addition.amount === 'number' || isMethodNumber(addition.amount);
                return <div key={addition.path} className={`bioxp-prepare-addition${current?.path === addition.path ? ' is-selected' : ''}`}>
                    <span className="bioxp-addition-letter">{String.fromCharCode(65 + i % 26)}</span>
                    <button className="bioxp-addition-name" type="button" aria-label={`Select addition ${addition.name}`} onClick={() => onSelect(addition.path)}>{addition.name}<small>{addition.source} → {addition.wells.join(', ') || 'No destination wells'}</small><small>{addition.settings}</small></button>
                    <label className="bioxp-addition-amount"><input aria-label={`${addition.name} preparation volume (µL)`} value={amount} inputMode="decimal" placeholder="—" disabled={!addition.editable || !amountEditable} onChange={e => onAmount(addition.path, e.target.value)} /><span>µL</span></label>
                    <button className="bioxp-addition-open" type="button" aria-label={`Edit ${addition.name} transfer`} onClick={() => onEdit(addition.path)}>↗</button>
                </div>;
            })}
            {!additions.length && <p>No additions yet. Add a reagent to choose its source, wells and amount.</p>}
        </div>
    </section>;
}
