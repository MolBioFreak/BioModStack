import { useEffect, useMemo, useState } from 'react';
import {
    fetchRnaStructureOptions,
    foldRnaStructure,
    partitionRnaStructure,
    type RnaStructureOptionsResponse,
    type RnaStructureResult,
    type RnaStructureSettings,
} from '../../../lib/api';
import type { AnalysisTrack, SequenceData } from '../types';
import type { RnaStructureDisplayMode } from '../RnaStructureViewer';

interface RnaStructurePanelProps {
    sequenceData: SequenceData;
    structureResult: RnaStructureResult | null;
    displayMode: RnaStructureDisplayMode;
    onDisplayModeChange: (mode: RnaStructureDisplayMode) => void;
    onStructureResultChange: (result: RnaStructureResult | null) => void;
    selectedTrackId: string | null;
    onSelectedTrackChange: (trackId: string | null) => void;
    onAnalysisTracksChange: (tracks: AnalysisTrack[]) => void;
}

type TrackKind = AnalysisTrack['kind'];

const TRACK_KIND_COLORS: Record<TrackKind, string> = {
    reactivity: '#67e8f9',
    coverage: '#f59e0b',
    mismatch: '#f87171',
    custom: '#a78bfa',
};

function coerceNumber(value: unknown): number | null {
    if (value == null || value === '') return null;
    const numeric = Number(value);
    return Number.isFinite(numeric) ? numeric : null;
}

function parseTrackValues(
    rawText: string,
    sequenceLength: number,
): { values: Array<number | null>; sourceFormat: string } {
    const trimmed = rawText.trim();
    if (!trimmed) {
        throw new Error('Track input is empty');
    }

    if (trimmed.startsWith('[')) {
        const parsed = JSON.parse(trimmed);
        if (!Array.isArray(parsed)) {
            throw new Error('JSON input must be an array');
        }
        if (parsed.length !== sequenceLength) {
            throw new Error(`JSON array has ${parsed.length} values but the RNA is ${sequenceLength} nt long`);
        }
        return {
            values: parsed.map((value) => {
                if (value == null) return null;
                const numeric = coerceNumber(value);
                if (numeric == null) {
                    throw new Error('JSON array contains a non-numeric value');
                }
                return numeric;
            }),
            sourceFormat: 'json_array',
        };
    }

    const lines = trimmed
        .split(/\r?\n/)
        .map((line) => line.trim())
        .filter((line) => line && !line.startsWith('#'));
    if (lines.length === 0) {
        throw new Error('Track input does not contain unknown data rows');
    }

    const firstColumns = lines[0].split(/[\t,\s]+/).filter(Boolean);
    if (firstColumns.length >= 4) {
        const values = Array.from<number | null>({ length: sequenceLength }).fill(null);
        for (const line of lines) {
            const [chrom, startToken, endToken, valueToken] = line.split(/[\t,\s]+/).filter(Boolean);
            const start = coerceNumber(startToken);
            const end = coerceNumber(endToken);
            const value = coerceNumber(valueToken);
            if (start == null || end == null || value == null) {
                throw new Error(`Invalid bedGraph-style row: ${line}`);
            }
            const clampedStart = Math.max(0, Math.floor(start));
            const clampedEnd = Math.min(sequenceLength, Math.ceil(end));
            if (clampedEnd <= clampedStart) continue;
            void chrom;
            for (let index = clampedStart; index < clampedEnd; index += 1) {
                values[index] = value;
            }
        }
        return { values, sourceFormat: 'bedgraph' };
    }

    const rows = lines.map((line) => line.split(/[\t,\s]+/).filter(Boolean));
    const positions = rows.map((columns) => coerceNumber(columns[0]));
    const oneBased = positions.every((position) => position != null && position >= 1);
    const values = Array.from<number | null>({ length: sequenceLength }).fill(null);

    for (const columns of rows) {
        if (columns.length < 2) {
            throw new Error(`Expected "position value" rows, got: ${columns.join(' ')}`);
        }
        const rawPosition = coerceNumber(columns[0]);
        const numericValue = coerceNumber(columns[1]);
        if (rawPosition == null || numericValue == null) {
            throw new Error(`Invalid position/value row: ${columns.join(' ')}`);
        }
        const index = oneBased ? Math.floor(rawPosition) - 1 : Math.floor(rawPosition);
        if (index < 0 || index >= sequenceLength) {
            throw new Error(`Track position ${rawPosition} is outside the RNA length (${sequenceLength})`);
        }
        values[index] = numericValue;
    }

    return { values, sourceFormat: oneBased ? 'position_value_1based' : 'position_value_0based' };
}

