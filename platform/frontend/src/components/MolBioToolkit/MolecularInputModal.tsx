import { useEffect, useMemo, useRef, useState } from 'react';
import {
    fetchNucleotideSequences,
    fetchPrimers,
    type FetchNucleotideSequencesParams,
    type NucleotideSequenceListItem,
    type Primer as LibraryPrimer,
} from '../../lib/api';
import type { Primer, SequenceData } from './types';
import {
    calculateGcPercent,
    findPatternPositions,
    inferSequenceTypeFromSequence,
    parseSequenceInput,
    reverseComplementSequence,
    sequenceUnitLabel,
} from './utils/nucleotides';

type InputTab = 'library' | 'import' | 'paste' | 'primers' | 'demos';

interface MolecularInputModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSelectSequence: (id: string) => void | Promise<void>;
    onImportFile: (file: File) => void | Promise<void>;
    onCreateSequence: (data: {
        name: string;
        sequence: string;
        sequenceType: 'dna' | 'rna';
        circular: boolean;
        description?: string;
    }) => void;
    onLoadDemo: (demo: SequenceData) => void;
    onAddPrimerToCurrentSequence: (primer: Primer) => void;
    onOpenPrimerAsConstruct: (primer: { name: string; sequence: string; description?: string }) => void;
    hasOpenSequence: boolean;
    currentSequenceData?: SequenceData | null;
    demos: SequenceData[];
}

function TopologyBadge({ circular }: { circular: boolean }) {
    return (
        <span className={`inline-flex items-center gap-1 rounded-full px-2 py-0.5 text-[11px] font-medium ${circular ? 'bg-emerald-500/15 text-emerald-300' : 'bg-sky-500/15 text-sky-300'}`}>
            <span>{circular ? '○' : '─'}</span>
            {circular ? 'Circular' : 'Linear'}
        </span>
    );
}

function SequenceTypeBadge({ sequenceType }: { sequenceType: string }) {
    const color = sequenceType === 'rna'
        ? 'bg-fuchsia-500/15 text-fuchsia-300'
        : 'bg-amber-500/15 text-amber-300';
    return (
        <span className={`rounded-full px-2 py-0.5 text-[11px] font-medium uppercase ${color}`}>
            {sequenceType}
        </span>
    );
}

function MiniPreview({ circular, sequenceType, length, featureCount }: { circular: boolean; sequenceType: string; length: number; featureCount: number }) {
    const width = `${Math.max(18, Math.min(100, 18 + Math.log10(Math.max(length, 10)) * 18))}%`;
    const accent = sequenceType === 'rna' ? '#d946ef' : circular ? '#10b981' : '#38bdf8';

    return (
        <div className="space-y-1">
            <div className="flex items-center gap-2 text-[11px] text-slate-500">
                <span>{length.toLocaleString()}</span>
                <span>•</span>
                <span>{featureCount} features</span>
            </div>
            <div className="h-2 rounded-full bg-slate-800 overflow-hidden border border-slate-700">
                <div className="h-full rounded-full" style={{ width, backgroundColor: accent }} />
            </div>
        </div>
    );
}

