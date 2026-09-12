import { useState } from 'react';
import { SequenceManager } from './SequenceManager';
import { PresetSelector } from './PresetSelector';
import { StructureInput } from './StructureInput';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import { parsePDBFile, type Chain, type ParsedPDB } from '../utils/pdbUtils';

export const compactUiCopy = (value: unknown, maxLength = 118): string => {
    if (typeof value !== 'string') return '';
    const text = value.trim().replace(/\s+/g, ' ');
    if (!text || text.length <= maxLength) return text;
    const firstSentence = text.match(/^.{1,118}?[.!?](?:\s|$)/)?.[0]?.trim();
    if (firstSentence && firstSentence.length <= maxLength) return firstSentence;
    return `${text.slice(0, Math.max(1, maxLength - 1)).trimEnd()}…`;
};


// Reusable param field component for grouped rendering
const canonicalStructureSourceName = (target: SelectedTarget): string => {
    const base = (target.name || target.pdbId || 'structure_input')
        .replace(/[^\w.-]+/g, '_')
        .replace(/_+/g, '_')
        .replace(/^_+|_+$/g, '');
    return /\.(pdb|cif|mmcif)$/i.test(base) ? base : `${base}.pdb`;
};

export function ParamField({
    param,
    params,
    updateParam,
    setShowFileBrowser,
    setActiveSequenceField,
    setShowSequenceManager,
    setSequenceToSave,
    ligandPresets
}: {
    param: UntypedApiValue;
    params: Record<string, UntypedApiValue>;
    updateParam: (key: string, value: UntypedApiValue) => void;
    setShowFileBrowser: (name: string | null) => void;
    setActiveSequenceField: (name: string) => void;
    setShowSequenceManager: (show: boolean) => void;
    setSequenceToSave?: (sequence: { sequence: string; name?: string } | null) => void;
    ligandPresets: UntypedApiValue[];
}) {
    const isTextareaField = param.type === 'textarea' || param.ui_control === 'textarea' || param.preset_type === 'json';
    const isSequenceField = ['sequence', 'dna', 'rna'].includes(String(param.preset_type || '')) || param.name === 'sequence' || (param.type === 'text' && !isTextareaField);
    const isContigField = typeof param.name === 'string' && param.name.includes('contig');
    const isPdbPresetField = param.preset_type === 'pdb';
    const isPathField = param.type === 'file' || param.type === 'directory';
    const isNumericField = param.type === 'integer' || param.type === 'number';
    const isSliderField = param.ui_control === 'slider' && isNumericField && param.minimum !== undefined && param.maximum !== undefined;
    const isWide = isTextareaField || isSequenceField || isPathField || param.preset_type === 'ligand';
    const sequenceUnit = param.preset_type === 'dna' ? 'bp' : param.preset_type === 'rna' ? 'nt' : 'aa';
    const normalizeSequenceInput = (raw: string) => {
        const upper = raw.toUpperCase().replace(/\s+/g, '');
        if (param.preset_type === 'dna') return upper.replace(/U/g, 'T').replace(/[^ACGTN]/g, '');
        if (param.preset_type === 'rna') return upper.replace(/T/g, 'U').replace(/[^ACGUN]/g, '');
        return upper.replace(/[^A-Z]/g, '');
    };
    const label = param.label || param.name;
    const description = compactUiCopy(param.description, 112);
    const value = params[param.name] ?? param.default ?? (param.type === 'boolean' ? false : '');
    const numericValue = (() => {
        const parsed = Number(value);
        if (Number.isFinite(parsed)) return parsed;
        const fallback = Number(param.default ?? param.minimum ?? 0);
        return Number.isFinite(fallback) ? fallback : 0;
    })();
    const numericStep = param.step ?? (param.type === 'integer' ? 1 : 0.01);
    const pathPlaceholder = param.ui_placeholder || param.placeholder || (param.type === 'directory' ? '/path/to/directory' : '/path/to/file');
    const [showSequenceImportModal, setShowSequenceImportModal] = useState(false);
    const [sequenceImportTab, setSequenceImportTab] = useState<'library' | 'pdb'>('library');
    const [pdbImportTarget, setPdbImportTarget] = useState<SelectedTarget | null>(null);
    const [pdbParsedStructure, setPdbParsedStructure] = useState<ParsedPDB | null>(null);
    const [pdbParsedChains, setPdbParsedChains] = useState<Chain[]>([]);
    const [pdbImportError, setPdbImportError] = useState<string | null>(null);
    const applySequenceImport = (sequence: string, name?: string, chainId?: string) => {
        updateParam(param.name, normalizeSequenceInput(sequence));
        if (name) {
            updateParam('sequence_name', name.replace(/\.(pdb|cif|mmcif)$/i, ''));
        }
        if (chainId) {
            updateParam('chain_id', chainId);
            updateParam('primary_chain_id', chainId);
        }
    };
    const resetPdbImportState = () => {
        setPdbImportTarget(null);
        setPdbParsedStructure(null);
        setPdbParsedChains([]);
        setPdbImportError(null);
    };
    const openSequenceImport = (tab: 'library' | 'pdb') => {
        setSequenceImportTab(tab);
        if (tab === 'pdb') resetPdbImportState();
        setShowSequenceImportModal(true);
    };
    const handlePdbSequenceImportSelect = async (target: SelectedTarget | null) => {
        if (!target) return;
        setPdbImportError(null);
        try {
            let file: File;
            if (target.type === 'upload' && target.file) {
                file = target.file;
            } else if (target.url) {
                const response = await fetch(target.url);
                const blob = await response.blob();
                file = new File([blob], canonicalStructureSourceName(target), { type: blob.type || 'chemical/x-pdb' });
            } else {
                setPdbImportError('Selected structure has no downloadable source.');
                return;
            }
            const parsed = await parsePDBFile(file);
            if (parsed.chains.length === 0) {
                setPdbImportError('No protein chains found in selected PDB/mmCIF.');
                return;
            }
            setPdbImportTarget(target);
            setPdbParsedStructure(parsed);
            setPdbParsedChains(parsed.chains);
            if (parsed.chains.length === 1) {
                const chain = parsed.chains[0];
                applySequenceImport(chain.sequence, target.name || target.pdbId || `Chain ${chain.id}`, chain.id || 'A');
                setShowSequenceImportModal(false);
                resetPdbImportState();
            }
        } catch (err) {
            console.error('Failed to import PDB sequence:', err);
            setPdbImportError('Failed to parse PDB/mmCIF sequence.');
        }
    };
    const importPdbChain = (chain: Chain) => {
        applySequenceImport(chain.sequence, pdbImportTarget?.name || pdbImportTarget?.pdbId || `Chain ${chain.id}`, chain.id || 'A');
        setShowSequenceImportModal(false);
        resetPdbImportState();
    };
    const updateNumeric = (raw: string) => {
        const parsed = param.type === 'integer' ? Number.parseInt(raw, 10) : Number.parseFloat(raw);
        if (Number.isFinite(parsed)) {
            updateParam(param.name, parsed);
        } else {
            updateParam(param.name, param.default ?? param.minimum ?? 0);
        }
    };

    return (
        <div className={isWide ? 'col-span-full' : ''}>
            <label className="block text-sm font-medium text-slate-300 mb-1.5">
                {label}
                {param.required && <span className="text-red-400 ml-1">*</span>}
            </label>
            {description && <p className="text-xs text-slate-500 mb-2">{description}</p>}

            {param.type === 'boolean' ? (
                <label className="flex items-center gap-3 rounded-lg border border-slate-700/70 bg-slate-900/40 px-3 py-2.5 cursor-pointer hover:border-slate-500 transition-colors">
                    <input
                        type="checkbox"
                        checked={Boolean(value)}
                        onChange={(e) => updateParam(param.name, e.target.checked)}
                        className="h-4 w-4 rounded border-slate-600 bg-slate-900 text-blue-500 focus:ring-blue-500"
                    />
                    <span className="text-sm text-slate-200">{value ? 'Enabled' : 'Disabled'}</span>
                </label>
            ) : param.enum ? (
                <select
                    value={value ?? ''}
                    onChange={(e) => updateParam(param.name, e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                >
                    {param.enum.map((opt: string) => (
                        <option key={opt || '__empty'} value={opt}>{param.enum_labels?.[opt] || opt || 'Auto / none'}</option>
                    ))}
                </select>
            ) : isContigField ? (
                <PresetSelector
                    presetType="contig"
                    value={value || ''}
                    onChange={(val) => updateParam(param.name, val)}
                    onBrowse={() => { }}
                    placeholder={param.ui_placeholder || param.placeholder || "Select a contig preset..."}
                />
            ) : isSliderField ? (
                <div className="rounded-lg border border-blue-500/15 bg-blue-500/5 p-2.5 space-y-2" data-bms-template-slider="compact">
                    <div className="flex items-center justify-between gap-2 text-[11px] text-slate-500">
                        <span>Default {param.default ?? '—'}</span>
                        {param.recommended_range && <span>Typical {param.recommended_range}</span>}
                    </div>
                    <div className="flex items-center justify-between gap-2">
                        <span className="min-w-10 text-sm text-slate-200 font-medium">{numericValue}</span>
                        <input
                            type="number"
                            value={numericValue}
                            onChange={(e) => updateNumeric(e.target.value)}
                            min={param.minimum}
                            max={param.maximum}
                            step={numericStep}
                            className="w-24 bg-slate-950 border border-slate-700 rounded px-2 py-1 text-white text-sm text-right"
                        />
                    </div>
                    <input
                        type="range"
                        min={param.minimum}
                        max={param.maximum}
                        step={numericStep}
                        value={numericValue}
                        onChange={(e) => updateNumeric(e.target.value)}
                        className="w-full accent-blue-500"
                    />
                    <div className="flex justify-between text-[11px] text-slate-500">
                        <span>{param.minimum}</span>
                        <span>{param.maximum}</span>
                    </div>
                    {param.default_source && (
                        <div className="text-[11px] text-slate-500">{param.default_source}</div>
                    )}
                </div>
            ) : isTextareaField ? (
                <textarea
                    value={value || ''}
                    onChange={(e) => updateParam(param.name, e.target.value)}
                    rows={6}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                    placeholder={param.ui_placeholder || param.placeholder || ''}
                    data-bms-template-textarea="raw"
                />
            ) : isPdbPresetField ? (
                <StructureInput
                    value={value || ''}
                    onChange={(v) => updateParam(param.name, v)}
                    onBrowse={() => setShowFileBrowser(param.name)}
                    targetChain={params[param.name === 'pdb_sequence_path' ? 'pdb_chain_ids' : 'target_chain'] || params['chain_id'] || ''}
                    onTargetChainChange={(c) => updateParam(param.name === 'pdb_sequence_path' ? 'pdb_chain_ids' : 'target_chain', c)}
                    enableMultiSelect={false}
                    enableDirectory={param.type === 'directory'}
                />
            ) : param.preset_type === 'ligand' ? (
                <div className="space-y-2">
                    <select
                        value=""
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    >
                        <option value="">Select preset ligand...</option>
                        {ligandPresets.map((preset: UntypedApiValue) => (
                            <option key={preset.id} value={preset.smiles}>
                                {preset.name}
                            </option>
                        ))}
                    </select>
                    <input
                        type="text"
                        value={value || ''}
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder={param.ui_placeholder || "Or enter SMILES string..."}
                    />
                </div>
            ) : isSequenceField ? (
                <div className="space-y-2">
                    <div className="flex gap-2 items-center flex-wrap">
                        <button
                            type="button"
                            onClick={() => openSequenceImport('library')}
                            className="rounded-lg border border-emerald-600/30 bg-emerald-600/12 px-3 py-2 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-600/20"
                        >
                            Sequence Library
                        </button>
                        <button
                            type="button"
                            onClick={() => openSequenceImport('pdb')}
                            className="rounded-lg border border-blue-600/30 bg-blue-600/12 px-3 py-2 text-sm font-medium text-blue-300 transition-colors hover:bg-blue-600/20"
                        >
                            Import from PDB
                        </button>
                        <span className="text-xs text-slate-500 bg-slate-800/50 px-2 py-1 rounded">
                            {String(value || '').length} {sequenceUnit}
                        </span>
                        {String(value || '').length > 0 && setSequenceToSave && (
                            <button
                                type="button"
                                onClick={() => {
                                    setSequenceToSave({ sequence: String(value || ''), name: params['sequence_name'] || params['job_name'] || '' });
                                    setActiveSequenceField(param.name);
                                    setShowSequenceManager(true);
                                }}
                                className="rounded-lg border border-emerald-600/30 bg-emerald-600/12 px-3 py-2 text-sm font-medium text-emerald-300 transition-colors hover:bg-emerald-600/20"
                            >
                                Save to Library
                            </button>
                        )}
                        {String(value || '').length > 0 && (
                            <button
                                type="button"
                                onClick={() => updateParam(param.name, '')}
                                className="px-2 py-2 text-slate-500 hover:text-red-400 text-sm transition-colors"
                                title="Clear sequence"
                            >
                                ✕
                            </button>
                        )}
                    </div>
                    <textarea
                        value={value || ''}
                        onChange={(e) => updateParam(param.name, normalizeSequenceInput(e.target.value))}
                        rows={6}
                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono resize-y focus:ring-2 focus:ring-blue-500 outline-none"
                        placeholder={param.ui_placeholder || param.placeholder || 'Enter amino acid sequence...'}
                    />
                    {showSequenceImportModal && (
                        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/60 p-4 backdrop-blur-sm" data-bms-sequence-pdb-import-modal="true">
                            <div className="flex h-[80vh] max-h-[85vh] w-full max-w-4xl flex-col rounded-xl border border-slate-700 bg-slate-900 shadow-2xl">
                                <div className="flex border-b border-slate-700 bg-slate-800/50 rounded-t-xl">
                                    <button
                                        type="button"
                                        onClick={() => setSequenceImportTab('library')}
                                        className={`flex-1 border-b-2 py-4 text-sm font-medium transition-colors ${sequenceImportTab === 'library' ? 'border-emerald-500 bg-slate-800 text-emerald-400' : 'border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'}`}
                                    >
                                        Sequence Library
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => setSequenceImportTab('pdb')}
                                        className={`flex-1 border-b-2 py-4 text-sm font-medium transition-colors ${sequenceImportTab === 'pdb' ? 'border-blue-500 bg-slate-800 text-blue-400' : 'border-transparent text-slate-400 hover:bg-slate-800/50 hover:text-slate-200'}`}
                                    >
                                        Import from PDB
                                    </button>
                                    <button
                                        type="button"
                                        onClick={() => {
                                            setShowSequenceImportModal(false);
                                            resetPdbImportState();
                                        }}
                                        className="rounded-tr-xl px-5 text-slate-400 transition-colors hover:bg-slate-800/50 hover:text-white"
                                    >
                                        ✕
                                    </button>
                                </div>
                                <div className="relative flex-1 overflow-hidden">
                                    {sequenceImportTab === 'library' && (
                                        <div className="absolute inset-0 overflow-auto p-5">
                                            <SequenceManager
                                                onSelect={(seq) => {
                                                    applySequenceImport(seq.sequence, seq.name, params['chain_id'] || 'A');
                                                    setShowSequenceImportModal(false);
                                                }}
                                                initialSequence={String(value || '')}
                                                initialName={String(params['sequence_name'] || params['job_name'] || '')}
                                                onClose={() => setShowSequenceImportModal(false)}
                                            />
                                        </div>
                                    )}
                                    {sequenceImportTab === 'pdb' && (
                                        <div className="absolute inset-0 overflow-auto p-5">
                                            {pdbImportError && (
                                                <div className="mb-3 rounded-lg border border-red-500/40 bg-red-500/10 px-3 py-2 text-sm text-red-200">
                                                    {pdbImportError}
                                                </div>
                                            )}
                                            {pdbParsedChains.length > 0 ? (
                                                <div className="space-y-4">
                                                    <div className="flex items-center justify-between">
                                                        <div>
                                                            <h3 className="text-lg font-medium text-slate-200">Select PDB Chain</h3>
                                                            <p className="text-sm text-slate-500">
                                                                {pdbParsedStructure?.models?.length ? `${pdbParsedStructure.models.length} model(s); ` : ''}
                                                                import one monomer chain into this workflow.
                                                            </p>
                                                        </div>
                                                        <button
                                                            type="button"
                                                            onClick={resetPdbImportState}
                                                            className="text-sm text-slate-400 hover:text-white"
                                                        >
                                                            ← Back to PDB connector
                                                        </button>
                                                    </div>
                                                    <div className="grid gap-2">
                                                        {pdbParsedChains.map((chain, idx) => (
                                                            <button
                                                                key={`${chain.id}-${idx}`}
                                                                type="button"
                                                                onClick={() => importPdbChain(chain)}
                                                                className="flex items-center justify-between rounded-lg border border-slate-700 bg-slate-800 p-3 text-left transition-colors hover:border-blue-500 hover:bg-blue-600/15"
                                                            >
                                                                <span className="font-medium text-slate-200">Chain {chain.id}</span>
                                                                <span className="rounded bg-slate-900/70 px-2 py-1 font-mono text-xs text-slate-400">{chain.sequence.length} aa</span>
                                                            </button>
                                                        ))}
                                                    </div>
                                                </div>
                                            ) : (
                                                <TargetAntigenSelector
                                                    onSelect={handlePdbSequenceImportSelect}
                                                    selectedTarget={pdbImportTarget}
                                                />
                                            )}
                                        </div>
                                    )}
                                </div>
                            </div>
                        </div>
                    )}
                </div>
            ) : isPathField ? (
                <div className="flex gap-2">
                    <input
                        type="text"
                        value={value || ''}
                        onChange={(e) => updateParam(param.name, e.target.value)}
                        className="flex-1 bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm font-mono outline-none"
                        placeholder={pathPlaceholder}
                    />
                    <button
                        type="button"
                        onClick={() => setShowFileBrowser(param.name)}
                        className="px-3 py-2 bg-slate-700 hover:bg-slate-600 rounded-lg text-slate-200 text-sm transition-colors"
                    >
                        Browse
                    </button>
                </div>
            ) : (
                <input
                    type={isNumericField ? 'number' : 'text'}
                    value={value ?? ''}
                    onChange={(e) => isNumericField ? updateNumeric(e.target.value) : updateParam(param.name, e.target.value)}
                    className="w-full bg-slate-900 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-blue-500 outline-none"
                    placeholder={param.ui_placeholder || param.placeholder || ''}
                    min={param.minimum}
                    max={param.maximum}
                    step={numericStep}
                />
            )}
        </div>
    );
}
