import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
    listShapeGeometries,
    submitShapeBlueprint,
    uploadShapeGeometry,
    type ShapeGeometrySummary,
} from '../lib/api';
import CanonicalMeshPreview from './CanonicalMeshPreview';
import MolstarViewer from './MolstarViewer';

const shortHash = (value: string) => `${value.slice(0, 12)}…${value.slice(-8)}`;
const boundedInteger = (value: string, fallback: number, minimum: number, maximum: number) => {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) return fallback;
    return Math.min(maximum, Math.max(minimum, Math.trunc(parsed)));
};
const formatAngstrom = (value: number) => Math.abs(value) >= 1_000_000
    ? `${value.toExponential(3)} Å`
    : `${value.toLocaleString(undefined, { maximumFractionDigits: 3 })} Å`;
const requestError = (cause: unknown, fallback: string) => {
    if (cause && typeof cause === 'object' && 'response' in cause) {
        const response = (cause as { response?: { data?: { detail?: string | { message?: string } } } }).response;
        const detail = response?.data?.detail;
        if (typeof detail === 'string') return detail;
        if (detail?.message) return detail.message;
    }
    return cause instanceof Error ? cause.message : fallback;
};

const SHAPE_CLIENT_REQUEST_KEY = 'bms.shape-blueprint.client-request-id';

const getShapeClientRequestId = () => {
    const existing = sessionStorage.getItem(SHAPE_CLIENT_REQUEST_KEY);
    if (existing) return existing;
    const created = crypto.randomUUID();
    sessionStorage.setItem(SHAPE_CLIENT_REQUEST_KEY, created);
    return created;
};