export function MolecularInputModal({
    isOpen,
    onClose,
    onSelectSequence,
    onImportFile,
    onCreateSequence,
    onLoadDemo,
    onAddPrimerToCurrentSequence,
    onOpenPrimerAsConstruct,
    hasOpenSequence,
    currentSequenceData,
    demos,
}: MolecularInputModalProps) {
    const [activeTab, setActiveTab] = useState<InputTab>('library');

    const [search, setSearch] = useState('');
    const [sequenceTypeFilter, setSequenceTypeFilter] = useState<'all' | 'dna' | 'rna'>('all');
    const [topologyFilter, setTopologyFilter] = useState<'all' | 'circular' | 'linear'>('all');
    const [sortBy, setSortBy] = useState<FetchNucleotideSequencesParams['sort_by']>('updated_at');
    const [libraryResults, setLibraryResults] = useState<NucleotideSequenceListItem[]>([]);
    const [libraryLoading, setLibraryLoading] = useState(false);
    const [libraryError, setLibraryError] = useState<string | null>(null);

    const [selectedFile, setSelectedFile] = useState<File | null>(null);

    const [buildName, setBuildName] = useState('');
    const [buildDescription, setBuildDescription] = useState('');
    const [buildRawSequence, setBuildRawSequence] = useState('');
    const [buildType, setBuildType] = useState<'auto' | 'dna' | 'rna'>('auto');
    const [buildCircular, setBuildCircular] = useState(true);

    const [primerSearch, setPrimerSearch] = useState('');
    const [primerFavoritesOnly, setPrimerFavoritesOnly] = useState(false);
    const [primerResults, setPrimerResults] = useState<LibraryPrimer[]>([]);
    const [primerLoading, setPrimerLoading] = useState(false);
    const [primerError, setPrimerError] = useState<string | null>(null);

    const importInputRef = useRef<HTMLInputElement>(null);

    useEffect(() => {
        if (!isOpen) return;
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                onClose();
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isOpen, onClose]);

    useEffect(() => {
        if (!isOpen || activeTab !== 'library') return;
        const timeoutId = window.setTimeout(async () => {
            setLibraryLoading(true);
            setLibraryError(null);
            try {
                const response = await fetchNucleotideSequences({
                    limit: 100,
                    search: search.trim() || undefined,
                    sequence_type: sequenceTypeFilter === 'all' ? undefined : sequenceTypeFilter,
                    topology: topologyFilter,
                    sort_by: sortBy,
                    sort_desc: true,
                });
                setLibraryResults(response.data);
            } catch (error) {
                setLibraryError(error instanceof Error ? error.message : 'Failed to load construct library');
            } finally {
                setLibraryLoading(false);
            }
        }, search ? 250 : 0);

        return () => window.clearTimeout(timeoutId);
    }, [activeTab, isOpen, search, sequenceTypeFilter, topologyFilter, sortBy]);

    useEffect(() => {
        if (!isOpen || activeTab !== 'primers') return;
        const timeoutId = window.setTimeout(async () => {
            setPrimerLoading(true);
            setPrimerError(null);
            try {
                const response = await fetchPrimers({
                    search: primerSearch.trim() || undefined,
                    favorites_only: primerFavoritesOnly,
                });
                setPrimerResults(response.data);
            } catch (error) {
                setPrimerError(error instanceof Error ? error.message : 'Failed to load primer library');
            } finally {
                setPrimerLoading(false);
            }
        }, primerSearch ? 250 : 0);

        return () => window.clearTimeout(timeoutId);
    }, [activeTab, isOpen, primerSearch, primerFavoritesOnly]);

    useEffect(() => {
        if (!isOpen) return;
        if (buildName || buildRawSequence || buildDescription) return;
        setBuildCircular(true);
    }, [isOpen, buildName, buildRawSequence, buildDescription]);

    const parsedBuildInput = useMemo(() => {
        return parseSequenceInput(
            buildRawSequence,
            buildType === 'auto' ? undefined : buildType,
        );
    }, [buildRawSequence, buildType]);

    const buildSequenceType = buildType === 'auto'
        ? parsedBuildInput.sequenceType
        : buildType;
    const buildUnitLabel = sequenceUnitLabel(buildSequenceType);
    const buildGc = calculateGcPercent(parsedBuildInput.sequence);

    if (!isOpen) return null;

    const tabs: Array<{ id: InputTab; label: string }> = [
        { id: 'library', label: 'Library' },
        { id: 'import', label: 'Import' },
        { id: 'paste', label: 'Paste / Build' },
        { id: 'primers', label: 'Primers / Oligos' },
        { id: 'demos', label: 'Demo Plasmids' },
    ];

    const handleImportSelectedFile = async () => {
        if (!selectedFile) return;
        await onImportFile(selectedFile);
        setSelectedFile(null);
        onClose();
    };

    const handleCreateConstruct = () => {
        if (!parsedBuildInput.sequence || parsedBuildInput.invalidCharacters.length > 0) {
            return;
        }

        onCreateSequence({
            name: buildName.trim() || (parsedBuildInput.name !== 'Untitled Sequence' ? parsedBuildInput.name : 'New Construct'),
            description: buildDescription.trim() || undefined,
            sequence: parsedBuildInput.sequence,
            sequenceType: buildSequenceType,
            circular: buildCircular,
        });

        setBuildName('');
        setBuildDescription('');
        setBuildRawSequence('');
        setBuildType('auto');
        setBuildCircular(true);
        onClose();
    };

    const buildPrimerForSequence = (primer: LibraryPrimer): Primer => {
        const strand = primer.binding_strand === -1 ? -1 : 1;
        const sequenceType = currentSequenceData?.sequenceType === 'rna' ? 'rna' : inferSequenceTypeFromSequence(primer.sequence);
        const searchSeq = strand === -1
            ? reverseComplementSequence(primer.sequence, sequenceType)
            : primer.sequence.toUpperCase();
        const positions = currentSequenceData
            ? findPatternPositions(currentSequenceData.sequence, searchSeq, {
                circular: currentSequenceData.circular,
            })
            : [];
        const start = positions[0] ?? primer.binding_start ?? 0;

        return {
            id: primer.id,
            name: primer.name,
            sequence: primer.sequence,
            start,
            end: positions[0] != null ? start + searchSeq.length : (primer.binding_end ?? (start + searchSeq.length)),
            strand,
            tm: primer.tm ?? undefined,
            gc_percent: primer.gc_percent ?? undefined,
        };
    };

    return (
        <div className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4" onClick={onClose}>
            <div
                className="w-full max-w-6xl max-h-[90vh] overflow-hidden rounded-2xl border border-slate-700 bg-slate-900 shadow-2xl flex flex-col"
                onClick={(event) => event.stopPropagation()}
            >
                <div className="flex items-center justify-between border-b border-slate-700 bg-slate-800/70 px-6 py-4">
                    <div>
                        <h3 className="text-lg font-semibold text-slate-100">Molecular Input</h3>
                        <p className="text-sm text-slate-400">Open a saved construct, import a file, build from pasted sequence, or pull primers from the library.</p>
                    </div>
                    <button
                        onClick={onClose}
                        className="rounded-lg p-2 text-slate-400 hover:bg-slate-700 hover:text-white transition-colors"
                    >
                        ✕
                    </button>
                </div>

                <div className="border-b border-slate-700 px-4 py-3">
                    <div className="flex flex-wrap gap-2">
                        {tabs.map((tab) => (
                            <button
                                key={tab.id}
                                onClick={() => setActiveTab(tab.id)}
                                className={`rounded-full px-3 py-1.5 text-sm transition-colors ${activeTab === tab.id ? 'bg-cyan-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
                            >
                                {tab.label}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="flex-1 overflow-y-auto p-6">
                    {activeTab === 'library' && (
                        <div className="space-y-4">
                            <div className="grid gap-3 md:grid-cols-[2fr,1fr,1fr,1fr]">
                                <input
                                    type="text"
                                    value={search}
                                    onChange={(event) => setSearch(event.target.value)}
                                    placeholder="Search constructs, accession, organism, source file..."
                                    className="rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                />
                                <select
                                    value={sequenceTypeFilter}
                                    onChange={(event) => setSequenceTypeFilter(event.target.value as typeof sequenceTypeFilter)}
                                    className="rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                >
                                    <option value="all">All types</option>
                                    <option value="dna">DNA</option>
                                    <option value="rna">RNA</option>
                                </select>
                                <select
                                    value={topologyFilter}
                                    onChange={(event) => setTopologyFilter(event.target.value as typeof topologyFilter)}
                                    className="rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                >
                                    <option value="all">All topologies</option>
                                    <option value="circular">Circular</option>
                                    <option value="linear">Linear</option>
                                </select>
                                <select
                                    value={sortBy}
                                    onChange={(event) => setSortBy(event.target.value as NonNullable<typeof sortBy>)}
                                    className="rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                >
                                    <option value="updated_at">Recently updated</option>
                                    <option value="created_at">Recently created</option>
                                    <option value="name">Name</option>
                                    <option value="length">Length</option>
                                    <option value="gc_content">GC%</option>
                                    <option value="feature_count">Feature count</option>
                                </select>
                            </div>

                            {libraryError && (
                                <div className="rounded-xl border border-red-800 bg-red-900/40 px-4 py-3 text-sm text-red-200">
                                    {libraryError}
                                </div>
                            )}

                            {libraryLoading ? (
                                <div className="rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-10 text-center text-slate-400">
                                    Loading construct library...
                                </div>
                            ) : libraryResults.length === 0 ? (
                                <div className="rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-10 text-center text-slate-400">
                                    No constructs match the current filters.
                                </div>
                            ) : (
                                <div className="grid gap-3 lg:grid-cols-2">
                                    {libraryResults.map((sequence) => (
                                        <button
                                            key={sequence.id}
                                            onClick={async () => {
                                                await onSelectSequence(sequence.id);
                                                onClose();
                                            }}
                                            className="rounded-2xl border border-slate-700 bg-slate-800/60 p-4 text-left transition-colors hover:border-cyan-500 hover:bg-slate-800"
                                        >
                                            <div className="flex items-start justify-between gap-3">
                                                <div className="min-w-0">
                                                    <div className="truncate text-base font-semibold text-slate-100">{sequence.name}</div>
                                                    {sequence.description && (
                                                        <div className="mt-1 line-clamp-2 text-sm text-slate-400">{sequence.description}</div>
                                                    )}
                                                </div>
                                                <TopologyBadge circular={sequence.is_circular} />
                                            </div>

                                            <div className="mt-3 flex flex-wrap items-center gap-2">
                                                <SequenceTypeBadge sequenceType={sequence.sequence_type} />
                                                {sequence.entity_kind && (
                                                    <span className="rounded-full bg-slate-700 px-2 py-0.5 text-[11px] text-slate-300">
                                                        {sequence.entity_kind.replace(/_/g, ' ')}
                                                    </span>
                                                )}
                                                {sequence.organism && (
                                                    <span className="rounded-full bg-slate-700 px-2 py-0.5 text-[11px] text-slate-300">
                                                        {sequence.organism}
                                                    </span>
                                                )}
                                            </div>

                                            <div className="mt-4">
                                                <MiniPreview
                                                    circular={sequence.is_circular}
                                                    sequenceType={sequence.sequence_type}
                                                    length={sequence.length}
                                                    featureCount={sequence.feature_count}
                                                />
                                            </div>

                                            <div className="mt-3 flex items-center justify-between text-xs text-slate-500">
                                                <span>GC {sequence.gc_content?.toFixed(1) ?? 'n/a'}%</span>
                                                <span>{sequence.updated_at ? new Date(sequence.updated_at).toLocaleString() : new Date(sequence.created_at).toLocaleString()}</span>
                                            </div>
                                        </button>
                                    ))}
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'import' && (
                        <div className="space-y-4">
                            <div
                                onClick={() => importInputRef.current?.click()}
                                className="rounded-2xl border-2 border-dashed border-slate-600 bg-slate-800/40 p-10 text-center cursor-pointer transition-colors hover:border-cyan-500 hover:bg-cyan-500/5"
                            >
                                <div className="text-3xl">🧬</div>
                                <div className="mt-3 text-base font-medium text-slate-200">Import construct file</div>
                                <div className="mt-1 text-sm text-slate-400">GenBank, FASTA, and SnapGene `.dna` are supported.</div>
                                <div className="mt-4 text-xs text-slate-500">Click to choose a file and load it into the editor.</div>
                            </div>
                            <input
                                ref={importInputRef}
                                type="file"
                                accept=".gb,.gbk,.genbank,.fasta,.fa,.fna,.dna"
                                onChange={(event) => setSelectedFile(event.target.files?.[0] || null)}
                                className="hidden"
                            />

                            {selectedFile && (
                                <div className="rounded-2xl border border-slate-700 bg-slate-800/50 p-4">
                                    <div className="text-sm text-slate-400">Selected file</div>
                                    <div className="mt-1 text-base font-medium text-slate-100">{selectedFile.name}</div>
                                    <div className="mt-1 text-xs text-slate-500">{(selectedFile.size / 1024).toFixed(1)} KB</div>
                                    <button
                                        onClick={handleImportSelectedFile}
                                        className="mt-4 rounded-xl bg-cyan-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-500"
                                    >
                                        Import Into Editor
                                    </button>
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'paste' && (
                        <div className="grid gap-6 lg:grid-cols-[1.35fr,0.9fr]">
                            <div className="space-y-3">
                                <input
                                    type="text"
                                    value={buildName}
                                    onChange={(event) => setBuildName(event.target.value)}
                                    placeholder="Construct name"
                                    className="w-full rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                />
                                <textarea
                                    value={buildDescription}
                                    onChange={(event) => setBuildDescription(event.target.value)}
                                    placeholder="Description or provenance notes"
                                    className="min-h-20 w-full rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                />
                                <textarea
                                    value={buildRawSequence}
                                    onChange={(event) => setBuildRawSequence(event.target.value)}
                                    placeholder="Paste FASTA or raw nucleotide sequence"
                                    className="min-h-72 w-full rounded-xl border border-slate-600 bg-slate-950 px-3 py-2 text-sm font-mono text-white outline-none focus:border-cyan-500"
                                />
                            </div>

                            <div className="space-y-4 rounded-2xl border border-slate-700 bg-slate-800/50 p-4">
                                <div className="text-sm font-medium text-slate-200">Build Options</div>

                                <div className="space-y-2">
                                    <label className="text-xs uppercase tracking-wide text-slate-500">Sequence Type</label>
                                    <select
                                        value={buildType}
                                        onChange={(event) => setBuildType(event.target.value as typeof buildType)}
                                        className="w-full rounded-xl border border-slate-600 bg-slate-900 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                    >
                                        <option value="auto">Auto-detect</option>
                                        <option value="dna">DNA</option>
                                        <option value="rna">RNA</option>
                                    </select>
                                </div>

                                <label className="flex items-center gap-2 text-sm text-slate-300">
                                    <input
                                        type="checkbox"
                                        checked={buildCircular}
                                        onChange={(event) => setBuildCircular(event.target.checked)}
                                        className="h-4 w-4"
                                    />
                                    Treat as circular construct
                                </label>

                                <div className="rounded-xl border border-slate-700 bg-slate-900/80 p-4 space-y-2">
                                    <div className="flex items-center gap-2">
                                        <SequenceTypeBadge sequenceType={buildSequenceType} />
                                        <TopologyBadge circular={buildCircular} />
                                    </div>
                                    <div className="text-sm text-slate-300">
                                        {parsedBuildInput.sequence.length.toLocaleString()} {buildUnitLabel}
                                    </div>
                                    <div className="text-xs text-slate-500">
                                        GC {buildGc}% {parsedBuildInput.sequence ? '• parsed and validated' : ''}
                                    </div>
                                    {parsedBuildInput.invalidCharacters.length > 0 && (
                                        <div className="rounded-lg border border-red-800 bg-red-900/40 px-3 py-2 text-xs text-red-200">
                                            Invalid characters: {parsedBuildInput.invalidCharacters.join(', ')}
                                        </div>
                                    )}
                                </div>

                                <button
                                    onClick={handleCreateConstruct}
                                    disabled={!parsedBuildInput.sequence || parsedBuildInput.invalidCharacters.length > 0}
                                    className="w-full rounded-xl bg-cyan-600 px-4 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-500 disabled:cursor-not-allowed disabled:bg-slate-600"
                                >
                                    Open In Editor
                                </button>
                            </div>
                        </div>
                    )}

                    {activeTab === 'primers' && (
                        <div className="space-y-4">
                            <div className="flex gap-3">
                                <input
                                    type="text"
                                    value={primerSearch}
                                    onChange={(event) => setPrimerSearch(event.target.value)}
                                    placeholder="Search primer or oligo library"
                                    className="flex-1 rounded-xl border border-slate-600 bg-slate-800 px-3 py-2 text-sm text-white outline-none focus:border-cyan-500"
                                />
                                <button
                                    onClick={() => setPrimerFavoritesOnly((value) => !value)}
                                    className={`rounded-xl px-3 py-2 text-sm transition-colors ${primerFavoritesOnly ? 'bg-amber-600 text-white' : 'bg-slate-800 text-slate-300 hover:bg-slate-700'}`}
                                >
                                    ★ Favorites
                                </button>
                            </div>

                            {primerError && (
                                <div className="rounded-xl border border-red-800 bg-red-900/40 px-4 py-3 text-sm text-red-200">
                                    {primerError}
                                </div>
                            )}

                            {primerLoading ? (
                                <div className="rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-10 text-center text-slate-400">
                                    Loading primer library...
                                </div>
                            ) : primerResults.length === 0 ? (
                                <div className="rounded-xl border border-slate-700 bg-slate-800/40 px-4 py-10 text-center text-slate-400">
                                    No primers match the current filters.
                                </div>
                            ) : (
                                <div className="space-y-2">
                                    {primerResults.map((primer) => {
                                        const primerType = inferSequenceTypeFromSequence(primer.sequence);
                                        const primerUnit = sequenceUnitLabel(primerType);
                                        return (
                                            <div
                                                key={primer.id}
                                                className="rounded-2xl border border-slate-700 bg-slate-800/50 p-4 flex items-start justify-between gap-4"
                                            >
                                                <div className="min-w-0">
                                                    <div className="flex items-center gap-2">
                                                        <div className="truncate text-base font-medium text-slate-100">{primer.name}</div>
                                                        {primer.is_favorite && <span className="text-amber-400">★</span>}
                                                    </div>
                                                    <div className="mt-1 font-mono text-xs text-slate-400 break-all">{primer.sequence}</div>
                                                    <div className="mt-2 flex flex-wrap gap-2">
                                                        <SequenceTypeBadge sequenceType={primerType} />
                                                        <span className="rounded-full bg-slate-700 px-2 py-0.5 text-[11px] text-slate-300">
                                                            {primer.length} {primerUnit}
                                                        </span>
                                                        <span className="rounded-full bg-slate-700 px-2 py-0.5 text-[11px] text-slate-300">
                                                            Tm {primer.tm?.toFixed(1) ?? 'n/a'}°C
                                                        </span>
                                                    </div>
                                                </div>
                                                <div className="flex shrink-0 flex-col gap-2">
                                                    {hasOpenSequence && (
                                                        <button
                                                            onClick={() => {
                                                                onAddPrimerToCurrentSequence(buildPrimerForSequence(primer));
                                                                onClose();
                                                            }}
                                                            className="rounded-xl bg-cyan-600 px-3 py-2 text-sm font-medium text-white transition-colors hover:bg-cyan-500"
                                                        >
                                                            Add To Current
                                                        </button>
                                                    )}
                                                    <button
                                                        onClick={() => {
                                                            onOpenPrimerAsConstruct({
                                                                name: primer.name,
                                                                sequence: primer.sequence,
                                                                description: primer.description ?? undefined,
                                                            });
                                                            onClose();
                                                        }}
                                                        className="rounded-xl bg-slate-700 px-3 py-2 text-sm font-medium text-slate-100 transition-colors hover:bg-slate-600"
                                                    >
                                                        Open As Construct
                                                    </button>
                                                </div>
                                            </div>
                                        );
                                    })}
                                </div>
                            )}
                        </div>
                    )}

                    {activeTab === 'demos' && (
                        <div className="space-y-3">
                            <div className="rounded-2xl border border-amber-500/30 bg-amber-500/8 p-3 text-sm text-amber-100">
                                These demos are imported from public Addgene browse-sequence pages for fast testing inside the toolkit. Treat them as external reference records and verify provenance before unknown real build decision.
                            </div>
                            <div className="grid gap-3 lg:grid-cols-2">
                            {demos.map((demo) => (
                                <button
                                    key={demo.name}
                                    onClick={() => {
                                        onLoadDemo(demo);
                                        onClose();
                                    }}
                                    className="rounded-2xl border border-slate-700 bg-slate-800/50 p-4 text-left transition-colors hover:border-cyan-500 hover:bg-slate-800"
                                >
                                    <div className="flex items-start justify-between gap-3">
                                        <div className="min-w-0">
                                            <div className="truncate text-base font-semibold text-slate-100">{demo.name}</div>
                                            {demo.description && (
                                                <div className="mt-1 text-sm text-slate-400">{demo.description}</div>
                                            )}
                                        </div>
                                        <div className="flex flex-col items-end gap-2">
                                            <TopologyBadge circular={demo.circular} />
                                            <span className="rounded-full bg-amber-500/15 px-2 py-0.5 text-[11px] font-medium text-amber-200">
                                                Synthetic
                                            </span>
                                        </div>
                                    </div>
                                    <div className="mt-3 flex items-center gap-2">
                                        <SequenceTypeBadge sequenceType={demo.sequenceType} />
                                        <span className="rounded-full bg-slate-700 px-2 py-0.5 text-[11px] text-slate-300">
                                            {demo.sequence.length.toLocaleString()} {sequenceUnitLabel(demo.sequenceType === 'rna' ? 'rna' : 'dna')}
                                        </span>
                                    </div>
                                    <div className="mt-4">
                                        <MiniPreview
                                            circular={demo.circular}
                                            sequenceType={demo.sequenceType}
                                            length={demo.sequence.length}
                                            featureCount={demo.features.length}
                                        />
                                    </div>
                                </button>
                            ))}
                            </div>
                        </div>
                    )}
                </div>
            </div>
        </div>
    );
}