export function RnaStructurePanel({
    sequenceData,
    structureResult,
    displayMode,
    onDisplayModeChange,
    onStructureResultChange,
    selectedTrackId,
    onSelectedTrackChange,
    onAnalysisTracksChange,
}: RnaStructurePanelProps) {
    const [options, setOptions] = useState<RnaStructureOptionsResponse | null>(null);
    const [settings, setSettings] = useState<RnaStructureSettings | null>(null);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [trackName, setTrackName] = useState('');
    const [trackKind, setTrackKind] = useState<TrackKind>('reactivity');
    const [trackDescription, setTrackDescription] = useState('');
    const [trackNormalization, setTrackNormalization] = useState('');
    const [trackSourceUrl, setTrackSourceUrl] = useState('');
    const [trackRawInput, setTrackRawInput] = useState('');
    const [useSelectedTrackForGuidance, setUseSelectedTrackForGuidance] = useState(false);

    const analysisTracks = useMemo(() => sequenceData.analysisTracks || [], [sequenceData.analysisTracks]);

    useEffect(() => {
        let cancelled = false;

        const loadOptions = async () => {
            try {
                const response = await fetchRnaStructureOptions();
                if (cancelled) return;
                setOptions(response.data);
                setSettings((current) => current ?? response.data.defaults);
            } catch (loadError) {
                if (cancelled) return;
                setError(loadError instanceof Error ? loadError.message : 'Failed to load RNA structure options');
            }
        };

        loadOptions();
        return () => {
            cancelled = true;
        };
    }, []);

    useEffect(() => {
        if (selectedTrackId && analysisTracks.some((track) => track.id === selectedTrackId)) {
            return;
        }
        onSelectedTrackChange(analysisTracks[0]?.id || null);
    }, [analysisTracks, onSelectedTrackChange, selectedTrackId]);

    const selectedTrack = useMemo(
        () => analysisTracks.find((track) => track.id === selectedTrackId) || null,
        [analysisTracks, selectedTrackId],
    );

    const structureModes = useMemo(() => {
        const modes: Array<{ id: RnaStructureDisplayMode; label: string; available: boolean }> = [
            { id: 'mfe', label: 'MFE', available: Boolean(structureResult?.mfe) },
            { id: 'centroid', label: 'Centroid', available: Boolean(structureResult?.centroid) },
            { id: 'mea', label: 'MEA', available: Boolean(structureResult?.mea) },
            { id: 'probability', label: 'Probability', available: Boolean(structureResult?.partition && structureResult?.pair_probabilities.length) },
        ];
        return modes;
    }, [structureResult]);

    if (sequenceData.sequenceType !== 'rna') {
        return (
            <div className="p-3 text-sm text-slate-400">
                RNA secondary structure analysis is only available for RNA constructs.
            </div>
        );
    }

    const activeSettings = settings ?? options?.defaults ?? null;
    const limitText = options
        ? `Global fold ≤ ${options.limits.max_global_fold_length} nt, partition ≤ ${options.limits.max_partition_length} nt, bounded fold ≤ ${options.limits.max_bounded_fold_length} nt`
        : null;

    const handleSettingsChange = <K extends keyof RnaStructureSettings>(key: K, value: RnaStructureSettings[K]) => {
        setSettings((current) => ({
            ...(current ?? options?.defaults ?? {
                temperature_c: 37,
                no_lonely_pairs: false,
                dangles: 2,
                gamma: 1,
                probability_cutoff: 0.02,
                max_pairs: 800,
            }),
            [key]: value,
        }));
    };

    const runAnalysis = async (mode: 'fold' | 'partition' | 'mfe') => {
        if (!activeSettings) return;
        setLoading(true);
        setError(null);

        const requestPayload = {
            name: sequenceData.name,
            sequence: sequenceData.sequence,
            is_circular: sequenceData.circular,
            settings: {
                ...activeSettings,
                circular: sequenceData.circular,
                shape_method: useSelectedTrackForGuidance && selectedTrack ? 'deigan' : null,
                shape_reactivities: useSelectedTrackForGuidance && selectedTrack ? selectedTrack.values : null,
            },
        };

        try {
            const response = mode === 'partition'
                ? await partitionRnaStructure(requestPayload)
                : await foldRnaStructure({
                    ...requestPayload,
                    include_partition: mode !== 'mfe',
                });
            onStructureResultChange(response.data);
            if (mode === 'partition' || mode === 'fold') {
                onDisplayModeChange(response.data.partition ? 'probability' : 'mfe');
            } else {
                onDisplayModeChange('mfe');
            }
        } catch (analysisError) {
            setError(
                analysisError instanceof Error
                    ? analysisError.message
                    : 'RNA structure analysis failed',
            );
        } finally {
            setLoading(false);
        }
    };

    const handleImportTrack = () => {
        try {
            const { values, sourceFormat } = parseTrackValues(trackRawInput, sequenceData.sequence.length);
            const numericValues = values.filter((value): value is number => typeof value === 'number' && Number.isFinite(value));
            const nextTrack: AnalysisTrack = {
                id: `track_${Date.now().toString(36)}`,
                name: trackName.trim() || `${trackKind[0].toUpperCase()}${trackKind.slice(1)} track`,
                kind: trackKind,
                description: trackDescription.trim() || undefined,
                color: TRACK_KIND_COLORS[trackKind],
                sourceFormat,
                sourceName: trackName.trim() || undefined,
                sourceUrl: trackSourceUrl.trim() || undefined,
                normalization: trackNormalization.trim() || undefined,
                values,
                minValue: numericValues.length ? Math.min(...numericValues) : undefined,
                maxValue: numericValues.length ? Math.max(...numericValues) : undefined,
                createdAt: new Date().toISOString(),
            };
            const updatedTracks = [...analysisTracks, nextTrack];
            onAnalysisTracksChange(updatedTracks);
            onSelectedTrackChange(nextTrack.id);
            setTrackRawInput('');
            setTrackDescription('');
            setTrackNormalization('');
            setTrackSourceUrl('');
            if (!trackName.trim()) {
                setTrackName('');
            }
        } catch (trackError) {
            setError(trackError instanceof Error ? trackError.message : 'Failed to import evidence track');
        }
    };

    const removeTrack = (trackId: string) => {
        const updatedTracks = analysisTracks.filter((track) => track.id !== trackId);
        onAnalysisTracksChange(updatedTracks);
        if (selectedTrackId === trackId) {
            onSelectedTrackChange(updatedTracks[0]?.id || null);
        }
    };

    return (
        <div className="space-y-4 p-3 text-sm">
            <div>
                <h4 className="font-semibold text-slate-200">RNA Structure</h4>
                <p className="mt-1 text-xs leading-5 text-slate-500">
                    ViennaRNA-backed folding, partition analysis, and aligned evidence tracks on the same RNA record.
                </p>
                {limitText && (
                    <p className="mt-1 text-[11px] leading-5 text-slate-500">{limitText}</p>
                )}
            </div>

            <div className="space-y-2 rounded-xl border border-slate-700 bg-slate-900/60 p-3">
                <div className="grid grid-cols-2 gap-2">
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Temp °C</span>
                        <input
                            type="number"
                            value={activeSettings?.temperature_c ?? 37}
                            onChange={(event) => handleSettingsChange('temperature_c', Number(event.target.value))}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Dangles</span>
                        <select
                            value={activeSettings?.dangles ?? 2}
                            onChange={(event) => handleSettingsChange('dangles', Number(event.target.value))}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        >
                            <option value={0}>0</option>
                            <option value={1}>1</option>
                            <option value={2}>2</option>
                            <option value={3}>3</option>
                        </select>
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Pair cutoff</span>
                        <input
                            type="number"
                            min="0.001"
                            max="0.99"
                            step="0.005"
                            value={activeSettings?.probability_cutoff ?? 0.02}
                            onChange={(event) => handleSettingsChange('probability_cutoff', Number(event.target.value))}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Max pairs</span>
                        <input
                            type="number"
                            min="10"
                            max="5000"
                            step="10"
                            value={activeSettings?.max_pairs ?? 800}
                            onChange={(event) => handleSettingsChange('max_pairs', Number(event.target.value))}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Gamma</span>
                        <input
                            type="number"
                            min="0.1"
                            max="10"
                            step="0.1"
                            value={activeSettings?.gamma ?? 1}
                            onChange={(event) => handleSettingsChange('gamma', Number(event.target.value))}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Max bp span</span>
                        <input
                            type="number"
                            min="2"
                            max={options?.limits.max_bp_span ?? 1000}
                            step="1"
                            value={activeSettings?.max_bp_span ?? ''}
                            onChange={(event) => handleSettingsChange('max_bp_span', event.target.value ? Number(event.target.value) : null)}
                            placeholder="unbounded"
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">SHAPE slope</span>
                        <input
                            type="number"
                            step="0.1"
                            value={activeSettings?.shape_slope ?? 1.8}
                            onChange={(event) => handleSettingsChange('shape_slope', Number(event.target.value))}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">SHAPE intercept</span>
                        <input
                            type="number"
                            step="0.1"
                            value={activeSettings?.shape_intercept ?? -0.6}
                            onChange={(event) => handleSettingsChange('shape_intercept', Number(event.target.value))}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        />
                    </label>
                </div>
                <label className="flex items-center gap-2 text-xs text-slate-400">
                    <input
                        type="checkbox"
                        checked={activeSettings?.no_lonely_pairs ?? false}
                        onChange={(event) => handleSettingsChange('no_lonely_pairs', event.target.checked)}
                        className="rounded border-slate-600 bg-slate-800"
                    />
                    Disallow lonely base pairs
                </label>
                <label className="flex items-center gap-2 text-xs text-slate-400">
                    <input
                        type="checkbox"
                        checked={useSelectedTrackForGuidance && Boolean(selectedTrack)}
                        onChange={(event) => setUseSelectedTrackForGuidance(event.target.checked)}
                        disabled={!selectedTrack}
                        className="rounded border-slate-600 bg-slate-800"
                    />
                    Use selected evidence track as SHAPE-like soft constraints
                    {selectedTrack ? ` (${selectedTrack.name})` : ' (select a track below first)'}
                </label>
                <label className="space-y-1 block">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Hard constraints</span>
                    <textarea
                        value={activeSettings?.hard_constraints ?? ''}
                        onChange={(event) => handleSettingsChange('hard_constraints', event.target.value || null)}
                        rows={3}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 font-mono text-xs"
                        placeholder="Optional pseudo dot-bracket constraint string matching RNA length"
                    />
                </label>
                <div className="grid grid-cols-1 gap-2">
                    <button
                        onClick={() => void runAnalysis('fold')}
                        disabled={loading || !activeSettings}
                        className="rounded-lg bg-violet-600 px-3 py-2 font-medium text-white transition-colors hover:bg-violet-500 disabled:opacity-50"
                    >
                        {loading ? 'Running…' : 'Fold + Partition'}
                    </button>
                    <div className="grid grid-cols-2 gap-2">
                        <button
                            onClick={() => void runAnalysis('mfe')}
                            disabled={loading || !activeSettings}
                            className="rounded-lg bg-slate-700 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-600 disabled:opacity-50"
                        >
                            MFE Only
                        </button>
                        <button
                            onClick={() => void runAnalysis('partition')}
                            disabled={loading || !activeSettings}
                            className="rounded-lg bg-slate-700 px-3 py-2 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-600 disabled:opacity-50"
                        >
                            Partition Refresh
                        </button>
                    </div>
                </div>
            </div>

            {structureResult && (
                <div className="space-y-2 rounded-xl border border-slate-700 bg-slate-900/60 p-3">
                    <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Display</div>
                    <div className="flex flex-wrap gap-1">
                        {structureModes.map((mode) => (
                            <button
                                key={mode.id}
                                onClick={() => mode.available && onDisplayModeChange(mode.id)}
                                disabled={!mode.available}
                                className={`rounded px-2.5 py-1 text-xs transition-colors ${displayMode === mode.id
                                    ? 'bg-cyan-600 text-white'
                                    : 'bg-slate-800 text-slate-300 hover:bg-slate-700'
                                    } disabled:opacity-40`}
                            >
                                {mode.label}
                            </button>
                        ))}
                    </div>
                    <div className="rounded-lg border border-slate-800 bg-slate-950/70 p-2 text-xs text-slate-400">
                        {structureResult.partition
                            ? `${structureResult.length} nt • ${structureResult.partition.pair_count} pair probabilities above p ≥ ${structureResult.partition.probability_cutoff}`
                            : `${structureResult.length} nt • MFE structure only`}
                    </div>
                </div>
            )}

            <div className="space-y-3 rounded-xl border border-slate-700 bg-slate-900/60 p-3">
                <div>
                    <div className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Evidence Tracks</div>
                    <p className="mt-1 text-xs leading-5 text-slate-500">
                        Import JSON arrays, `position value`, or bedGraph-like tracks exported from probing or IGV-style workflows.
                    </p>
                </div>

                {analysisTracks.length > 0 && (
                    <div className="space-y-2">
                        <select
                            value={selectedTrackId ?? ''}
                            onChange={(event) => onSelectedTrackChange(event.target.value || null)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        >
                            <option value="">No evidence overlay</option>
                            {analysisTracks.map((track) => (
                                <option key={track.id} value={track.id}>
                                    {track.name} ({track.kind})
                                </option>
                            ))}
                        </select>
                        <div className="space-y-2">
                            {analysisTracks.map((track) => (
                                <div key={track.id} className="flex items-start justify-between gap-2 rounded-lg border border-slate-800 bg-slate-950/70 px-2 py-2">
                                    <div className="min-w-0">
                                        <div className="flex items-center gap-2">
                                            <span
                                                className="inline-block h-2.5 w-2.5 rounded-full"
                                                style={{ backgroundColor: track.color || TRACK_KIND_COLORS[track.kind] }}
                                            />
                                            <span className="truncate text-sm text-slate-200">{track.name}</span>
                                        </div>
                                        <div className="mt-1 text-[11px] text-slate-500">
                                            {track.kind} • {track.values.length} values
                                            {track.sourceFormat ? ` • ${track.sourceFormat}` : ''}
                                        </div>
                                    </div>
                                    <button
                                        onClick={() => removeTrack(track.id)}
                                        className="rounded px-2 py-1 text-[11px] text-red-300 transition-colors hover:bg-red-500/10"
                                    >
                                        Remove
                                    </button>
                                </div>
                            ))}
                        </div>
                    </div>
                )}

                <div className="grid grid-cols-2 gap-2">
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Track name</span>
                        <input
                            value={trackName}
                            onChange={(event) => setTrackName(event.target.value)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                            placeholder="e.g. SHAPE reactivity"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Kind</span>
                        <select
                            value={trackKind}
                            onChange={(event) => setTrackKind(event.target.value as TrackKind)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        >
                            <option value="reactivity">Reactivity</option>
                            <option value="coverage">Coverage</option>
                            <option value="mismatch">Mismatch</option>
                            <option value="custom">Custom</option>
                        </select>
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Normalization</span>
                        <input
                            value={trackNormalization}
                            onChange={(event) => setTrackNormalization(event.target.value)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                            placeholder="e.g. SHAPE-MaP"
                        />
                    </label>
                    <label className="space-y-1">
                        <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Source URL</span>
                        <input
                            value={trackSourceUrl}
                            onChange={(event) => setTrackSourceUrl(event.target.value)}
                            className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                            placeholder="Optional provenance link"
                        />
                    </label>
                </div>
                <label className="space-y-1 block">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Description</span>
                    <input
                        value={trackDescription}
                        onChange={(event) => setTrackDescription(event.target.value)}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 text-sm"
                        placeholder="Brief track description"
                    />
                </label>
                <label className="space-y-1 block">
                    <span className="text-[11px] uppercase tracking-[0.12em] text-slate-500">Track data</span>
                    <textarea
                        value={trackRawInput}
                        onChange={(event) => setTrackRawInput(event.target.value)}
                        rows={8}
                        className="w-full rounded border border-slate-600 bg-slate-800 px-2 py-1.5 font-mono text-xs"
                        placeholder={'[0.12, 0.08, null, ...]\n# or\n1 0.51\n2 0.42\n# or\nrna 0 25 0.31'}
                    />
                </label>
                <button
                    onClick={handleImportTrack}
                    disabled={!trackRawInput.trim()}
                    className="w-full rounded-lg bg-cyan-600 px-3 py-2 font-medium text-white transition-colors hover:bg-cyan-500 disabled:opacity-50"
                >
                    Import Evidence Track
                </button>
                {selectedTrack && selectedTrack.sourceUrl && (
                    <a
                        href={selectedTrack.sourceUrl}
                        target="_blank"
                        rel="noreferrer"
                        className="inline-flex text-xs text-cyan-300 hover:text-cyan-200"
                    >
                        Open selected track source
                    </a>
                )}
            </div>

            {error && (
                <div className="rounded-lg border border-red-800 bg-red-950/40 px-3 py-2 text-xs text-red-200">
                    {error}
                </div>
            )}
        </div>
    );
}

export default RnaStructurePanel;
