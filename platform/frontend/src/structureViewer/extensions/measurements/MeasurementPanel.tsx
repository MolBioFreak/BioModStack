import { useMemo, useState } from 'react';

import type { MeasurementResult, ViewerMeasurement } from '../../contracts/measurements.js';
import type { ViewerResult } from '../../contracts/viewerResults';
import type { AtomRef } from '../../contracts/structureIdentity.js';

interface MeasurementPointDraft {
    readonly chain: string;
    readonly residue: string;
    readonly insertionCode: string;
    readonly atom: string;
}

export interface MeasurementPanelProps {
    readonly documentId: string;
    readonly results: readonly MeasurementResult[];
    readonly adoptSelection?: () => ViewerResult<readonly AtomRef[]> | undefined;
    readonly measurements: readonly ViewerMeasurement[];
    readonly onChange: (measurements: readonly ViewerMeasurement[]) => void;
}

const EMPTY_POINT: MeasurementPointDraft = { chain: '', residue: '', insertionCode: '', atom: '' };
const REQUIRED_POINTS = { distance: 2, angle: 3, dihedral: 4 } as const;

const pointLabel = (point: AtomRef): string => (
    `${point.authAsymId ?? point.labelAsymId ?? '?'}:${point.authSeqId ?? point.labelSeqId ?? '?'}${point.insertionCode ?? ''}:${point.authAtomId ?? point.labelAtomId ?? '?'}`
);

