

import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { completeCurrentLaunchContext, fetchModels, fetchFiles, submitJob, uploadFile, fetchTemplates, fetchTemplateById, fetchInputPresets, type Job } from '../lib/api';
import { getLaunchContext } from '../lib/projectManager';
import { SequenceManagerModal } from './SequenceManagerModal';
import { SequenceManager } from './SequenceManager';
import { TemplateManagerModal } from './TemplateManagerModal';
import { MutagenesisTemplate } from './MutagenesisTemplate';
import { AntibodyDenovoTemplate } from './AntibodyDenovoTemplate';

import { StructurePredictionTemplate } from './StructurePredictionTemplate';


import { OligoDesignerTemplate } from './OligoDesignerTemplate';
import { ProteinModificationTemplate } from './ProteinModificationTemplate';

import { MolecularDynamicsTemplate } from './MolecularDynamicsTemplate';
import { ConformationalMappingLauncher } from './conformationalMapping/ConformationalMappingLauncher';
import { PresetSelector } from './PresetSelector';
import { LigandSelector, type LigandEntry } from './LigandSelector';
import { StructureInput } from './StructureInput';
import { TargetAntigenSelector, type SelectedTarget } from './TargetAntigenSelector';
import { parsePDBFile, type Chain, type ParsedPDB } from '../utils/pdbUtils';
import { ModelDocumentationLinks, getModelDocumentationLinks, type ModelDocumentationTopic } from './ModelDocumentationLinks';
import { getDedicatedTemplateInitialValues, isDedicatedLauncherTemplate } from './jobSubmissionTemplateState.js';
import { getWorkflowModelTopics } from './workflowModelInventory.js';
import { isAntibodyPipelineMode } from '../lib/antibodyModes';
import { ModelIntegrationControl, useModelIntegrationConfig } from './ModelIntegrationControl';
import { FrustraMpnnSettingsPanel } from './frustrampnn/FrustraMpnnSettingsPanel.js';
import {
    hydrateFrustraMpnnSettings,
    mergeFrustraMpnnLaunchParams,
    resolveFrustraMpnnWorkflowId,
    type FrustraMpnnRequestedSettings,
} from './frustrampnn/frustraMpnnSettingsState.js';


const LEGACY_PROTEIN_MODIFICATION_TEMPLATE_IDS = new Set([
    'protein_cad_experimental',
    'protein_local_redesign',
    'protein_hunter_experimental',
]);

const LEGACY_CONFORMATIONAL_MAPPING_TEMPLATE_IDS = new Set([
    'confornets_experimental',
]);

interface FileBrowserProps {
    onSelect: (path: string) => void;
    onCancel: () => void;
}

