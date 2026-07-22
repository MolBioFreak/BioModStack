import { useMemo, useState } from 'react';

import type { ViewerMeasurement } from '../../contracts/measurements.js';
import type { AtomRef } from '../../contracts/structureIdentity.js';

interface MeasurementPointDraft {
    readonly chain: string;
    readonly residue: string;
    readonly insertionCode: string;
    readonly atom: string;
}

export interface MeasurementPanelProps {
    readonly documentId: string;
    readonly measurements: readonly ViewerMeasurement[];
    readonly onChange: (measurements: readonly ViewerMeasurement[]) => void;
}

const EMPTY_POINT: MeasurementPointDraft = { chain: '', residue: '', insertionCode: '', atom: '' };
const REQUIRED_POINTS = { distance: 2, angle: 3, dihedral: 4 } as const;

const pointLabel = (point: AtomRef): string => (
    `${point.authAsymId ?? point.labelAsymId ?? '?'}:${point.authSeqId ?? point.labelSeqId ?? '?'}${point.insertionCode ?? ''}:${point.authAtomId ?? point.labelAtomId ?? '?'}`
);

export function MeasurementPanel({ documentId, measurements, onChange }: MeasurementPanelProps) {
    const [type, setType] = useState<ViewerMeasurement['type']>('distance');
    const [label, setLabel] = useState('');
    const [points, setPoints] = useState<readonly MeasurementPointDraft[]>([
        { ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT },
    ]);
    const [error, setError] = useState<string | null>(null);
    const required = REQUIRED_POINTS[type];
    const activePoints = useMemo(() => points.slice(0, required), [points, required]);

    const updatePoint = (index: number, patch: Partial<MeasurementPointDraft>) => {
        setPoints(points.map((point, pointIndex) => pointIndex === index ? { ...point, ...patch } : point));
    };
    const addMeasurement = () => {
        const atoms: AtomRef[] = [];
        for (const [index, point] of activePoints.entries()) {
            const residue = Number(point.residue);
            if (!point.chain.trim() || !Number.isInteger(residue) || !point.atom.trim()) {
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
            measurementId: `operator-${crypto.randomUUID()}`,
            label: label.trim() || undefined,
            provenanceRef: `operator-authored:${new Date().toISOString()}`,
        };
        const measurement: ViewerMeasurement = type === 'distance'
            ? { ...base, type, points: [atoms[0]!, atoms[1]!] }
            : type === 'angle'
                ? { ...base, type, points: [atoms[0]!, atoms[1]!, atoms[2]!] }
                : { ...base, type, points: [atoms[0]!, atoms[1]!, atoms[2]!, atoms[3]!] };
        onChange([...measurements, measurement]);
        setLabel('');
        setPoints([{ ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT }, { ...EMPTY_POINT }]);
        setError(null);
    };

    return (
        <details className="rounded border border-violet-700/70 bg-slate-900/95 p-2 text-xs text-slate-200">
            <summary className="cursor-pointer font-semibold text-violet-200">Exact atom measurements ({measurements.length})</summary>
            <div className="mt-2 grid grid-cols-2 gap-2">
                <label>Type
                    <select className="mt-1 w-full rounded bg-slate-800 p-1" value={type} onChange={(event) => setType(event.target.value as ViewerMeasurement['type'])}>
                        <option value="distance">Distance</option>
                        <option value="angle">Angle</option>
                        <option value="dihedral">Dihedral</option>
                    </select>
                </label>
                <label>Label
                    <input className="mt-1 w-full rounded bg-slate-800 p-1" value={label} onChange={(event) => setLabel(event.target.value)} placeholder="Optional" />
                </label>
            </div>
            <div className="mt-2 space-y-1">
                {activePoints.map((point, index) => (
                    <div key={index} className="grid grid-cols-[1fr_1fr_.7fr_1fr] gap-1" aria-label={`Measurement point ${index + 1}`}>
                        <input aria-label={`Point ${index + 1} author chain`} className="rounded bg-slate-800 p-1" value={point.chain} onChange={(event) => updatePoint(index, { chain: event.target.value })} placeholder="Chain" />
                        <input aria-label={`Point ${index + 1} author residue`} className="rounded bg-slate-800 p-1" inputMode="numeric" value={point.residue} onChange={(event) => updatePoint(index, { residue: event.target.value })} placeholder="Residue" />
                        <input aria-label={`Point ${index + 1} insertion code`} className="rounded bg-slate-800 p-1" value={point.insertionCode} onChange={(event) => updatePoint(index, { insertionCode: event.target.value })} placeholder="Ins" />
                        <input aria-label={`Point ${index + 1} author atom`} className="rounded bg-slate-800 p-1" value={point.atom} onChange={(event) => updatePoint(index, { atom: event.target.value })} placeholder="Atom" />
                    </div>
                ))}
            </div>
            {error && <div role="alert" className="mt-2 text-red-300">{error}</div>}
            <button type="button" onClick={addMeasurement} className="mt-2 rounded bg-violet-700 px-2 py-1 font-semibold hover:bg-violet-600">Add measurement</button>
            {measurements.length > 0 && (
                <ul className="mt-2 space-y-1">
                    {measurements.map((measurement) => (
                        <li key={measurement.measurementId} className="flex items-start justify-between gap-2 rounded bg-slate-950/70 px-2 py-1">
                            <span>
                                <span className="font-semibold">{measurement.label || measurement.type}</span>
                                <span className="block text-[10px] text-slate-400">{measurement.points.map(pointLabel).join(' → ')}</span>
                            </span>
                            <button type="button" onClick={() => onChange(measurements.filter((entry) => entry.measurementId !== measurement.measurementId))} className="text-red-300 hover:text-red-200">Remove</button>
                        </li>
                    ))}
                </ul>
            )}
        </details>
    );
}
