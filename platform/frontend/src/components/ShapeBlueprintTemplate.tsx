import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';

import {
    completeCurrentLaunchContext,
    listShapeGeometries,
    submitShapeBlueprint,
    uploadShapeGeometry,
    type ShapeGeometrySummary,
} from '../lib/api';
import { buildShapeLaunchRequest } from '../lib/shapeBlueprintLaunch';
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
    const [lengthMode, setLengthMode] = useState<'fixed' | 'deterministic_range'>('fixed');
    const [minimumLength, setMinimumLength] = useState(350);
    const [maximumLength, setMaximumLength] = useState(450);
    const [numBackbones, setNumBackbones] = useState(1);
    const [sequencesPerBackbone, setSequencesPerBackbone] = useState(1);
    const [sequencePolicy, setSequencePolicy] = useState<'auto' | 'skip' | 'external'>('auto');
    const [sequenceEngine, setSequenceEngine] = useState<'proteinmpnn' | 'fampnn'>('proteinmpnn');
    const [seed, setSeed] = useState(0);
    const [error, setError] = useState<string | null>(null);
    const [reviewMode, setReviewMode] = useState<'surface' | 'points'>('surface');

    const geometriesQuery = useQuery({
        queryKey: ['shape-geometries'],
        queryFn: () => listShapeGeometries().then((response) => response.data.geometries),
    });
    const geometries = useMemo(() => geometriesQuery.data ?? [], [geometriesQuery.data]);
    const selected = useMemo<ShapeGeometrySummary | undefined>(
        () => geometries.find((geometry) => geometry.geometry_id === selectedId) ?? geometries[0],
        [geometries, selectedId],
    );
    const selectedMaxDimension = selected ? Math.max(...selected.dimensions_angstrom) : null;
    const hasHashBoundSurface = Boolean(selected?.preview_obj_sha256);
    const effectiveReviewMode = reviewMode === 'surface' && hasHashBoundSurface ? 'surface' : 'points';
    const invalidLengthPolicy = lengthMode === 'deterministic_range' && minimumLength > maximumLength;

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
            return submitShapeBlueprint(buildShapeLaunchRequest(selected, {
                client_request_id: clientRequestId,
                name: name.trim() || 'Shape Blueprint design',
                target_length: lengthMode === 'fixed' ? targetLength : undefined,
                length_policy: {
                    mode: lengthMode,
                    min: lengthMode === 'fixed' ? targetLength : minimumLength,
                    max: lengthMode === 'fixed' ? targetLength : maximumLength,
                },
                num_backbones: numBackbones,
                sequences_per_backbone: sequencePolicy === 'skip' ? 0 : sequencesPerBackbone,
                sequence_policy: sequencePolicy,
                sequence_engine: sequencePolicy === 'external' ? sequenceEngine : undefined,
                seed,
                guidance_profile: 'rfd3_ca_shape_transfer_control_v1',
            }));
        },
        onSuccess: async (response) => {
            sessionStorage.removeItem(SHAPE_CLIENT_REQUEST_KEY);
            navigate(await completeCurrentLaunchContext(response.data) ?? `/designs/${response.data.job_id}`);
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
                            <option value="angstrom">Ångström (1 mesh unit = 1 Å)</option><option value="nanometer">Nanometer</option><option value="micrometer">Micrometer</option><option value="millimeter">Millimeter</option><option value="centimeter">Centimeter</option><option value="meter">Meter</option><option value="inch">Inch</option><option value="foot">Foot</option>
                        </select>
                        <button type="button" disabled={!file || upload.isPending} onClick={() => upload.mutate()} className="rounded-lg bg-cyan-600 px-4 py-2 text-sm font-medium text-white disabled:opacity-40">{upload.isPending ? 'Validating mesh…' : 'Admit mesh'}</button>
                    </div>
                    <select value={selected?.geometry_id ?? ''} onChange={(event) => setSelectedId(event.target.value)} className="w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white">
                        {!geometries.length && <option value="">No admitted geometry</option>}
                        {geometries.map((geometry) => <option key={geometry.geometry_id} value={geometry.geometry_id}>{geometry.source_format.toUpperCase()} · {geometry.geometry_id}</option>)}
                    </select>

                    <div className="border-t border-slate-800 pt-4">
                        <h2 className="font-semibold text-white">2. Launch settings</h2>
                        <div className="mt-3 rounded-lg border border-cyan-500/30 bg-cyan-500/10 p-3 text-xs leading-5 text-cyan-100">
                            <strong>Guidance: RFD3 Cα shape-transfer control v1.</strong> Uses the source controller's 0.75 shape weight, guide scale 2, constant schedule, and 800 active interior targets through the reviewed native RFD3 <code>delta_L</code> transfer. It is a modern RFD3 transfer—not classic-RFdiffusion parity—and it is not yet a promoted protein-validity profile.
                        </div>
                        <label className="mt-3 block text-xs text-slate-400">Job name<input value={name} onChange={(event) => setName(event.target.value)} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                        <div className="mt-3 grid grid-cols-2 gap-3">
                            <label className="text-xs text-slate-400">Length policy<select value={lengthMode} onChange={(event) => setLengthMode(event.target.value as 'fixed' | 'deterministic_range')} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"><option value="fixed">Fixed length</option><option value="deterministic_range">Deterministic range</option></select></label>
                            {lengthMode === 'fixed' ? <label className="text-xs text-slate-400">Target length<input type="number" min={40} max={600} value={targetLength} onChange={(event) => setTargetLength(boundedInteger(event.target.value, 120, 40, 600))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label> : <><label className="text-xs text-slate-400">Minimum length<input type="number" min={40} max={600} value={minimumLength} onChange={(event) => setMinimumLength(boundedInteger(event.target.value, 350, 40, 600))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label><label className="text-xs text-slate-400">Maximum length<input type="number" min={40} max={600} value={maximumLength} onChange={(event) => setMaximumLength(boundedInteger(event.target.value, 450, 40, 600))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label></>}
                            <label className="text-xs text-slate-400">RFD3 total candidates<input type="number" min={1} max={200} value={numBackbones} onChange={(event) => setNumBackbones(boundedInteger(event.target.value, 1, 1, 200))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                            <label className="text-xs text-slate-400">Sequence policy<select value={sequencePolicy} onChange={(event) => setSequencePolicy(event.target.value as 'auto' | 'skip' | 'external')} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"><option value="auto">Auto · ProteinMPNN when needed</option><option value="skip">Skip sequence design</option><option value="external">Explicit engine</option></select></label>
                            {sequencePolicy === 'external' && <label className="text-xs text-slate-400">Sequence engine<select value={sequenceEngine} onChange={(event) => setSequenceEngine(event.target.value as 'proteinmpnn' | 'fampnn')} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white"><option value="proteinmpnn">ProteinMPNN</option><option value="fampnn">FAMPNN</option></select></label>}
                            <label className="text-xs text-slate-400">Sequences / admitted backbone<input type="number" min={sequencePolicy === 'skip' ? 0 : 1} max={8} disabled={sequencePolicy === 'skip'} value={sequencePolicy === 'skip' ? 0 : sequencesPerBackbone} onChange={(event) => setSequencesPerBackbone(boundedInteger(event.target.value, 1, 1, 8))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white disabled:opacity-50" /></label>
                            <label className="text-xs text-slate-400">Deterministic seed<input type="number" min={0} max={2147483647} value={seed} onChange={(event) => setSeed(boundedInteger(event.target.value, 0, 0, 2147483647))} className="mt-1 w-full rounded-lg border border-slate-700 bg-slate-950 px-3 py-2 text-sm text-white" /></label>
                        </div>
                    </div>
                    {invalidLengthPolicy && <p className="text-xs text-amber-200">Minimum length must not exceed maximum length.</p>}
                    <button type="button" disabled={!selected || launch.isPending || invalidLengthPolicy} onClick={() => launch.mutate()} className="w-full rounded-xl bg-emerald-600 px-4 py-3 font-semibold text-white hover:bg-emerald-500 disabled:opacity-40">{launch.isPending ? 'Staging immutable request…' : 'Launch Shape Blueprint'}</button>
                </section>

                <section className="overflow-hidden rounded-2xl border border-slate-800 bg-slate-900/80">
                    <div className="border-b border-slate-800 p-4">
                        <div className="flex flex-wrap items-start justify-between gap-3">
                            <div><h2 className="font-semibold text-white">Canonical geometry review</h2><p className="mt-1 text-xs text-slate-400">{hasHashBoundSurface ? 'Review the exact hash-bound server-canonicalized surface or point pool—not the raw upload or a browser reconstruction.' : 'This legacy surface is not hash-bound; exact review is limited to the canonical point pool.'}</p></div>
                            <div className="flex rounded-lg border border-slate-700 p-1 text-xs">
                                <button type="button" disabled={!hasHashBoundSurface} onClick={() => setReviewMode('surface')} className={`rounded px-3 py-1 disabled:cursor-not-allowed disabled:opacity-40 ${effectiveReviewMode === 'surface' ? 'bg-cyan-600 text-white' : 'text-slate-300'}`}>Surface</button>
                                <button type="button" onClick={() => setReviewMode('points')} className={`rounded px-3 py-1 ${effectiveReviewMode === 'points' ? 'bg-cyan-600 text-white' : 'text-slate-300'}`}>Points</button>
                            </div>
                        </div>
                    </div>
                    {selected ? (effectiveReviewMode === 'surface'
                        ? <CanonicalMeshPreview url={`/api/shape-blueprint/geometries/${selected.geometry_id}/preview.obj`} height={430} label="Canonical Shape surface" />
                        : <MolstarViewer structureUrl={`/api/shape-blueprint/geometries/${selected.geometry_id}/points.cif`} format="cif" height={430} label="Canonical Shape point pool" />)
                        : <div className="flex h-[430px] items-center justify-center text-sm text-slate-500">Select geometry to preview</div>}
                    {selected && <div className="grid gap-2 border-t border-slate-800 p-4 text-xs text-slate-300 sm:grid-cols-2">
                        <div>Source <span className="font-semibold text-white">{selected.source_format.toUpperCase()}</span> · {selected.source_parser.replaceAll('_', ' ')}</div>
                        <div>Units <span className="font-semibold text-white">{selected.source_unit}</span> · {selected.angstrom_per_unit.toExponential(3)} Å/unit</div>
                        <div className="sm:col-span-2">Dimensions <span className="font-mono text-cyan-200">{selected.dimensions_angstrom.map(formatAngstrom).join(' × ')}</span></div>
                        <div>Source bytes <span className="font-mono text-cyan-200" title={selected.source_sha256}>{shortHash(selected.source_sha256)}</span></div>
                        <div>Geometry <span className="font-mono text-cyan-200" title={selected.geometry_sha256}>{shortHash(selected.geometry_sha256)}</span></div>
                        <div>Manifest <span className="font-mono text-cyan-200" title={selected.manifest_sha256}>{shortHash(selected.manifest_sha256)}</span></div>
                        {selected.preview_obj_sha256 && <div>Surface <span className="font-mono text-cyan-200" title={selected.preview_obj_sha256}>{shortHash(selected.preview_obj_sha256)}</span></div>}
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