export function MeasurementPanel({ documentId, measurements, results, adoptSelection, onChange }: MeasurementPanelProps) {
    const [type, setType] = useState<ViewerMeasurement['type']>('distance');
    const [label, setLabel] = useState('');
    const [points, setPoints] = useState<readonly MeasurementPointDraft[]>([
        { ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT },
    ]);
    const [editing, setEditing] = useState<ViewerMeasurement | null>(null);
    const [adopted, setAdopted] = useState<readonly AtomRef[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const currentResults = results.filter(result => measurements.some(measurement => JSON.stringify(measurement) === JSON.stringify(result.measurement)));
    const required = REQUIRED_POINTS[type];
    const activePoints = useMemo(() => points.slice(0, required), [points, required]);

    const updatePoint = (index: number, patch: Partial<MeasurementPointDraft>) => {
        setAdopted(null);
        setPoints(points.map((point, pointIndex) => pointIndex === index ? { ...point, ...patch } : point));
    };
    const addMeasurement = () => {
        const atoms: AtomRef[] = [];
        if (adopted?.length === required) atoms.push(...adopted);
        else for (const [index, point] of activePoints.entries()) {
            const residue = Number(point.residue);
            if (!point.chain.trim() || !point.residue.trim() || !Number.isInteger(residue) || !point.atom.trim()) {
                setError(`Point ${index + 1} requires author chain, integer residue number, and atom name.`);
                return;
            }
            atoms.push({
                documentId,
                authAsymId: point.chain.trim(),
                authSeqId: residue,
                insertionCode: point.insertionCode.trim() || undefined,
                authAtomId: point.atom.trim(),
            });
        }
        const base = {
            measurementId: editing?.measurementId ?? `operator-${crypto.randomUUID()}`,
            label: label.trim() || undefined,
            provenanceRef: editing?.provenanceRef ?? `operator-authored:${new Date().toISOString()}`,
        };
        const measurement: ViewerMeasurement = type === 'distance'
            ? { ...base, type, points: [atoms[0]!, atoms[1]!] }
            : type === 'angle'
                ? { ...base, type, points: [atoms[0]!, atoms[1]!, atoms[2]!] }
                : { ...base, type, points: [atoms[0]!, atoms[1]!, atoms[2]!, atoms[3]!] };
        onChange(editing ? measurements.map(entry => entry.measurementId === editing.measurementId ? measurement : entry) : [...measurements, measurement]);
        setEditing(null);
        setLabel('');
        setAdopted(null);
        setPoints([{ ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT }]);
        setError(null);
    };

    return (
        <details className="rounded border border-violet-700/70 bg-slate-900/95 p-2 text-xs text-slate-200">
            <summary className="cursor-pointer font-semibold text-violet-200">Exact atom measurements ({currentResults.filter(result => result.status === 'computed').length} computed)</summary>
            <div className="mt-2 grid grid-cols-2 gap-2">
                <label>Type
                    <select className="mt-1 min-w-0 w-full rounded bg-slate-800 p-1" value={type} onChange={(event) => { setType(event.target.value as ViewerMeasurement['type']); setAdopted(null); }}>
                        <option value="distance">Distance</option>
                        <option value="angle">Angle</option>
                        <option value="dihedral">Dihedral</option>
                    </select>
                </label>
                <label>Label
                    <input className="mt-1 min-w-0 w-full rounded bg-slate-800 p-1" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Optional" />
                </label>
            </div>
            <p className="mt-2 text-slate-400">Exact atoms only. Native Mol* Measurements remain separate and may use residue or multi-atom centers.</p>
            <button type="button" className="mt-2 rounded border border-slate-600 px-2 py-1" onClick={() => {
                const result = adoptSelection?.();
                if (!result || result.status !== 'ok') { setError(!result ? 'Viewer is not ready' : result.status === 'error' ? result.error.message : result.reason); return; }
                if (result.value.length !== required) { setError(`Select exactly ${required} individual atoms in native selection history (most recent first).`); return; }
                setAdopted(result.value);
                setPoints([...result.value.map(point => ({ chain: point.authAsymId ?? '', residue: String(point.authSeqId ?? ''), insertionCode: point.insertionCode ?? '', atom: point.authAtomId ?? '' })), ...Array.from({length: 4 - required}, () => ({...EMPTY_POINT}))]);
                setError(null);
            }}>Adopt exact atom selections</button>
            {adopted && <p className="mt-1 text-slate-400">Adopted full identities (most recent first), including alternate location. Editing a field returns to manual author identity.</p>}
            <div className="mt-2 space-y-1">
                {activePoints.map((point, index) => (
                    <div key={index} className="grid grid-cols-2 sm:grid-cols-2 gap-1" aria-label={`Measurement point ${index + 1}`}>
                        <label className="min-w-0">Author chain<input aria-label={`Point ${index + 1} author chain`} className="min-w-0 w-full rounded bg-slate-800 p-1" value={point.chain} onChange={(event) => updatePoint(index, { chain: event.target.value })} placeholder="Chain" /></label>
                        <label className="min-w-0">Author residue<input aria-label={`Point ${index + 1} author residue`} className="min-w-0 w-full rounded bg-slate-800 p-1" inputMode="numeric" value={point.residue} onChange={(event) => updatePoint(index, { residue: event.target.value })} placeholder="Residue" /></label>
                        <label className="min-w-0">Insertion code<input aria-label={`Point ${index + 1} insertion code`} className="min-w-0 w-full rounded bg-slate-800 p-1" value={point.insertionCode} onChange={(event) => updatePoint(index, { insertionCode: event.target.value })} placeholder="Ins" /></label>
                        <label className="min-w-0">Author atom<input aria-label={`Point ${index + 1} author atom`} className="min-w-0 w-full rounded bg-slate-800 p-1" value={point.atom} onChange={(event) => updatePoint(index, { atom: event.target.value })} placeholder="Atom" /></label>
                    </div>
                ))}
            </div>
            {error && <div role="alert" className="mt-2 text-red-300">{error}</div>}
            <button type="button" onClick={addMeasurement} className="mt-2 rounded bg-violet-700 px-2 py-1 font-semibold hover:bg-violet-600">{editing ? 'Update measurement' : 'Add measurement'}</button>
            {measurements.length > 0 && (
                <ul className="mt-2 max-h-64 space-y-1 overflow-auto">
                    {measurements.map((measurement) => (
                        <li key={measurement.measurementId} className="flex flex-wrap items-start justify-between gap-2 rounded bg-slate-950/70 px-2 py-1">
                            <span className="min-w-0 flex-1 basis-40">
                                <span className="font-semibold">{measurement.label || measurement.type}</span>
                                <span className="block break-words text-[10px] text-slate-400">{measurement.points.map(pointLabel).join(' → ')}</span>
                                <span className="block" role="status">{(() => {
                                    const result = currentResults.find(result => result.measurement.measurementId === measurement.measurementId);
                                    return result?.status === 'computed' ? `${result.value} ${result.units === 'degrees' ? '°' : result.units}` : result?.reason ?? 'Pending native computation';
                                })()}</span>
                            </span>
                            <button type="button" className="text-blue-300" onClick={() => {
                                setEditing(measurement); setType(measurement.type); setLabel(measurement.label ?? ''); setAdopted(measurement.points);
                                setPoints([...measurement.points.map(point => ({ chain: point.authAsymId ?? point.labelAsymId ?? '', residue: String(point.authSeqId ?? point.labelSeqId ?? ''), insertionCode: point.insertionCode ?? '', atom: point.authAtomId ?? point.labelAtomId ?? '' })), ...Array.from({length: 4 - measurement.points.length}, () => ({...EMPTY_POINT}))]); setError(null);
                            }}>Edit</button>
                            <button type="button" onClick={() => onChange(measurements.filter((entry) => entry.measurementId !== measurement.measurementId))} className="text-red-300 hover:text-red-200">Remove</button>
                        </li>
                    ))}
                </ul>
            )}
        </details>
    );
}