export default function ShapeBlueprintTemplate() {
    const navigate = useNavigate();
    const queryClient = useQueryClient();
    const [selectedId, setSelectedId] = useState('');
    const [clientRequestId] = useState(getShapeClientRequestId);
    const [file, setFile] = useState<File | null>(null);
    const [unit, setUnit] = useState('angstrom');
    const [name, setName] = useState('Shape Blueprint design');
    const [targetLength, setTargetLength] = useState(120);
    const [numBackbones, setNumBackbones] = useState(1);
    const [sequencesPerBackbone, setSequencesPerBackbone] = useState(1);
    const [seed, setSeed] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [reviewMode, setReviewMode] = useState<'surface' | 'points'>('surface');

    const geometriesQuery = useQuery({
        queryKey: ['shape-geometries'],
        queryFn: () => listShapeGeometries().then((response) => response.data.geometries),
    });
    const geometries = geometriesQuery.data ?? [];
    const selected = useMemo<ShapeGeometrySummary | undefined>(
        () => geometries.find((geometry) => geometry.geometry_id === selectedId) ?? geometries[0],
        [geometries, selectedId],
    );
    const selectedMaxDimension = selected ? Math.max(...selected.dimensions_angstrom) : null;

    const upload = useMutation({
        mutationFn: () => {
            if (!file) throw new Error('Choose a closed triangular OBJ or STL first.');
            return uploadShapeGeometry(file, unit);
        },
        onSuccess: async (response) => {
            setSelectedId(response.data.geometry_id);
            setFile(null);
            setError(null);
            await queryClient.invalidateQueries({ queryKey: ['shape-geometries'] });
        },
        onError: (cause: unknown) => setError(requestError(cause, 'Geometry admission failed.')),
    });

    const launch = useMutation({
        mutationFn: () => {
            if (!selected) throw new Error('Select or upload canonical geometry first.');
            return submitShapeBlueprint({
                client_request_id: clientRequestId,
                name: name.trim() || 'Shape Blueprint design',
                geometry_id: selected.geometry_id,
                expected_geometry_sha256: selected.geometry_sha256,
                expected_point_pool_sha256: selected.point_pool_sha256,
                target_length: targetLength,
                num_backbones: numBackbones,
                sequences_per_backbone: sequencesPerBackbone,
                seed,
            });
        },
        onSuccess: (response) => {
            sessionStorage.removeItem(SHAPE_CLIENT_REQUEST_KEY);
            navigate(`/designs/${response.data.job_id}`);
        },
        onError: (cause: unknown) => setError(requestError(cause, 'Shape request failed.')),
    });

    return (
        <div className="mx-auto max-w-7xl space-y-5 p-4 sm:p-6">
            <header className="rounded-2xl border border-cyan-500/20 bg-slate-950/80 p-5">
                <p className="text-xs font-semibold uppercase tracking-[0.2em] text-cyan-300">Shape Blueprint</p>
                <h1 className="mt-1 text-2xl font-semibold text-white">Canonical shape-guided protein design</h1>
                <p className="mt-2 max-w-4xl text-sm leading-6 text-slate-300">
                    Immutable CAD mesh → canonical surface, points, and signed-distance field → bounded Shape request → artifact-bound result review.
                </p>
            </header>

            {error && <div className="rounded-xl border border-red-500/40 bg-red-500/10 p-3 text-sm text-red-200">{error}</div>}

            <div className="grid gap-5 lg:grid-cols-[390px_minmax(0,1fr)]">
                <section className="space-y-4 rounded-2xl border border-slate-800 bg-slate-900/80 p-4">
                    <div>
                        <h2 className="font-semibold text-white">1. Admit or select geometry</h2>
                        <p className="mt-1 text-xs text-slate-400">Upload one closed triangular OBJ or 3D-print STL (ASCII or binary). Admission rejects holes, non-manifold topology, self-intersections, and disconnected bodies.</p>
                    </div>
                    <input type="file" accept=".obj,.stl" onChange={(event) => {
                        const selectedFile = event.target.files?.[0] ?? null;
                        setFile(selectedFile);
                    }} className="block w-full text-xs text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-cyan-600 file:px-3 file:py-2 file:text-white" />
                    {file?.name.toLowerCase().endsWith('.stl') && <div className="rounded-lg border border-amber-500/30 bg-amber-500/10 p-2 text-xs leading-5 text-amber-100"><strong>Confirm source units.</strong> STL files do not encode units. For protein-scale shape borrowing, the default treats each STL coordinate unit as 1 Å; literal millimeter scaling is usually far too large.</div>}
                    <div className="grid grid-cols-[1fr_auto] gap-2">
                        <select value={unit} onChange={(event) => setUnit(event.target.value)} className="rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white">
                            <option value="angstrom">Ångström (1 mesh unit = 1 Å)</option><option value="nanometer">Nanometer</option><option value="micrometer">Micrometer</option><option value="millimeter">Millimeter</option><option value="centimeter">Centimeter</option><option value="inch">Inch</option>
                        </select>
                        <button type="button" disabled={!file || upload.isPending} onClick={() => upload.mutate()} className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{upload.isPending ? 'Validating mesh…' : 'Admit mesh'}</button>
                    </div>
                    <select value={selected?.geometry_id ?? ''} onChange={(event) => setSelectedId(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white">
                        {!geometries.length && <option value="">No admitted geometry</option>}
                        {geometries.map((geometry) => <option key={geometry.geometry_id} value={geometry.geometry_id}>{geometry.source_format.toUpperCase()} · {geometry.geometry_id}</option>)}
                    </select>

                    <div className="border-t border-slate-800 pt-4">
                        <h2 className="font-semibold text-white">2. Launch settings</h2>
                        <label className="mt-3 block text-xs text-slate-400">Job name<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                        <div className="mt-3 grid grid-cols-2 gap-3">
                            <label className="text-xs text-slate-400">Target length<input type="number" min={40} max={600} value={targetLength} onChange={(event) => setTargetLength(boundedInteger(event.target.value, 120, 40, 600))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                            <label className="text-xs text-slate-400">RFD3 backbones<input type="number" min={1} max={32} value={numBackbones} onChange={(event) => setNumBackbones(boundedInteger(event.target.value, 1, 1, 32))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                            <label className="text-xs text-slate-400">Sequences / lane<input type="number" min={1} max={8} value={sequencesPerBackbone} onChange={(event) => setSequencesPerBackbone(boundedInteger(event.target.value, 1, 1, 8))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                            <label className="text-xs text-slate-400">Deterministic seed<input type="number" min={0} max={2147483647} value={seed} onChange={(event) => setSeed(boundedInteger(event.target.value, 0, 0, 2147483647))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                        </div>
                    </div>
                    <button type="button" disabled={!selected || launch.isPending} onClick={() => launch.mutate()} className="w-full rounded-xl bg-emerald-600 px-4 py-3 font-semibold text-white hover:bg-emerald-500 disabled:opacity-40">{launch.isPending ? 'Staging immutable request…' : 'Launch Shape Blueprint'}</button>
                </section>

                <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80">
                    <div className="border-b border-slate-800 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div><h2 className="font-semibold text-white">Canonical geometry review</h2><p className="mt-1 text-xs text-slate-400">Review the exact server-canonicalized surface or hash-bound point pool—not the raw upload or a browser reconstruction.</p></div>
                            <div className="flex rounded-lg border border-slate-700 p-1 text-xs">
                                <button type="button" onClick={() => setReviewMode('surface')} className={`rounded px-3 py-1 ${reviewMode === 'surface' ? 'bg-cyan-600 text-white' : 'text-slate-300'}`}>Surface</button>
                                <button type="button" onClick={() => setReviewMode('points')} className={`rounded px-3 py-1 ${reviewMode === 'points' ? 'bg-cyan-600 text-white' : 'text-slate-300'}`}>Points</button>
                            </div>
                        </div>
                    </div>
                    {selected ? (reviewMode === 'surface'
                        ? <CanonicalMeshPreview url={`/api/shape-blueprint/geometries/${selected.geometry_id}/preview.obj`} height={430} label="Canonical Shape surface" />
                        : <MolstarViewer structureUrl={`/api/shape-blueprint/geometries/${selected.geometry_id}/points.cif`} format="cif" height={430} label="Canonical Shape point pool" />)
                        : <div className="flex h-[430px] items-center justify-center text-sm text-slate-500">Select geometry to preview</div>}
                    {selected && <div className="grid gap-2 border-t border-slate-800 p-4 text-xs text-slate-300 sm:grid-cols-2">
                        <div>Source <span className="font-semibold text-white">{selected.source_format.toUpperCase()}</span> · {selected.source_parser.replaceAll('_', ' ')}</div>
                        <div>Units <span className="font-semibold text-white">{selected.source_unit}</span> · {selected.angstrom_per_unit.toExponential(3)} Å/unit</div>
                        <div className="sm:col-span-2">Dimensions <span className="font-mono text-cyan-200">{selected.dimensions_angstrom.map(formatAngstrom).join(' × ')}</span></div>
                        <div>Source bytes <span className="font-mono text-cyan-200" title={selected.source_sha256}>{shortHash(selected.source_sha256)}</span></div>
                        <div>Geometry <span className="font-mono text-cyan-200" title={selected.geometry_sha256}>{shortHash(selected.geometry_sha256)}</span></div>
                        <div>Points <span className="font-mono text-cyan-200" title={selected.point_pool_sha256}>{shortHash(selected.point_pool_sha256)}</span></div>
                        <div>SDF <span className="font-mono text-cyan-200" title={selected.sdf_sha256}>{shortHash(selected.sdf_sha256)}</span></div>
                        <div>Convention <span className="text-emerald-300">{selected.sdf_sign}</span> • {selected.sdf_grid_shape.join('×')}</div>
                        <div>{selected.vertex_count.toLocaleString()} vertices • {selected.face_count.toLocaleString()} faces</div>
                        <div>{selected.point_count.toLocaleString()} deterministic points</div>
                    </div>}
                    {selectedMaxDimension !== null && (selectedMaxDimension > 1_000 || selectedMaxDimension < 5) && <div className="border-t border-amber-500/20 bg-amber-500/10 p-3 text-xs leading-5 text-amber-100"><strong>Check mesh scale:</strong> longest dimension is {formatAngstrom(selectedMaxDimension)}. This is unusual for a protein-scale blueprint; confirm the source unit and re-admit if needed.</div>}
                </section>
            </div>
        </div>
    );
}