function FileBrowser({ onSelect, onCancel }: FileBrowserProps) {
    const [path, setPath] = useState('/');
    const fileInputRef = useRef<HTMLInputElement>(null);
    const queryClient = useQueryClient();

    const { data: files } = useQuery({
        queryKey: ['files', path],
        queryFn: () => fetchFiles(path),
    });

    const uploadMutation = useMutation({
        mutationFn: (file: File) => uploadFile(path === '/' ? 'inputs' : path, file),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['files', path] });
        },
    });

    const handleNavigate = (newPath: string) => {
        setPath(newPath);
    };

    const handleUp = () => {
        const parts = path.split('/').filter(p => p);
        parts.pop();
        setPath('/' + parts.join('/'));
    };

    const handleUploadClick = () => {
        if (fileInputRef.current) {
            fileInputRef.current.click();
        }
    };

    const handleFileChange = (e: React.ChangeEvent<HTMLInputElement>) => {
        if (e.target.files && e.target.files[0]) {
            uploadMutation.mutate(e.target.files[0]);
        }
        // Reset input
        e.target.value = '';
    };

    return (
        <div className="fixed inset-0 bg-black/50 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-2xl h-[80vh] flex flex-col shadow-2xl">
                <div className="p-4 border-b border-slate-700 flex justify-between items-center bg-slate-800/50 rounded-t-xl">
                    <h3 className="font-semibold text-slate-200">Select File</h3>
                    <div className="flex gap-3 items-center">
                        <input
                            type="file"
                            ref={fileInputRef}
                            onChange={handleFileChange}
                            className="hidden"
                        />
                        <button
                            onClick={handleUploadClick}
                            disabled={uploadMutation.isPending}
                            className="rounded-md border border-slate-600 bg-slate-800 px-3 py-1.5 text-xs font-medium text-slate-200 transition-colors hover:bg-slate-700"
                        >
                            {uploadMutation.isPending ? 'Uploading...' : 'Upload'}
                        </button>
                        <button onClick={onCancel} className="text-slate-400 hover:text-white">✕</button>
                    </div>
                </div>

                <div className="p-2 border-b border-slate-700 bg-slate-800/30 flex items-center gap-2">
                    <button
                        onClick={handleUp}
                        className="rounded border border-slate-600 bg-slate-800 px-2.5 py-1 text-sm font-medium text-slate-300 hover:bg-slate-700 disabled:opacity-50"
                        disabled={path === '/'}
                    >
                        Up
                    </button>
                    <input
                        type="text"
                        value={path}
                        readOnly
                        className="flex-1 bg-transparent text-sm text-slate-400 outline-none"
                    />
                </div>

                <div className="flex-1 overflow-auto p-2">
                    {files?.data.entries.map((entry: UntypedApiValue) => (
                        <div
                            key={entry.path}
                            onClick={() => entry.is_directory ? handleNavigate(entry.path) : onSelect(entry.path)}
                            className={`flex items-center gap-3 p-2 rounded cursor-pointer ${entry.is_directory
                                ? 'text-blue-400 hover:bg-blue-500/10'
                                : 'text-slate-300 hover:bg-slate-700'
                                }`}
                        >
                            <span className={`inline-flex h-7 min-w-10 items-center justify-center rounded border text-[10px] font-semibold uppercase tracking-[0.14em] ${
                                entry.is_directory
                                    ? 'border-blue-500/30 bg-blue-500/10 text-blue-300'
                                    : 'border-slate-600 bg-slate-800 text-slate-300'
                            }`}>
                                {entry.is_directory ? 'Dir' : 'File'}
                            </span>
                            <span className="flex-1 truncate">{entry.name}</span>
                            {!entry.is_directory && (
                                <span className="text-xs text-slate-500">
                                    {(entry.size_bytes / 1024).toFixed(1)} KB
                                </span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        </div>
    );
}

const compactUiCopy = (value: unknown, maxLength = 118): string => {
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

function ParamField({
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

const MODEL_DOCUMENTATION_TOPIC_KEYS = new Set<ModelDocumentationTopic>([
    'alphafold2', 'boltz2', 'boltzgen', 'caliby', 'chai1', 'confornets', 'diffdock', 'disco', 'esmfold2',
    'fampnn', 'fold_cp', 'laproteina', 'ligandmpnn', 'ppiflow', 'protein_hunter', 'proteinmpnn', 'protenix', 'rf3',
    'rfantibody', 'rfdiffusion', 'rfdpoly', 'unidock',
]);

const getTemplateDocumentationTopics = (
    template: UntypedApiValue | null | undefined,
    launchParams?: UntypedApiValue | null,
): ModelDocumentationTopic[] => {
    const selectedTopic = String(
        launchParams?.workflow_model_topic
        || launchParams?.model_documentation_topic
        || launchParams?.mapping_model
        || template?.preset_params?.workflow_model_topic
        || template?.preset_params?.model_documentation_topic
        || ''
    ).trim() as ModelDocumentationTopic;
    if (selectedTopic && MODEL_DOCUMENTATION_TOPIC_KEYS.has(selectedTopic)) return [selectedTopic];

    const workflowTopics = getWorkflowModelTopics(template?.id || template?.model_id);
    if (workflowTopics.length > 0) return workflowTopics;

    const identity = `${template?.id || ''} ${template?.model_id || ''} ${template?.name || ''}`.toLowerCase();
    if (identity.includes('boltz_cp_experimental') || identity.includes('fold-cp')) return ['fold_cp', 'boltz2'];
    if (identity.includes('confornets')) return ['confornets'];
    if (identity.includes('esmfold2')) return ['esmfold2'];
    if (identity.includes('protein_modification_experimental') || identity.includes('protein_local_redesign') || identity.includes('local redesign')) return ['laproteina', 'disco', 'rfdiffusion', 'fampnn', 'proteinmpnn', 'boltz2'];
    if (identity.includes('antibody_denovo') || identity.includes('nanobody') || identity.includes('rfantibody')) return ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2'];

    if (identity.includes('structure_prediction') || identity.includes('structure prediction')) return ['boltz2', 'rf3', 'protenix', 'esmfold2'];
    if (identity.includes('boltz')) return ['boltz2'];
    if (identity.includes('rfdiffusion') || identity.includes('diffusion')) return ['rfdiffusion'];
    return [];
};

const getModelDocumentationTopics = (model: UntypedApiValue | null | undefined): ModelDocumentationTopic[] => {
    const workflowTopics = getWorkflowModelTopics(model?.id);
    if (workflowTopics.length > 0) return workflowTopics;

    const identity = `${model?.id || ''} ${model?.name || ''} ${model?.category || ''}`.toLowerCase();
    if (identity.includes('boltz_cp_experimental') || identity.includes('fold-cp')) return ['fold_cp', 'boltz2'];
    if (identity.includes('confornets')) return ['confornets'];
    if (identity.includes('esmfold2')) return ['esmfold2'];
    if (identity.includes('protenix')) return ['protenix'];
    if (identity.includes('rf3') || identity.includes('rosettafold')) return ['rf3'];
    if (identity.includes('antibody') || identity.includes('rfantibody')) return ['rfantibody', 'boltzgen', 'ppiflow', 'fampnn', 'caliby', 'proteinmpnn', 'protenix', 'boltz2', 'esmfold2'];
    if (identity.includes('rfdiffusion')) return ['rfdiffusion'];
    if (identity.includes('boltzgen')) return ['boltzgen'];
    if (identity.includes('boltz2') || identity.includes('boltz-2')) return ['boltz2'];
    return [];
};

const getCompactTemplateDescription = (template: UntypedApiValue): string => {
    switch (template.id) {
        case 'structure_prediction':
            return 'Predict proteins, nucleic acids, and complexes.';
        case 'antibody_denovo':
            return 'Generate, refine, validate, and review nanobody candidates.';

        case 'protein_modification_experimental':
        case 'protein_local_redesign':
            return 'Generate new proteins or remodel selected regions of an existing structure.';
        case 'boltz_cp_experimental':
            return 'Experimental Fold-CP path for large Boltz-2 folds.';
        case 'confornets_experimental':
        case 'conformational_mapping':
            return 'Complete-complex Protenix v2 ensembles, canonical ConforNets/import alternatives, residue mapping, FrustraMPNN landscapes, and support-ranked comparison.';
        case 'esmfold2':
        case 'esmfold2_experimental':
            return 'ESMFold2 engine inside Structure Prediction.';
        case 'mutagenesis':
            return 'Build variant libraries and predict structures.';
        case 'oligo_design':
            return 'Design nucleoprotein assemblies with validation.';
        default:
            return template.short_description || template.summary || template.description || '';
    }
};

const getCompactModelDescription = (model: UntypedApiValue): string => {
    switch (model.id) {
        case 'boltz2':
            return 'Structure and complex prediction validator.';
        case 'boltz_cp_experimental':
            return 'Experimental Fold-CP large-protein path.';
        case 'confornets_experimental':
            return 'Canonical conformational mapping workflow.';
        case 'esmfold2':
        case 'esmfold2_experimental':
            return 'Local all-atom protein and complex folding.';
        case 'antibody_denovo':
            return 'Nanobody generation and refinement toolkit.';

        case 'rfdiffusion':
            return 'Backbone generation and local redesign.';
        default:
            return model.short_description || model.summary || model.description || '';
    }
};

export function JobSubmission() {
    const queryClient = useQueryClient();
    const navigate = useNavigate();
    const [searchParams, setSearchParams] = useSearchParams();
    const launchContextId = searchParams.get('launch_context_id');
    const launchContextQuery = useQuery({
        queryKey: ['launch-context', launchContextId],
        queryFn: ({ signal }) => getLaunchContext(launchContextId as string, signal),
        enabled: Boolean(launchContextId),
        retry: false,
    });
    const recoveredLaunchRef = useRef<string | null>(null);
    useEffect(() => {
        const recoveryJobId = launchContextQuery.data?.recovery_job_id;
        if (!recoveryJobId || recoveredLaunchRef.current === recoveryJobId) return;
        recoveredLaunchRef.current = recoveryJobId;
        void completeCurrentLaunchContext({ id: recoveryJobId }).then((returnUri) => {
            if (returnUri) navigate(returnUri);
        });
    }, [launchContextQuery.data?.recovery_job_id, navigate]);
    const [wizardMode, setWizardMode] = useState<'templates' | 'experimental' | 'manual'>('templates');

    // Read template from URL, allows page refresh and bookmarking
    const urlTemplate = searchParams.get('template');
    const initialTemplateId = urlTemplate === 'protein_local_redesign'
        ? 'protein_modification_experimental'
        : urlTemplate === 'confornets_experimental' ? 'conformational_mapping' : urlTemplate;
    const [selectedTemplateId, setSelectedTemplateIdInternal] = useState<string | null>(initialTemplateId);

    // Wrapper to sync state with URL
    const setSelectedTemplateId = useCallback((id: string | null) => {
        const canonicalId = id === 'confornets_experimental' ? 'conformational_mapping' : id;
        setSelectedTemplateIdInternal(canonicalId);
        const next = new URLSearchParams(searchParams);
        if (canonicalId) {
            next.set('template', canonicalId);
        } else {
            next.delete('template');
        }
        setSearchParams(next, { replace: true });
    }, [searchParams, setSearchParams]);
    const [selectedModelId, setSelectedModelId] = useState<string | null>(null);
    const [selectedModeId, setSelectedModeId] = useState<string | null>(null);
    const [jobName, setJobName] = useState('');
    const [params, setParams] = useState<Record<string, UntypedApiValue>>({});
    const frustrampnnIntegrationQuery = useModelIntegrationConfig('frustrampnn');
    const [explicitRunFrustrampnn, setExplicitRunFrustrampnn] = useState<boolean | undefined>(undefined);
    const [frustrampnnSettings, setFrustrampnnSettings] = useState<FrustraMpnnRequestedSettings>(() => (
        hydrateFrustraMpnnSettings(undefined)
    ));
    const [showFileBrowser, setShowFileBrowser] = useState<string | null>(null);
    const [showSequenceManager, setShowSequenceManager] = useState(false);
    const [showTemplateManager, setShowTemplateManager] = useState(false);
    const [sequenceToSave, setSequenceToSave] = useState<{ sequence: string; name?: string } | null>(null);
    const [activeSequenceField, setActiveSequenceField] = useState<string>('sequence');
    const [ligands, setLigands] = useState<LigandEntry[]>([]);
    const [showAdvanced, setShowAdvanced] = useState(false);
    const [clonedValues, setClonedValues] = useState<Record<string, UntypedApiValue> | undefined>(undefined);
    const [dedicatedTemplateVersion, setDedicatedTemplateVersion] = useState(0);
    const [templateManagerContext, setTemplateManagerContext] = useState<{
        currentParams?: Record<string, UntypedApiValue>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }>({});

    const openTemplateManager = (context: {
        currentParams?: Record<string, UntypedApiValue>;
        currentModelId?: string;
        currentMode?: string;
        baseTemplateId?: string;
    }) => {
        setTemplateManagerContext(context);
        setShowTemplateManager(true);
    };

    // Dedicated templates should not retain stale clone params once user navigates away.
    const handleDedicatedTemplateBack = () => {
        setSelectedTemplateId(null);
        setClonedValues(undefined);
    };

    const handleTemplateCardSelect = (templateId: string) => {
        setClonedValues(getDedicatedTemplateInitialValues(templateId));
        if (isDedicatedLauncherTemplate(templateId)) {
            setDedicatedTemplateVersion((prev) => prev + 1);
        }
        setSelectedTemplateId(templateId);
    };

    // Check for cloned job data on mount
    useEffect(() => {
        const stored = localStorage.getItem('clonedJobData');
        if (stored) {
            try {
                const data = JSON.parse(stored);
                console.log('Loading cloned job data:', data);

                // Set common fields
                if (data.name) setJobName(data.name);

                // Determine routing
                if (data.mode === 'antibody_denovo' || isAntibodyPipelineMode(data.mode) || data.params?.antibody_pipeline_steps) {
                    setWizardMode('templates');
                    setSelectedTemplateId('antibody_denovo');
                    setClonedValues({ ...data.params, name: data.name });
                }
                else if (data.params?.mutagenesis_variants) {
                    setWizardMode('templates');
                    setSelectedTemplateId('mutagenesis');
                    // Mutagenesis logic might need updates for pre-filling too, but focusing on Antibody first
                }
                // 3. Boltz-CP experimental reuses the structure-prediction template with a fixed launch variant.
                else if (data.model_id === 'boltz_cp_experimental') {
                    setWizardMode('experimental');
                    setSelectedTemplateId('boltz_cp_experimental');
                    setClonedValues({
                        ...data.params,
                        name: data.name,
                        template_model_id: 'boltz_cp_experimental',
                        structure_launch_variant: data.params?.structure_launch_variant || 'boltz_cp_experimental',
                    });
                }
                // 4. ESMFold2 compatibility IDs reopen the parent Structure Prediction workflow.
                else if (data.model_id === 'esmfold2' || data.model_id === 'esmfold2_experimental') {
                    setWizardMode('templates');
                    setSelectedTemplateId('structure_prediction');
                    setClonedValues({
                        ...data.params,
                        name: data.name,
                        pred_method: 'esmfold2',
                    });
                }

                else if (data.model_id === 'boltzgen') {
                    setWizardMode('templates');
                    setSelectedTemplateId('antibody_denovo');
                    setClonedValues({ ...data.params, name: data.name, denovo_generator: 'boltzgen' });
                }

                // 6. Legacy and canonical conformational-mapping jobs reopen the one published canonical launcher.
                else if (data.model_id === 'confornets_experimental' || data.params?.template_model_id === 'confornets_experimental') {
                    sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
                    setWizardMode('templates');
                    setSelectedTemplateId('conformational_mapping');
                    setClonedValues({
                        ...(getDedicatedTemplateInitialValues('conformational_mapping') || {}),
                        name: data.name,
                        backend: 'confornets',
                    });
                    setJobName(data.name || data.params?.job_name || data.params?.sequence_name || '');
                }
                else if (data.model_id === 'conformational_mapping') {
                    sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
                    setWizardMode('templates');
                    setSelectedTemplateId('conformational_mapping');
                    setClonedValues({
                        ...(getDedicatedTemplateInitialValues('conformational_mapping') || {}),
                        ...data.params,
                        name: data.name || data.params?.name || 'Conformational mapping',
                    });
                    setJobName(data.name || 'Conformational mapping');
                }
                else if (data.model_id === 'protein_local_redesign' || data.params?.template_model_id === 'protein_local_redesign') {
                    setWizardMode('experimental');
                    setSelectedTemplateId('protein_modification_experimental');
                    setClonedValues({
                        ...data.params,
                        pinned_gpu: data.pinned_gpu,
                        name: data.name,
                        modification_mode: 'rfd3_local_redesign',
                        template_model_id: 'protein_local_redesign',
                    });
                }
                // 7. Manual Mode
                else {
                    setWizardMode('manual');
                    setSelectedModelId(data.model_id);
                    setSelectedModeId(data.mode);
                    setClonedValues(data.params);
                    setParams(data.params);
                }

                // Clear storage
                localStorage.removeItem('clonedJobData');
            } catch (e) {
                console.error("Failed to parse cloned job data", e);
            }
        }
    }, [setSelectedTemplateId]);

    useEffect(() => {
        setExplicitRunFrustrampnn(
            typeof params.run_frustrampnn === 'boolean' ? params.run_frustrampnn : undefined,
        );
        setFrustrampnnSettings(hydrateFrustraMpnnSettings(params.frustrampnn_settings));
    }, [params.run_frustrampnn, params.frustrampnn_settings]);

    const { data: modelsData } = useQuery({
        queryKey: ['models'],
        queryFn: () => fetchModels(),
    });

    const { data: templatesData } = useQuery({
        queryKey: ['templates'],
        queryFn: () => fetchTemplates(),
    });

    // Dedicated launcher templates that use specialized components instead of API-driven config
    const dedicatedTemplateByModelId: Record<string, string> = {
        template_antibody_denovo: 'antibody_denovo',
        boltzgen: 'antibody_denovo',

        protein_modification_experimental: 'protein_modification_experimental',
        protein_local_redesign: 'protein_modification_experimental',
        protein_cad_experimental: 'protein_modification_experimental',
        boltz_cp_experimental: 'boltz_cp_experimental',
        conformational_mapping: 'conformational_mapping',
        confornets_experimental: 'conformational_mapping',
        esmfold2: 'structure_prediction',
        esmfold2_experimental: 'structure_prediction',
    };
    const hardcodedWorkflowTemplates = useMemo(() => [
        {
            id: 'mutagenesis',
            name: 'Mutagenesis Library',
            description: 'Build variant libraries and predict structures.',
            icon: 'dna',
            color: '#8B5CF6',
            stages: [{ tool: 'Library Gen' }, { tool: 'Structure Prediction' }],
        },
        {
            id: 'structure_prediction',
            name: 'Structure Prediction',
            description: 'Predict proteins, nucleic acids, and complexes.',
            icon: 'microscope',
            color: '#F59E0B',
            stages: [{ tool: 'Boltz-2 / RF3 / Protenix' }],
        },

        {
            id: 'antibody_denovo',
            name: 'De Novo Nanobody Toolkit',
            description: 'Generate, refine, validate, and review nanobody candidates.',
            icon: 'flask',
            color: '#14B8A6',
            stages: [
                { tool: 'RFantibody / BoltzGen / PPIFlow' },
                { tool: 'FAMPNN' },
                { tool: 'PPIFlow (Opt.)' },
                { tool: 'Protenix / Boltz2 / ESMFold2' },
                { tool: 'Review + QC' }
            ],
        },

        {
            id: 'oligo_design',
            name: 'Oligo Designer',
            description: 'Design nucleoprotein assemblies with validation.',
            icon: 'dna',
            color: '#6366F1',
            stages: [{ tool: 'RFDpoly' }, { tool: 'Boltz-2' }, { tool: 'Filtering' }],
        },
    ], []);
    const hardcodedExperimentalTemplates = useMemo(() => [
        {
            id: 'protein_modification_experimental',
            name: 'De Novo Design',
            description: 'Generate new proteins or modify selected regions of an existing structure.',
            icon: 'cube',
            color: '#22C55E',
            experimental: true,
            stages: [
                { tool: 'DISCO / La-Proteina' },
                { tool: 'RFdiffusion3' },
                { tool: 'FAMPNN / ProteinMPNN' },
                { tool: 'Boltz-2 (Opt.)' },
            ],
        },

    ], []);
    const visibleApiTemplates = useMemo(() => {
        const templates = templatesData?.data ?? [];
        return templates.filter((t: UntypedApiValue) =>
            !['structure_validation', 'structure_prediction'].includes(t.id) &&
            t.id !== 'binder_design' &&
            !LEGACY_PROTEIN_MODIFICATION_TEMPLATE_IDS.has(t.id) &&
            !LEGACY_CONFORMATIONAL_MAPPING_TEMPLATE_IDS.has(t.id) &&
            (t.id !== 'dna_polymerase' || (window as UntypedApiValue).__DEBUG_MODE__)
        );
    }, [templatesData]);
    const workflowTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: UntypedApiValue) => !t.experimental), ...hardcodedWorkflowTemplates],
        [hardcodedWorkflowTemplates, visibleApiTemplates]
    );
    const experimentalTemplateCards = useMemo(
        () => [...visibleApiTemplates.filter((t: UntypedApiValue) => t.experimental), ...hardcodedExperimentalTemplates],
        [hardcodedExperimentalTemplates, visibleApiTemplates]
    );

    const routeUserTemplate = (template: UntypedApiValue) => {
        const rawApiTemplateId = typeof template.base_template_id === 'string' ? template.base_template_id : null;
        const apiTemplateId = rawApiTemplateId === 'confornets_experimental' ? 'conformational_mapping' : rawApiTemplateId;
        const matchedApiTemplate = apiTemplateId
            ? visibleApiTemplates.find((candidate: UntypedApiValue) => candidate.id === apiTemplateId)
            : null;
        if (matchedApiTemplate) {
            const loadedJobName = template.params?.job_name || template.params?.name || template.name || '';
            if (apiTemplateId === 'conformational_mapping') {
                sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
            }
            setWizardMode(matchedApiTemplate.experimental ? 'experimental' : 'templates');
            setSelectedTemplateId(apiTemplateId);
            setClonedValues({
                ...(matchedApiTemplate.preset_params || {}),
                ...(template.params || {}),
                job_name: loadedJobName,
                name: loadedJobName,
            });
            setParams({ ...(matchedApiTemplate.preset_params || {}), ...(template.params || {}) });
            setJobName(loadedJobName);
            setSelectedModelId(null);
            setSelectedModeId(null);
            return;
        }

        const dedicatedTemplateId =
            (isDedicatedLauncherTemplate(apiTemplateId) && apiTemplateId) ||
            (template.model_id ? dedicatedTemplateByModelId[template.model_id] : null);

        if (dedicatedTemplateId) {
            const loadedJobName = template.params?.job_name || template.params?.name || template.name || '';
            const templateModelId = template.model_id || template.params?.template_model_id;
            const isLegacyEsmfold2 = templateModelId === 'esmfold2' || templateModelId === 'esmfold2_experimental';
            const isLegacyBoltzGen = templateModelId === 'boltzgen';
            if (dedicatedTemplateId === 'conformational_mapping') {
                sessionStorage.removeItem('bms.conformational-mapping.launcher.v1');
            }
            setWizardMode(dedicatedTemplateId === 'boltz_cp_experimental' ? 'experimental' : 'templates');
            setSelectedTemplateId(dedicatedTemplateId);
            setDedicatedTemplateVersion((prev) => prev + 1);
            setClonedValues({
                ...template.params,
                name: loadedJobName,
                job_name: loadedJobName,
                template_model_id: isLegacyEsmfold2 || isLegacyBoltzGen ? undefined : templateModelId,
                modification_mode: templateModelId === 'protein_local_redesign'
                    ? 'rfd3_local_redesign'
                    : template.params?.modification_mode,
                ...(isLegacyEsmfold2 ? { pred_method: 'esmfold2' } : {}),
                ...(isLegacyBoltzGen ? { denovo_generator: 'boltzgen' } : {}),
                structure_launch_variant: dedicatedTemplateId === 'boltz_cp_experimental'
                    ? (template.params?.structure_launch_variant || 'boltz_cp_experimental')
                    : template.params?.structure_launch_variant,
            });
            setJobName(loadedJobName);
            setSelectedModelId(null);
            setSelectedModeId(null);
            setParams({});
            return;
        }

        setWizardMode('manual');
        setSelectedTemplateId(null);
        setClonedValues(undefined);
        setParams(template.params || {});
        if (template.model_id) setSelectedModelId(template.model_id);
        if (template.mode) setSelectedModeId(template.mode);
        setJobName(template.params?.job_name || template.name || '');
    };

    const { data: selectedTemplateData } = useQuery({
        queryKey: ['template', selectedTemplateId],
        queryFn: () => selectedTemplateId ? fetchTemplateById(selectedTemplateId) : null,
        // Skip fetch for hardcoded templates - they don't exist in the API
        enabled: !!selectedTemplateId && !isDedicatedLauncherTemplate(selectedTemplateId),
    });
    const templateDetail = selectedTemplateData?.data?.data ?? selectedTemplateData?.data;

    // Fetch ligand presets for dynamic dropdown
    const { data: ligandPresetsData } = useQuery({
        queryKey: ['presets', 'ligand'],
        queryFn: () => fetchInputPresets('ligand'),
    });
    const ligandPresets = ligandPresetsData?.data ?? [];

    const submitMutation = useMutation({
        mutationFn: (jobData: Partial<Job>) => submitJob(jobData),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            const returnUri = response.data?.return_uri;
            if (typeof returnUri === 'string' && returnUri.startsWith('/projects/') && !returnUri.startsWith('//')) {
                navigate(returnUri);
            } else {
                navigate('/');
            }
        },
        onError: (error: UntypedApiValue) => {
            console.error('Job submission failed:', error);
            const detail = error.response?.data?.detail;
            const message = typeof detail === 'object'
                ? JSON.stringify(detail, null, 2)
                : (detail || error.message || error);
            window.alert('Job Submission Failed:\n' + message);
        }
    });

    const models = (modelsData?.data ?? []).filter((model: UntypedApiValue) => !['protein_modification_experimental', 'protein_cad_experimental', 'protein_local_redesign', 'caliby_experimental', 'protein_hunter_experimental', 'boltz_cp_experimental', 'confornets_experimental', 'conformational_mapping', 'esmfold2', 'esmfold2_experimental'].includes(model.id));
    const selectedModel = models.find((m: UntypedApiValue) => m.id === selectedModelId);
    const selectedMode = selectedModel?.modes.find((m: UntypedApiValue) => m.id === selectedModeId);
    const resolvedFrustrampnnWorkflowId = useMemo(() => {
        if (wizardMode === 'manual') {
            return resolveFrustraMpnnWorkflowId(selectedModelId, selectedModeId);
        }
        if (!selectedTemplateId || isDedicatedLauncherTemplate(selectedTemplateId) || !templateDetail) {
            return null;
        }

        const launchParams = { ...templateDetail.preset_params, ...params };
        let modelId = launchParams.template_model_id;
        let modeId = launchParams.template_mode_id;
        if (!(modelId && modeId) && launchParams.rfd_mode) {
            modelId = 'rfdiffusion';
            modeId = launchParams.rfd_mode;
        } else if (
            !(modelId && modeId)
            && launchParams.diffusion_method === 'boltzgen'
            && ligands.some((ligand) => ligand.type === 'dna' || ligand.type === 'rna')
        ) {
            modelId = 'boltz2';
            modeId = 'complex';
        }
        return resolveFrustraMpnnWorkflowId(modelId, modeId);
    }, [wizardMode, selectedModelId, selectedModeId, selectedTemplateId, templateDetail, params, ligands]);
    const configuredFrustrampnnWorkflow = resolvedFrustrampnnWorkflowId
        ? frustrampnnIntegrationQuery.data?.workflows?.[resolvedFrustrampnnWorkflowId]
        : undefined;
    const frustrampnnConfigurationReady = !resolvedFrustrampnnWorkflowId || (
        !frustrampnnIntegrationQuery.isFetching
        && !frustrampnnIntegrationQuery.isError
        && configuredFrustrampnnWorkflow !== undefined
    );
    const runFrustrampnn = explicitRunFrustrampnn
        ?? configuredFrustrampnnWorkflow?.default_enabled
        ?? false;

    // Initialize params when model/mode changes (manual mode)
    useEffect(() => {
        if (selectedModel) {
            const defaults: Record<string, UntypedApiValue> = {};
            (selectedModel.params || []).forEach((p: UntypedApiValue) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            const nextParams = { ...defaults, ...(clonedValues || {}) };
            setParams(nextParams);
        }
    }, [selectedModel, selectedModelId, clonedValues]);

    // Initialize params when template changes (template mode)
    useEffect(() => {
        if (templateDetail?.user_params) {
            const defaults: Record<string, UntypedApiValue> = {};
            templateDetail.user_params.forEach((p: UntypedApiValue) => {
                if (p.default !== undefined) defaults[p.name] = p.default;
            });
            const nextParams = { ...defaults, ...(clonedValues || {}) };
            setParams(nextParams);
            const defaultTemplateName = nextParams.job_name || nextParams.name || nextParams.sequence_name || templateDetail.name || selectedTemplateId || '';
            if (defaultTemplateName) {
                setJobName(prev => (clonedValues?.name || clonedValues?.job_name) ? String(defaultTemplateName) : (prev.trim() ? prev : String(defaultTemplateName)));
            }
        }
    }, [templateDetail, selectedTemplateId, clonedValues]);

    useEffect(() => {
        if (!selectedTemplateId || isDedicatedLauncherTemplate(selectedTemplateId)) {
            return;
        }
        const matchedTemplate = visibleApiTemplates.find((template: UntypedApiValue) => template.id === selectedTemplateId);
        if (matchedTemplate) {
            setWizardMode(matchedTemplate.experimental ? 'experimental' : 'templates');
        }
    }, [selectedTemplateId, visibleApiTemplates]);

    // Handle param change
    const updateParam = (key: string, value: UntypedApiValue) => {
        setParams(prev => ({ ...prev, [key]: value }));
    };

    const getTemplateIconLabel = (template: UntypedApiValue) => {
        if (template.id === 'protein_cad_experimental') return 'PC';


        if (template.id === 'boltz_cp_experimental') return 'CP';
        if (template.id === 'confornets_experimental') return 'CM';
        if (template.id === 'conformational_mapping') return 'CM';

        return template.icon === 'target' ? 'TG'
            : template.icon === 'flask' ? 'RF'
                : template.icon === 'dna' ? 'MU'
                    : template.icon === 'microscope' ? 'SP'
                        : template.icon === 'pill' ? 'BG'
                            : template.icon === 'binder' ? 'BC'
                                : template.icon === 'cube' ? 'PL'
                                    : 'OL';
    };

    const renderTemplateCard = (template: UntypedApiValue) => {
        const isSelected = selectedTemplateId === template.id;
        const docTopics = getTemplateDocumentationTopics(template);
        const docLinks = getModelDocumentationLinks(docTopics);
        return (
            <div
                key={template.id}
                onClick={() => handleTemplateCardSelect(template.id)}
                className={`cursor-pointer rounded-lg border-2 p-4 transition-all ${
                    template.experimental
                        ? isSelected
                            ? 'border-orange-400/60 bg-orange-500/10 shadow-xl'
                            : 'border-orange-500/25 bg-orange-500/5 hover:border-orange-400/50 hover:shadow-lg'
                        : isSelected
                            ? 'scale-[1.02] border-[var(--accent-primary)] shadow-xl'
                            : 'border-[var(--border-primary)] hover:scale-[1.01] hover:border-[var(--border-secondary)] hover:shadow-lg'
                } bg-[var(--card-bg)] text-[var(--text-primary)]`}
                style={{
                    boxShadow: isSelected
                        ? template.experimental
                            ? '0 10px 34px rgba(251, 146, 60, 0.18)'
                            : '0 8px 30px color-mix(in srgb, var(--accent-primary) 35%, transparent)'
                        : undefined
                }}
            >
                <div className="mb-2 flex items-center justify-between gap-3">
                    <div className="flex items-center gap-3">
                        <div
                            className="flex h-10 w-10 items-center justify-center rounded text-sm font-bold"
                            style={{ backgroundColor: `${template.color}20`, color: template.color }}
                        >
                            {getTemplateIconLabel(template)}
                        </div>
                        <h3 className="font-bold text-base" style={{ color: template.color }}>{template.name}</h3>
                    </div>
                    {template.experimental && (
                        <span className="rounded-full border border-orange-400/25 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                            Experimental Alpha
                        </span>
                    )}
                </div>
                <p className="mb-2 text-xs opacity-70 line-clamp-2">{getCompactTemplateDescription(template)}</p>
                {docLinks.length > 0 && (
                    <div className="group/docs relative mb-2 inline-block" onClick={(event) => event.stopPropagation()}>
                        <span
                            className="inline-flex rounded-md border border-slate-600/80 bg-slate-900/70 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-300 transition-colors group-hover/docs:border-blue-400/60 group-hover/docs:text-blue-200"
                            aria-haspopup="true"
                        >
                            Docs ({docLinks.length})
                        </span>
                        <div
                            data-bms-workflow-doc-hover="true"
                            className="pointer-events-none absolute left-0 top-full z-30 mt-1 hidden min-w-56 max-w-[min(28rem,80vw)] flex-wrap gap-1 rounded-lg border border-slate-700/80 bg-slate-950/95 p-2 shadow-2xl group-hover/docs:flex group-hover/docs:pointer-events-auto"
                        >
                            {docLinks.map((link) => (
                                <a
                                    key={link.href}
                                    href={link.href}
                                    target="_blank"
                                    rel="noreferrer"
                                    onClick={(event) => event.stopPropagation()}
                                    className="inline-flex rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-[10px] font-medium text-slate-200 transition-colors hover:border-blue-400/60 hover:text-blue-200"
                                >
                                    {link.label}
                                </a>
                            ))}
                        </div>
                    </div>
                )}
                <div className="flex items-center gap-0.5 flex-wrap text-[10px]">
                    {template.stages.map((stage: UntypedApiValue, idx: number) => (
                        <div key={idx} className="flex items-center">
                            <span
                                className="rounded px-1.5 py-0.5 font-medium"
                                style={{ backgroundColor: `${template.color}15`, color: template.color }}
                            >
                                {stage.tool}
                            </span>
                            {idx < template.stages.length - 1 && (
                                <span className="mx-0.5 opacity-40" style={{ color: template.color }}>→</span>
                            )}
                        </div>
                    ))}
                </div>
            </div>
        );
    };

    const getModelCardBadge = (model: UntypedApiValue) => {
        const identity = `${model.id ?? ''} ${model.name ?? ''}`.toLowerCase();
        if (identity.includes('proteinmpnn') || identity.includes('ligandmpnn') || identity.includes('fampnn') || identity.includes('full-atom mpnn')) {
            return 'SEQ';
        }
        if (identity.includes('rfantibody') || identity.includes('antibody')) {
            return 'BIND';
        }
        if (identity.includes('boltz2') || identity.includes('alphafold') || identity.includes('rosettafold') || identity.includes('protenix') || identity.includes('rf3')) {
            return 'FOLD';
        }
        if (identity.includes('boltzgen')) {
            return 'GEN';
        }
        if (identity.includes('diffdock') || identity.includes('uni-dock') || identity.includes('unidock')) {
            return 'DOCK';
        }
        if (identity.includes('nanopore') || identity.includes('ngs')) {
            return 'NGS';
        }
        if (identity.includes('oligo') || identity.includes('dna') || identity.includes('rna')) {
            return 'NA';
        }
        if (identity.includes('rfdiffusion') || identity.includes('redesign') || identity.includes('design')) {
            return 'DES';
        }
        return model.ui_icon === 'cube' ? '3D' : 'ML';
    };

    // Filter params for current mode
    const visibleParams = useMemo(() => (selectedModel?.params || []).filter((p: UntypedApiValue) => {
        if (!selectedMode) return false;
        if (selectedMode.params && selectedMode.params.length > 0) {
            return selectedMode.params.includes(p.name);
        }
        return !p.hidden;
    }) ?? [], [selectedMode, selectedModel?.params]);

    // Group visible params by ui_group
    const groupedParams = useMemo(() => {
        const groups: Record<string, UntypedApiValue[]> = {};
        visibleParams.forEach((p: UntypedApiValue) => {
            const group = p.ui_group || 'General';
            if (!groups[group]) groups[group] = [];
            groups[group].push(p);
        });
        // Sort params within each group by ui_order
        Object.values(groups).forEach(grp => {
            grp.sort((a, b) => (a.ui_order ?? 99) - (b.ui_order ?? 99));
        });
        return groups;
    }, [visibleParams]);

    // Check if ready to submit - works for both template mode and manual mode
    const isTemplateMode = wizardMode === 'templates' || wizardMode === 'experimental';
    const templateLaunchName = String(jobName || params.job_name || params.sequence_name || templateDetail?.name || selectedTemplateId || '').trim();

    const visibleTemplateParams = useMemo(() => {
        if (!templateDetail?.user_params) return [];
        return templateDetail.user_params.filter((param: UntypedApiValue) => {
            if (!param.condition) return true;
            const controllingParam = templateDetail.user_params.find((p: UntypedApiValue) => p.name === param.condition.param);
            const controllingValue = params[param.condition.param] !== undefined
                ? params[param.condition.param]
                : controllingParam?.default;
            return param.condition.values.includes(controllingValue);
        });
    }, [templateDetail, params]);

    const groupedTemplateParams = useMemo(() => {
        const groups: Record<string, UntypedApiValue[]> = {};
        visibleTemplateParams.forEach((p: UntypedApiValue) => {
            const group = p.ui_group || 'General';
            if (!groups[group]) groups[group] = [];
            groups[group].push(p);
        });
        Object.values(groups).forEach(grp => {
            grp.sort((a, b) => (a.ui_order ?? 99) - (b.ui_order ?? 99));
        });
        return groups;
    }, [visibleTemplateParams]);

    const templateManagerParams = useMemo(() => {
        const baseParams = isTemplateMode && templateDetail
            ? {
                ...(templateDetail.preset_params || {}),
                ...params,
                job_name: templateLaunchName,
            }
            : params;
        return configuredFrustrampnnWorkflow
            ? mergeFrustraMpnnLaunchParams(baseParams, runFrustrampnn, frustrampnnSettings)
            : baseParams;
    }, [
        isTemplateMode,
        params,
        templateDetail,
        templateLaunchName,
        configuredFrustrampnnWorkflow,
        runFrustrampnn,
        frustrampnnSettings,
    ]);

    const missingRequiredTemplateParams = isTemplateMode && templateDetail?.user_params
        ? templateDetail.user_params
            .filter((param: UntypedApiValue) => param.required)
            .filter((param: UntypedApiValue) => {
                const value = params[param.name] ?? param.default;
                return value === undefined || value === null || String(value).trim() === '';
            })
            .map((param: UntypedApiValue) => param.label || param.name)
        : [];
    const allMissingRequiredTemplateParams = missingRequiredTemplateParams;
    const isReady = frustrampnnConfigurationReady && Boolean(
        (isTemplateMode && selectedTemplateId && templateLaunchName && templateDetail && allMissingRequiredTemplateParams.length === 0) ||
        (wizardMode === 'manual' && jobName && selectedModelId && selectedModeId)
    );
    const launchBlockedReason = !frustrampnnConfigurationReady
        ? 'FrustraMPNN integration configuration is unavailable. Launch is blocked.'
        : !isReady
        ? (isTemplateMode && selectedTemplateId && allMissingRequiredTemplateParams.length > 0
            ? `Required: ${allMissingRequiredTemplateParams.join(', ')}`
            : 'Select a workflow and complete required fields')
        : '';

    const handleSubmit = () => {
        if (!isReady) return;

        // Get template data - handle both axios response wrapper and direct data
        const templateData = templateDetail;

        if (isTemplateMode && templateData) {
            // Template mode: merge preset params with user params
            const mergedParams = { ...templateData.preset_params, ...params };
            const templateModelIdOverride = mergedParams.template_model_id;
            const templateModeIdOverride = mergedParams.template_mode_id;
            delete mergedParams.template_model_id;
            delete mergedParams.template_mode_id;

            // Determine the Nextflow profile based on template type
            // Priority: rfd_mode (binder/monomer) > diffusion_method (boltzgen) > pred_method (structure prediction/validation) > skip_rfd (fampnn_predict)
            let nextflowProfile = '';
            let effectiveModelId = 'template_' + (selectedTemplateId || 'unknown');

            if (templateModelIdOverride && templateModeIdOverride) {
                effectiveModelId = templateModelIdOverride;
                nextflowProfile = templateModeIdOverride;
            } else if (mergedParams.rfd_mode) {
                // Binder or monomer design templates
                nextflowProfile = mergedParams.rfd_mode;
                effectiveModelId = 'rfdiffusion';
            } else if (mergedParams.diffusion_method === 'boltzgen') {
                // Check if this is complex PREDICTION (DNA/RNA present) vs DESIGN
                const hasNucleicAcid = ligands.some(l => l.type === 'dna' || l.type === 'rna');
                if (hasNucleicAcid) {
                    // DNA/RNA complex prediction - use Boltz-2, NOT BoltzGen
                    nextflowProfile = 'complex';
                    effectiveModelId = 'boltz2';
                } else {
                    // BoltzGen ligand-aware binder design template
                    nextflowProfile = 'boltzgen';
                    effectiveModelId = 'boltzgen';
                }
            } else if (mergedParams.pred_method) {
                // Structure prediction templates - map pred_method to model_id and mode
                const predMethodMap: Record<string, { model_id: string; mode: string }> = {
                    'boltz': { model_id: 'boltz2', mode: 'predict' },
                    'rf3': { model_id: 'rf3', mode: 'predict' },
                    'protenix': { model_id: 'protenix', mode: 'predict' },
                    'both': { model_id: 'boltz2', mode: 'predict' }, // Primary model for "both" mode
                    'all': { model_id: 'boltz2', mode: 'predict' },  // Primary model for "all" mode
                };
                const mapping = predMethodMap[mergedParams.pred_method];
                if (mapping) {
                    effectiveModelId = mapping.model_id;
                    nextflowProfile = mapping.mode;
                } else {
                    nextflowProfile = mergedParams.pred_method;
                }
            } else if (mergedParams.skip_rfd === true) {
                // DNA polymerase or similar - skip diffusion, just sequence design + prediction
                nextflowProfile = 'fampnn_predict';
                effectiveModelId = 'proteinmpnn';
            } else {
                // Fallback to template ID
                nextflowProfile = selectedTemplateId || 'binder_denovo';
            }

            const governedMergedParams = resolvedFrustrampnnWorkflowId
                ? mergeFrustraMpnnLaunchParams(mergedParams, runFrustrampnn, frustrampnnSettings)
                : mergedParams;

            console.log('DEBUG params state:', params);
            console.log('DEBUG num_parallel_jobs from params:', params.num_parallel_jobs);
            console.log('DEBUG mergedParams:', governedMergedParams);
            console.log('DEBUG num_parallel_jobs from mergedParams:', governedMergedParams.num_parallel_jobs);
            console.log('Submitting job:', { name: templateLaunchName, model_id: effectiveModelId, mode: nextflowProfile, params: governedMergedParams });

            // Add complex_components if ligands are selected
            const finalParams = ligands.length > 0 ? {
                ...governedMergedParams,
                complex_components: [
                    { type: 'protein', id: 'A', sequence: governedMergedParams.sequence || params.sequence },
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence, name: l.name }))
                ]
            } : governedMergedParams;

            submitMutation.mutate({
                name: templateLaunchName,
                model_id: effectiveModelId,
                mode: nextflowProfile,
                params: finalParams,
            });
        } else if (selectedModelId && selectedModeId) {
            // Manual mode

            // Filter params to only include those defined in the selected mode
            const filteredParams: Record<string, UntypedApiValue> = {};
            if (selectedMode && selectedMode.params) {
                selectedMode.params.forEach((paramName: string) => {
                    if (params[paramName] !== undefined && params[paramName] !== '') {
                        filteredParams[paramName] = params[paramName];
                    }
                });
            } else {
                // Fallback if no params defined in mode (shouldn't happen for well-defined models)
                Object.assign(filteredParams, params);
            }

            // specific check for ntp_type to ensure it's not sent if empty even if in params list
            if (filteredParams['ntp_type'] === '') {
                delete filteredParams['ntp_type'];
            }

            const governedFilteredParams = resolvedFrustrampnnWorkflowId
                ? mergeFrustraMpnnLaunchParams(filteredParams, runFrustrampnn, frustrampnnSettings)
                : filteredParams;

            // Add complex_components if ligands are selected (e.g. for Complex Prediction)
            const proteinSeq = governedFilteredParams.sequence || governedFilteredParams.protein_sequence;

            const finalParams = ligands.length > 0 ? {
                ...governedFilteredParams,
                complex_components: [
                    { type: 'protein', id: 'A', sequence: proteinSeq },
                    ...ligands.map(l => ({ type: l.type, id: l.id, ccd: l.ccd, smiles: l.smiles, sequence: l.sequence, name: l.name }))
                ]
            } : governedFilteredParams;

            submitMutation.mutate({
                name: jobName,
                model_id: selectedModelId,
                mode: selectedModeId,
                params: finalParams,
            });
        } else {
            console.error('Submit failed: Template data not loaded or invalid mode', { wizardMode, templateData, selectedTemplateData });
        }
    };

    const genericFrustrampnnControl = resolvedFrustrampnnWorkflowId ? (
        <div
            className="rounded-xl border border-cyan-900/70 bg-cyan-950/15 p-4"
            data-job-submission-frustrampnn
        >
            {configuredFrustrampnnWorkflow ? (
                <ModelIntegrationControl
                    modelId="frustrampnn"
                    workflowId={resolvedFrustrampnnWorkflowId}
                    checked={runFrustrampnn}
                    onChange={(checked) => {
                        setExplicitRunFrustrampnn(checked);
                        setParams((previous) => ({ ...previous, run_frustrampnn: checked }));
                    }}
                    fallbackLabel="Frustration analysis"
                    integration={frustrampnnIntegrationQuery.data}
                    settingsControl={(
                        <FrustraMpnnSettingsPanel
                            value={frustrampnnSettings}
                            onChange={(settings) => {
                                setFrustrampnnSettings(settings);
                                setParams((previous) => ({ ...previous, frustrampnn_settings: settings }));
                            }}
                        />
                    )}
                />
            ) : (
                <p role="alert" className="text-sm text-amber-300">
                    FrustraMPNN integration configuration is unavailable. Launch is blocked.
                </p>
            )}
        </div>
    ) : null;

    // Dedicated templates that handle their own header/navigation
    const dedicatedTemplates = ['mutagenesis', 'antibody_denovo', 'structure_prediction', 'boltz_cp_experimental', 'oligo_design', 'protein_modification_experimental', 'molecular_dynamics', 'conformational_mapping'];
    const showMainHeader = !selectedTemplateId || !dedicatedTemplates.includes(selectedTemplateId);

    return (
        <div className="min-h-screen bg-slate-950 p-6">
            {launchContextId && (
                <aside className="mb-4 rounded-lg border border-blue-500/40 bg-blue-950/40 px-4 py-3 text-sm text-blue-100" aria-label="Project launch destination">
                    {launchContextQuery.isLoading && 'Resolving Project launch destination…'}
                    {launchContextQuery.isError && 'Project launch destination is invalid, expired, claimed, or unavailable.'}
                    {launchContextQuery.data && (
                        <>
                            <div className="font-semibold">Verified Project launch destination</div>
                            <div className="mt-1 font-mono text-xs">
                                Project {launchContextQuery.data.project_id} · Global Experiment {launchContextQuery.data.global_experiment_id} · Domain Experiment {launchContextQuery.data.domain_experiment_id}
                            </div>
                        </>
                    )}
                </aside>
            )}
            {/* Main header - hidden when dedicated templates are active */}
            {showMainHeader && (
                <header className="mb-8 flex items-center gap-4">
                    <Link
                        to="/"
                        className="inline-flex items-center rounded-lg border border-slate-700 px-3 py-2 text-sm font-medium text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
                    >
                        Back
                    </Link>
                    <div>
                        <h1 className="text-2xl font-bold bg-gradient-to-r from-blue-400 to-accent bg-clip-text text-transparent">
                            New Experiment
                        </h1>
                        <p className="text-slate-400 text-sm">Configure and launch a new job</p>
                    </div>
                </header>
            )}

            <main className="max-w-[104rem] mx-auto space-y-8">

                {/* 2. Mode Toggle: workflow cards only; the raw model-picker tab stays hidden for now. */}
                <section>
                    <div className="flex gap-2 mb-4">
                        <button
                            onClick={() => {
                                setWizardMode('templates');
                                setSelectedModelId(null);
                                setSelectedModeId(null);
                                setSelectedTemplateId(null);
                                setClonedValues(undefined);
                            }}
                            className={`min-w-[9.5rem] rounded-lg border px-4 py-2.5 text-sm font-medium transition-all ${wizardMode === 'templates'
                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-300'
                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                                }`}
                        >
                            Workflows
                        </button>
                        <button
                            onClick={() => {
                                setWizardMode('experimental');
                                setSelectedModelId(null);
                                setSelectedModeId(null);
                                setSelectedTemplateId(null);
                                setClonedValues(undefined);
                            }}
                            className={`min-w-[9.5rem] rounded-lg border px-4 py-2.5 text-sm font-medium transition-all ${wizardMode === 'experimental'
                                ? 'border-orange-400/40 bg-orange-500/12 text-orange-300'
                                : 'border-slate-700 bg-slate-900/60 text-slate-300 hover:bg-slate-800'
                                }`}
                        >
                            Experimental
                        </button>
                    </div>

                    {/* Templates Mode */}
                    {(wizardMode === 'templates' || wizardMode === 'experimental') && (
                        <div className="space-y-4">
                            {selectedTemplateId === 'mutagenesis' ? (
                                <MutagenesisTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    onSubmit={async (jobNamePrefix, variants, predictorConfig) => {
                                        // MUTAGENESIS BATCH: Single API call with all variants
                                        // Each variant regenerates its own MSA (no shared reference MSA)

                                        console.log('[MUTAGENESIS BATCH] Submitting', variants.length, 'variants as single batch');
                                        if (predictorConfig.msa_reference_sequence) {
                                            console.log('[MUTAGENESIS BATCH] Ignoring reference MSA (mutants regenerate MSAs)');
                                        }

                                        // Build params with mutagenesis_variants array
                                        const batchParams = {
                                            // Always regenerate MSAs for mutants (no shared reference MSA)
                                            msa_force_refresh: true,
                                            // Array of variants (each with name + sequence)
                                            mutagenesis_variants: variants.map(v => ({
                                                name: v.name,
                                                sequence: v.sequence
                                            })),
                                            // Predictor params (same for all variants)
                                            boltz_recycling_steps: predictorConfig.recycling_steps,
                                            boltz_num_samples: predictorConfig.diffusion_samples,
                                            boltz_sampling_steps: predictorConfig.sampling_steps,
                                            boltz_use_msa: predictorConfig.use_msa,
                                            boltz_use_potentials: predictorConfig.use_potentials,
                                            boltz_step_scale: predictorConfig.step_scale,
                                            pred_method: predictorConfig.predictor,
                                            run_frustrampnn: predictorConfig.run_frustrampnn,
                                            // Complex components: ligands array now includes DNA/RNA with sequence field
                                            ...(predictorConfig.ligands?.length ? {
                                                ligands: predictorConfig.ligands
                                            } : {})
                                        };

                                        try {
                                            await submitMutation.mutateAsync({
                                                name: jobNamePrefix,
                                                model_id: predictorConfig.predictor === 'rf3'
                                                    ? 'rf3'
                                                    : predictorConfig.predictor === 'esmfold2'
                                                        ? 'esmfold2'
                                                        : 'boltz2',
                                                mode: 'predict',
                                                params: batchParams
                                            });
                                            queryClient.invalidateQueries({ queryKey: ['jobs'] });
                                        } catch (error) {
                                            console.error("[MUTAGENESIS BATCH] Submission failed", error);
                                        }
                                    }}
                                />
                            ) : selectedTemplateId === 'antibody_denovo' ? (
                                <AntibodyDenovoTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'structure_prediction' || selectedTemplateId === 'boltz_cp_experimental' ? (
                                <StructurePredictionTemplate
                                    key={`${selectedTemplateId}:${dedicatedTemplateVersion}`}
                                    onBack={handleDedicatedTemplateBack}
                                    onOpenTemplateManager={openTemplateManager}
                                    initialValues={selectedTemplateId === 'boltz_cp_experimental'
                                        ? {
                                            ...(getDedicatedTemplateInitialValues('boltz_cp_experimental') || {}),
                                            ...(templateDetail?.preset_params || {}),
                                            ...(clonedValues || {}),
                                        }
                                        : clonedValues}
                                />
                            ) : selectedTemplateId === 'oligo_design' ? (
                                <OligoDesignerTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'protein_modification_experimental' ? (
                                <ProteinModificationTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                    requiredPinnedGpu={launchContextQuery.data?.pinned_gpu ?? null}
                                />

                            ) : selectedTemplateId === 'molecular_dynamics' ? (
                                <MolecularDynamicsTemplate
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={clonedValues}
                                />
                            ) : selectedTemplateId === 'conformational_mapping' ? (
                                <ConformationalMappingLauncher
                                    key={`conformational_mapping:${dedicatedTemplateVersion}`}
                                    onBack={handleDedicatedTemplateBack}
                                    initialValues={{
                                        ...(getDedicatedTemplateInitialValues('conformational_mapping') || {}),
                                        ...(templateDetail?.preset_params || {}),
                                        ...(clonedValues || {}),
                                    }}
                                />
                            ) : (
                                <>
                                    <p className="text-slate-300 text-base font-medium mb-4">
                                        {wizardMode === 'experimental'
                                            ? 'Choose active alpha:'
                                            : 'Choose workflow:'}
                                    </p>
                                    {wizardMode === 'experimental' && (
                                        <div className="rounded-xl border border-orange-400/20 bg-orange-500/8 px-4 py-3 text-sm text-orange-100">
                                            <span className="font-semibold text-orange-200">Alpha:</span> launch controls here; method docs linked.
                                        </div>
                                    )}
                                    <div className="grid grid-cols-2 gap-3">
                                        {(wizardMode === 'experimental' ? experimentalTemplateCards : workflowTemplateCards).map((template: UntypedApiValue) =>
                                            renderTemplateCard(template)
                                        )}
                                    </div>
                                    {wizardMode === 'experimental' && experimentalTemplateCards.length === 0 && (
                                        <div className="rounded-xl border border-slate-700 bg-slate-900/50 px-4 py-5 text-sm text-slate-400">
                                            No experimental workflows are currently exposed in this branch.
                                        </div>
                                    )}
                                </>
                            )}
                        </div>
                    )}

                    {/* Manual Mode: Select Model */}
                    {wizardMode === 'manual' && (
                        <div>
                            <label className="block text-sm font-medium text-slate-400 mb-2">Select Model</label>
                            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
                                {models.map((model: UntypedApiValue) => {
                                    const modelDocLinks = getModelDocumentationLinks(getModelDocumentationTopics(model));
                                    return (
                                    <div
                                        key={model.id}
                                        onClick={() => {
                                            setClonedValues(undefined);
                                            setSelectedModelId(model.id);
                                            setSelectedModeId(null); // Reset mode
                                        }}
                                        className={`cursor-pointer p-4 rounded-xl border transition-all relative overflow-hidden group ${selectedModelId === model.id
                                            ? 'bg-slate-800 border-blue-500 shadow-lg shadow-blue-500/10'
                                            : 'bg-slate-800/30 border-slate-700 hover:border-slate-600 hover:bg-slate-800/50'
                                            }`}
                                    >
                                        <div className="flex justify-between items-start mb-2">
                                            <div
                                                className="w-10 h-10 rounded-lg flex items-center justify-center text-lg shadow-inner"
                                                style={{ backgroundColor: `${model.ui_color}20`, color: model.ui_color }}
                                            >
                                                {getModelCardBadge(model)}
                                            </div>
                                            {model.experimental && (
                                                <span className="text-[10px] uppercase font-bold text-orange-400 bg-orange-400/10 px-2 py-0.5 rounded-full">
                                                    Experimental
                                                </span>
                                            )}
                                        </div>
                                        <h3 className="font-semibold text-slate-200 mb-1">{model.name}</h3>
                                        <p className="text-xs text-slate-500 line-clamp-2">{getCompactModelDescription(model)}</p>
                                        {modelDocLinks.length > 0 && (
                                            <div className="group/docs relative mt-2 inline-block" onClick={(event) => event.stopPropagation()}>
                                                <span
                                                    className="inline-flex rounded-md border border-slate-600/80 bg-slate-900/70 px-2 py-1 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-300 transition-colors group-hover/docs:border-blue-400/60 group-hover/docs:text-blue-200"
                                                    aria-haspopup="true"
                                                >
                                                    Docs ({modelDocLinks.length})
                                                </span>
                                                <div
                                                    data-bms-model-doc-hover="true"
                                                    className="pointer-events-none absolute left-0 top-full z-30 mt-1 hidden min-w-56 max-w-[min(28rem,80vw)] flex-wrap gap-1 rounded-lg border border-slate-700/80 bg-slate-950/95 p-2 shadow-2xl group-hover/docs:flex group-hover/docs:pointer-events-auto"
                                                >
                                                    {modelDocLinks.map((link) => (
                                                        <a
                                                            key={link.href}
                                                            href={link.href}
                                                            target="_blank"
                                                            rel="noreferrer"
                                                            onClick={(event) => event.stopPropagation()}
                                                            className="inline-flex rounded-md border border-slate-600 bg-slate-900 px-2 py-1 text-[10px] font-medium text-slate-200 transition-colors hover:border-blue-400/60 hover:text-blue-200"
                                                        >
                                                            {link.label}
                                                        </a>
                                                    ))}
                                                </div>
                                            </div>
                                        )}
                                    </div>
                                    );
                                })}
                            </div>
                        </div>
                    )}
                </section>

                {/* 3. Template Configuration - Only show if template selected and NOT a dedicated template */}
                {selectedTemplateId && !isDedicatedLauncherTemplate(selectedTemplateId) && templateDetail && (
                    <section className="animate-in fade-in slide-in-from-bottom-4 duration-500" data-bms-template-config-shell="true">
                        <div className="rounded-2xl border border-slate-700 bg-slate-900/45 p-6 shadow-xl">
                            <div className="mb-6 flex flex-wrap items-start justify-between gap-4">
                                <div>
                                    <button
                                        type="button"
                                        onClick={handleDedicatedTemplateBack}
                                        className="mb-3 inline-flex items-center rounded-lg border border-slate-700 px-3 py-1.5 text-xs font-medium text-slate-300 transition-colors hover:bg-slate-800 hover:text-white"
                                    >
                                        ← Back to workflows
                                    </button>
                                    <h2 className="flex items-center gap-2 text-xl font-semibold text-slate-100">
                                        <span className={`h-6 w-1.5 rounded-full ${templateDetail.experimental ? 'bg-orange-400' : 'bg-emerald-500'}`} />
                                        {templateDetail.name}
                                        {templateDetail.experimental && (
                                            <span className="rounded-full border border-orange-400/25 bg-orange-500/10 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-orange-300">
                                                Experimental Alpha
                                            </span>
                                        )}
                                    </h2>
                                    <p className="mt-1 max-w-3xl text-sm text-slate-400">
                                        {compactUiCopy(templateDetail.description || templateDetail.goal || '', 170)}
                                    </p>
                                </div>
                                <ModelDocumentationLinks
                                    topics={getTemplateDocumentationTopics(templateDetail, params)}
                                    summary="Model docs update from the selected workflow model; launch controls stay here."
                                    compact
                                    className="min-w-[18rem] flex-1 md:max-w-xl"
                                />
                            </div>

                            <div className="mb-6 flex flex-wrap items-center gap-2 border-y border-slate-800 py-3 text-[11px]">
                                {templateDetail.stages.map((stage: UntypedApiValue, idx: number) => (
                                    <div key={idx} className="flex items-center gap-2">
                                        <span className="rounded-md bg-slate-800 px-2 py-1 font-medium text-slate-300">
                                            {stage.tool}
                                        </span>
                                        {idx < templateDetail.stages.length - 1 && (
                                            <span className="text-slate-600">→</span>
                                        )}
                                    </div>
                                ))}
                            </div>

                            <div className="space-y-6">
                                {['Inputs', 'Mapping Mode', 'Model Orchestration', 'Outputs', 'Advanced'].filter(group => groupedTemplateParams[group]).map((groupName) => (
                                    <div key={groupName} className={groupName === 'Advanced' ? 'rounded-xl border border-slate-700/60' : ''}>
                                        {groupName === 'Advanced' ? (
                                            <button
                                                type="button"
                                                onClick={() => setShowAdvanced(!showAdvanced)}
                                                className="flex w-full items-center justify-between rounded-xl bg-slate-800/45 px-4 py-3 text-left transition-colors hover:bg-slate-800/70"
                                            >
                                                <span className="flex items-center gap-2 text-sm font-medium text-slate-300">
                                                    <span className="h-4 w-1 rounded-full bg-slate-500" />
                                                    Advanced / Runtime Paths
                                                </span>
                                                <span className="text-xs text-slate-500">{showAdvanced ? '▲' : '▼'}</span>
                                            </button>
                                        ) : (
                                            <h3 className="mb-3 flex items-center gap-2 text-sm font-semibold text-slate-300">
                                                <span className={`h-4 w-1 rounded-full ${groupName === 'Inputs' ? 'bg-emerald-500' : groupName === 'Outputs' ? 'bg-purple-500' : 'bg-blue-500'}`} />
                                                {groupName}
                                            </h3>
                                        )}
                                        {(groupName !== 'Advanced' || showAdvanced) && (
                                            <div className={`${groupName === 'Advanced' ? 'p-4 ' : ''}grid grid-cols-1 gap-4 md:grid-cols-2`}>
                                                {groupedTemplateParams[groupName].map((param: UntypedApiValue) => (
                                                    <ParamField
                                                        key={param.name}
                                                        param={param}
                                                        params={params}
                                                        updateParam={updateParam}
                                                        setShowFileBrowser={setShowFileBrowser}
                                                        setActiveSequenceField={setActiveSequenceField}
                                                        setShowSequenceManager={setShowSequenceManager}
                                                        setSequenceToSave={setSequenceToSave}
                                                        ligandPresets={ligandPresets}
                                                    />
                                                ))}
                                            </div>
                                        )}
                                    </div>
                                ))}
                            </div>

                            {genericFrustrampnnControl}

                            {(templateDetail?.preset_params?.pred_method ||
                                selectedTemplateId?.includes('structure') ||
                                selectedTemplateId?.includes('predict')) && (
                                    <LigandSelector
                                        ligands={ligands}
                                        setLigands={setLigands}
                                        showCustomSmiles={true}
                                    />
                                )}
                        </div>
                    </section>
                )}

                {/* 4. Configure - Only show if model selected (Manual Mode) */}
                {wizardMode === 'manual' && selectedModel && (
                    <section className="animate-in fade-in slide-in-from-bottom-4 duration-500">
                        <div className="bg-slate-800/30 border border-slate-700 rounded-xl p-6">
                            <h2 className="text-lg font-semibold text-slate-200 mb-6 flex items-center gap-2">
                                <span className="w-1.5 h-6 bg-blue-500 rounded-full" />
                                Configuration
                            </h2>

                            <ModelDocumentationLinks
                                topics={getModelDocumentationTopics(selectedModel)}
                                summary="Docs linked; launch controls here."
                                compact
                                className="mb-6"
                            />

                            <div className="space-y-6">
                                {/* Mode Selection */}
                                <div>
                                    <label className="block text-sm font-medium text-slate-400 mb-2">
                                        Workflow Mode
                                    </label>
                                    <select
                                        value={selectedModeId || ''}
                                        onChange={(e) => setSelectedModeId(e.target.value)}
                                        className="w-full bg-slate-900 border border-slate-700 rounded-lg px-4 py-2.5 text-white focus:ring-2 focus:ring-blue-500 outline-none"
                                    >
                                        <option value="" disabled>Select a mode...</option>
                                        {(selectedModel.modes || [])
                                            .filter((mode: UntypedApiValue) => mode.id !== 'dna_complex') // Deprecated: use Boltz-2 Complex Prediction instead
                                            .map((mode: UntypedApiValue) => (
                                                <option key={mode.id} value={mode.id}>
                                                    {mode.name}
                                                </option>
                                            ))}
                                    </select>
                                    {selectedMode && (
                                        <p className="mt-2 text-sm text-slate-500">{compactUiCopy(selectedMode.description, 120)}</p>
                                    )}
                                </div>

                                {/* Dynamic Parameters - Grouped */}
                                {selectedMode && Object.keys(groupedParams).length > 0 && (
                                    <div className="space-y-6 pt-6 border-t border-slate-700/50">
                                        {/* Render groups in preferred order */}
                                        {['Inputs', 'Docking Settings', 'General'].filter(g => groupedParams[g]).map(groupName => (
                                            <div key={groupName}>
                                                {groupName !== 'General' && (
                                                    <h3 className="text-sm font-semibold text-slate-300 mb-4 flex items-center gap-2">
                                                        <span className={`w-1 h-4 rounded-full ${groupName === 'Inputs' ? 'bg-emerald-500' : 'bg-blue-500'}`} />
                                                        {groupName}
                                                    </h3>
                                                )}
                                                <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                                    {groupedParams[groupName].map((param: UntypedApiValue) => (
                                                        <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} setSequenceToSave={setSequenceToSave} ligandPresets={ligandPresets} />
                                                    ))}
                                                </div>
                                            </div>
                                        ))}

                                        {/* Advanced section - collapsible */}
                                        {groupedParams['Advanced'] && (
                                            <div className="border border-slate-700/50 rounded-lg overflow-hidden">
                                                <button
                                                    type="button"
                                                    onClick={() => setShowAdvanced(!showAdvanced)}
                                                    className="w-full flex items-center justify-between px-4 py-3 bg-slate-800/50 hover:bg-slate-800/70 transition-colors"
                                                >
                                                    <span className="text-sm font-medium text-slate-400 flex items-center gap-2">
                                                        <span className="w-1 h-4 rounded-full bg-slate-500" />
                                                        Advanced Settings
                                                    </span>
                                                    <span className="text-slate-500 text-xs">{showAdvanced ? '▲' : '▼'}</span>
                                                </button>
                                                {showAdvanced && (
                                                    <div className="p-4 grid grid-cols-1 md:grid-cols-2 gap-4">
                                                        {groupedParams['Advanced'].map((param: UntypedApiValue) => (
                                                            <ParamField key={param.name} param={param} params={params} updateParam={updateParam} setShowFileBrowser={setShowFileBrowser} setActiveSequenceField={setActiveSequenceField} setShowSequenceManager={setShowSequenceManager} setSequenceToSave={setSequenceToSave} ligandPresets={ligandPresets} />
                                                        ))}
                                                    </div>
                                                )}
                                            </div>
                                        )}
                                    </div>
                                )
                                }

                                {genericFrustrampnnControl}

                                {/* Ligand Selector for Complex Prediction mode in manual/advanced mode */}
                                {selectedModeId === 'complex' && (
                                    <div className="pt-6 border-t border-slate-700/50">
                                        <LigandSelector
                                            ligands={ligands}
                                            setLigands={setLigands}
                                            showCustomSmiles={true}
                                        />
                                    </div>
                                )}
                            </div>
                        </div>
                    </section>
                )}

                {/* Submit Button - Hide if Mutagenesis, Antibody De Novo, or Structure Prediction Template is active (they have their own) */}
                {!isDedicatedLauncherTemplate(selectedTemplateId) && (
                    <div className="flex justify-end gap-3 pt-4 pb-12">
                        {/* Save as Template Button */}
                        {(isTemplateMode || (wizardMode === 'manual' && selectedModelId)) && (
                            <button
                                onClick={() => openTemplateManager({
                                    currentParams: templateManagerParams,
                                    currentModelId: selectedModelId || templateDetail?.preset_params?.template_model_id || undefined,
                                    currentMode: selectedModeId || templateDetail?.preset_params?.template_mode_id || undefined,
                                    baseTemplateId: selectedTemplateId || undefined,
                                })}
                                className="inline-flex min-w-[12rem] items-center justify-center rounded-xl border border-slate-600 bg-slate-900/60 px-6 py-3.5 text-sm font-semibold text-slate-100 transition-all hover:bg-slate-800"
                            >
                                Template Manager
                            </button>
                        )}
                        <button
                            onClick={handleSubmit}
                            disabled={!isReady || submitMutation.isPending}
                            title={!isReady ? launchBlockedReason : undefined}
                            className={`inline-flex min-w-[12rem] items-center justify-center rounded-xl border px-6 py-3.5 text-sm font-semibold transition-all ${isReady
                                ? 'border-blue-500/40 bg-blue-500/15 text-blue-200 hover:bg-blue-500/20'
                                : 'border-slate-700 bg-slate-900/60 text-slate-500 cursor-not-allowed'
                                }`}
                        >
                            {submitMutation.isPending ? 'Launching Job...' : 'Launch Experiment'}
                        </button>
                        {!isReady && launchBlockedReason && selectedTemplateId && (
                            <div className="self-center text-xs text-slate-500">{launchBlockedReason}</div>
                        )}
                    </div>
                )}
            </main>

            {/* Loading Overlay for Batch Submission */}
            {submitMutation.isPending && selectedTemplateId === 'mutagenesis' && (
                <div className="fixed inset-0 bg-black/80 backdrop-blur-sm z-[100] flex items-center justify-center">
                    <div className="bg-slate-900 border border-slate-700 p-8 rounded-2xl shadow-2xl flex flex-col items-center">
                        <div className="w-16 h-16 border-4 border-accent/30 border-t-accent rounded-full animate-spin mb-4" />
                        <h3 className="text-xl font-bold text-white mb-2">Submitting Batch Jobs...</h3>
                        <p className="text-slate-400">Please wait while we launch your variant library.</p>
                    </div>
                </div>
            )}

            {/* File Browser Modal */}
            {showFileBrowser && (
                <FileBrowser
                    onSelect={(path) => {
                        updateParam(showFileBrowser, path);
                        setShowFileBrowser(null);
                    }}
                    onCancel={() => setShowFileBrowser(null)}
                />
            )}

            {/* Sequence Manager Modal */}
            <SequenceManagerModal
                isOpen={showSequenceManager}
                onClose={() => {
                    setShowSequenceManager(false);
                    setSequenceToSave(null);
                }}
                onSelect={(seq) => {
                    // Load selected sequence into the current sequence param
                    updateParam(activeSequenceField, seq.sequence);
                    if (seq.name && activeSequenceField === 'sequence') updateParam('sequence_name', seq.name);
                }}
                initialSequence={sequenceToSave?.sequence || ''}
                initialName={sequenceToSave?.name || ''}
            />

            {/* Template Manager Modal */}
            <TemplateManagerModal
                isOpen={showTemplateManager}
                onClose={() => {
                    setShowTemplateManager(false);
                    setTemplateManagerContext({});
                }}
                onSelect={routeUserTemplate}
                currentParams={templateManagerContext.currentParams}
                currentModelId={templateManagerContext.currentModelId}
                currentMode={templateManagerContext.currentMode}
                baseTemplateId={templateManagerContext.baseTemplateId}
            />
        </div>
    );
}
