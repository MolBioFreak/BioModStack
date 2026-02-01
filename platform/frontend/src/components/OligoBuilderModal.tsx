import { useState, useEffect, useRef } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchUserTemplates, createUserTemplate, deleteUserTemplate } from '../lib/api';
import type { LigandEntry } from './LigandSelector';

interface OligoBuilderModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSubmit: (entries: LigandEntry[]) => void;
    ligandCount: number;
}

type NucleicAcidType = 'dna' | 'rna';
type Base = 'A' | 'T' | 'C' | 'G' | 'U' | null;

const DNA_BASES: Base[] = ['A', 'T', 'C', 'G'];
const RNA_BASES: Base[] = ['A', 'U', 'C', 'G'];

const WC_PAIRS: Record<string, Record<string, Base>> = {
    dna: { 'A': 'T', 'T': 'A', 'C': 'G', 'G': 'C' },
    rna: { 'A': 'U', 'U': 'A', 'C': 'G', 'G': 'C' }
};

const BASE_COLORS: Record<string, string> = {
    'A': 'bg-green-500',
    'T': 'bg-red-500',
    'U': 'bg-red-500',
    'C': 'bg-blue-500',
    'G': 'bg-yellow-500',
};

export function OligoBuilderModal({ isOpen, onClose, onSubmit, ligandCount }: OligoBuilderModalProps) {
    const queryClient = useQueryClient();
    const [naType, setNaType] = useState<NucleicAcidType>('dna');
    const [length, setLength] = useState(10);
    const [templateStrand, setTemplateStrand] = useState<Base[]>([]);
    const [primerStrand, setPrimerStrand] = useState<Base[]>([]);
    const [enforceWC, setEnforceWC] = useState(true);
    const [singleStrandMode, setSingleStrandMode] = useState(false);
    const [sequenceText, setSequenceText] = useState('');
    const [showTemplates, setShowTemplates] = useState(false);
    const skipLengthResetRef = useRef(false);  // Skip reset when pasting

    // Fetch saved oligo templates
    const { data: savedTemplates = [] } = useQuery({
        queryKey: ['oligo-templates'],
        queryFn: () => fetchUserTemplates(undefined, 'oligo_builder'),
        enabled: isOpen,
        select: (res) => res.data,
    });

    // Save template mutation
    const saveMutation = useMutation({
        mutationFn: (name: string) => createUserTemplate({
            name,
            model_id: 'oligo_builder',
            icon: 'beaker',
            color: '#10B981',
            params: { naType, length, templateStrand, primerStrand, enforceWC, singleStrandMode }
        }),
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['oligo-templates'] }),
    });

    // Delete template mutation
    const deleteMutation = useMutation({
        mutationFn: deleteUserTemplate,
        onSuccess: () => queryClient.invalidateQueries({ queryKey: ['oligo-templates'] }),
    });

    // Handle sequence text input with DNA/RNA auto-detect
    const handleSequenceTextChange = (text: string) => {
        const cleaned = text.toUpperCase().replace(/[^ATUCG_\-\s]/g, '');
        setSequenceText(cleaned);

        // Parse bases (ignore whitespace and dashes)
        const basesOnly = cleaned.replace(/[\s\-_]/g, '').split('') as Base[];
        if (basesOnly.length === 0) return;

        // Auto-detect DNA vs RNA
        if (cleaned.includes('U') && !cleaned.includes('T')) {
            setNaType('rna');
        } else if (cleaned.includes('T') && !cleaned.includes('U')) {
            setNaType('dna');
        }

        // Update strands - flag to skip useEffect reset
        const newLength = Math.max(2, Math.min(50, basesOnly.length));
        skipLengthResetRef.current = true;  // Prevent useEffect from resetting
        setLength(newLength);
        setTemplateStrand(basesOnly.slice(0, newLength));
    };

    // Load template
    const loadTemplate = (params: any) => {
        // Skip the useEffect that resets strands on length change
        skipLengthResetRef.current = true;
        setNaType(params.naType || 'dna');
        setLength(params.length || 10);
        setTemplateStrand(params.templateStrand || []);
        setPrimerStrand(params.primerStrand || []);
        setEnforceWC(params.enforceWC ?? true);
        setSingleStrandMode(params.singleStrandMode ?? false);
        setShowTemplates(false);
    };

    // Save current config as template
    const handleSaveTemplate = () => {
        const name = prompt('Template name:');
        if (name?.trim()) {
            saveMutation.mutate(name.trim());
        }
    };

    // Initialize strands when length changes (skip if set from text input)
    useEffect(() => {
        if (skipLengthResetRef.current) {
            skipLengthResetRef.current = false;
            return;
        }
        const newTemplate = Array(length).fill('A') as Base[];
        setTemplateStrand(newTemplate);
        if (enforceWC) {
            setPrimerStrand(newTemplate.map(b => b ? WC_PAIRS[naType][b] : null));
        } else {
            setPrimerStrand(Array(length).fill(null) as Base[]);
        }
    }, [length, naType]);

    // Update primer when template changes (if WC enforced)
    useEffect(() => {
        if (enforceWC && !singleStrandMode) {
            setPrimerStrand(templateStrand.map(b => b ? WC_PAIRS[naType][b] : null));
        }
    }, [templateStrand, enforceWC, naType, singleStrandMode]);

    const bases = naType === 'dna' ? DNA_BASES : RNA_BASES;

    const cycleBase = (strand: 'template' | 'primer', index: number) => {
        const setStrand = strand === 'template' ? setTemplateStrand : setPrimerStrand;
        const currentStrand = strand === 'template' ? templateStrand : primerStrand;
        const currentBase = currentStrand[index];

        // Cycle through bases + null (gap)
        const allOptions = [...bases, null];
        const currentIdx = allOptions.indexOf(currentBase);
        const nextIdx = (currentIdx + 1) % allOptions.length;
        const newBase = allOptions[nextIdx];

        setStrand(prev => {
            const newStrand = [...prev];
            newStrand[index] = newBase;
            return newStrand;
        });
    };

    const handleSubmit = () => {
        const entries: LigandEntry[] = [];
        const baseId = ligandCount;

        // Template strand (if has any bases)
        const templateSeq = templateStrand.filter(b => b !== null).join('');
        if (templateSeq.length > 0) {
            entries.push({
                id: String.fromCharCode(66 + baseId),
                type: naType,
                sequence: templateSeq,
                name: `${naType.toUpperCase()} Template 5'→3' (${templateSeq.length}nt)`
            });
        }

        // Primer strand (if not single strand mode and has bases)
        // IMPORTANT: Reverse primer for Boltz - it expects 5'→3' for both strands
        // but they need to be antiparallel for duplex formation
        if (!singleStrandMode) {
            const primerSeq = primerStrand.filter(b => b !== null).join('');
            if (primerSeq.length > 0) {
                // Reverse the primer so Boltz gets antiparallel strands
                const primerSeqReversed = primerSeq.split('').reverse().join('');
                entries.push({
                    id: String.fromCharCode(66 + baseId + (templateSeq.length > 0 ? 1 : 0)),
                    type: naType,
                    sequence: primerSeqReversed,
                    name: `${naType.toUpperCase()} Primer (${primerSeq.length}nt, reversed for duplex)`
                });
            }
        }

        if (entries.length > 0) {
            onSubmit(entries);
        }
    };

    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/70 backdrop-blur-sm z-50 flex items-center justify-center p-4">
            <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-4xl max-h-[90vh] overflow-hidden">
                {/* Header */}
                <div className="px-6 py-4 border-b border-slate-700 flex items-center justify-between">
                    <h2 className="text-lg font-semibold text-white">Advanced Oligo Builder</h2>
                    <button onClick={onClose} className="text-slate-400 hover:text-white">×</button>
                </div>

                {/* Controls */}
                <div className="px-6 py-4 border-b border-slate-700 flex flex-wrap gap-4 items-center">
                    {/* NA Type Toggle */}
                    <div className="flex items-center gap-2">
                        <span className="text-sm text-slate-400">Type:</span>
                        <div className="flex bg-slate-800 rounded-lg p-0.5">
                            <button
                                onClick={() => setNaType('dna')}
                                className={`px-3 py-1 text-sm rounded ${naType === 'dna' ? 'bg-blue-500 text-white' : 'text-slate-400'}`}
                            >DNA</button>
                            <button
                                onClick={() => setNaType('rna')}
                                className={`px-3 py-1 text-sm rounded ${naType === 'rna' ? 'bg-purple-500 text-white' : 'text-slate-400'}`}
                            >RNA</button>
                        </div>
                    </div>

                    {/* Length */}
                    <div className="flex items-center gap-2">
                        <span className="text-sm text-slate-400">Length:</span>
                        <input
                            type="number"
                            min={2}
                            max={50}
                            value={length}
                            onChange={(e) => setLength(Math.min(50, Math.max(2, parseInt(e.target.value) || 2)))}
                            className="w-16 bg-slate-800 border border-slate-700 rounded px-2 py-1 text-white text-sm"
                        />
                    </div>

                    {/* Single Strand Mode */}
                    <label className="flex items-center gap-2 cursor-pointer">
                        <input
                            type="checkbox"
                            checked={singleStrandMode}
                            onChange={(e) => setSingleStrandMode(e.target.checked)}
                            className="w-4 h-4 rounded bg-slate-800 border-slate-700"
                        />
                        <span className="text-sm text-slate-300">Single Strand Only</span>
                    </label>

                    {/* Watson-Crick Toggle */}
                    {!singleStrandMode && (
                        <label className="flex items-center gap-2 cursor-pointer">
                            <input
                                type="checkbox"
                                checked={enforceWC}
                                onChange={(e) => setEnforceWC(e.target.checked)}
                                className="w-4 h-4 rounded bg-slate-800 border-slate-700"
                            />
                            <span className="text-sm text-slate-300">Enforce Watson-Crick Pairing</span>
                        </label>
                    )}

                    {/* Template Controls */}
                    <div className="flex gap-2 ml-auto">
                        <button
                            onClick={handleSaveTemplate}
                            disabled={saveMutation.isPending}
                            className="px-3 py-1.5 text-sm bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg flex items-center gap-1.5 transition-colors disabled:opacity-50"
                        >
                            Save
                        </button>
                        <button
                            onClick={() => setShowTemplates(!showTemplates)}
                            className={`px-3 py-1.5 text-sm rounded-lg flex items-center gap-1.5 transition-colors ${showTemplates ? 'bg-blue-500 text-white' : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                                }`}
                        >
                            Load {savedTemplates.length > 0 ? `(${savedTemplates.length})` : ''}
                        </button>
                    </div>
                </div>

                {/* Template List (collapsible) */}
                {showTemplates && (
                    <div className="px-6 pb-4 border-b border-slate-700">
                        {savedTemplates.length > 0 ? (
                            <div className="flex flex-wrap gap-2">
                                {savedTemplates.map((t: any) => (
                                    <div key={t.id} className="flex items-center gap-1 bg-slate-800 rounded-lg pl-3 pr-1 py-1">
                                        <button
                                            onClick={() => loadTemplate(t.params)}
                                            className="text-sm text-slate-300 hover:text-white"
                                        >
                                            {t.name}
                                        </button>
                                        <button
                                            onClick={() => deleteMutation.mutate(t.id)}
                                            className="p-1 text-slate-500 hover:text-red-400 text-xs"
                                        >
                                            ×
                                        </button>
                                    </div>
                                ))}
                            </div>
                        ) : (
                            <div className="text-sm text-slate-400 py-2">
                                No saved templates yet. Click "Save" to save the current configuration.
                            </div>
                        )}
                    </div>
                )}

                {/* Strand Editor */}
                <div className="px-6 py-6 overflow-x-auto">

                    <div className="space-y-2 min-w-max">
                        {/* Template Strand (5'→3') */}
                        <div className="flex items-center gap-1">
                            <span className="text-xs text-slate-500 w-16">5'</span>
                            {templateStrand.map((base, idx) => (
                                <button
                                    key={`template-${idx}`}
                                    onClick={() => cycleBase('template', idx)}
                                    className={`w-8 h-8 rounded text-xs font-bold text-white transition-all hover:scale-110 ${base ? BASE_COLORS[base] : 'bg-slate-700 border-2 border-dashed border-slate-500'
                                        }`}
                                    title={`Position ${idx + 1}: Click to cycle bases`}
                                >
                                    {base || '—'}
                                </button>
                            ))}
                            <span className="text-xs text-slate-500 w-16 text-right">3'</span>
                            <span className="text-xs text-slate-400 ml-4">Template</span>
                        </div>

                        {/* Base pair connections */}
                        {!singleStrandMode && (
                            <div className="flex items-center gap-1">
                                <span className="w-16" />
                                {templateStrand.map((tBase, idx) => {
                                    const pBase = primerStrand[idx];
                                    const isValidPair = tBase && pBase && WC_PAIRS[naType][tBase] === pBase;
                                    const isMismatch = tBase && pBase && WC_PAIRS[naType][tBase] !== pBase;
                                    return (
                                        <div
                                            key={`conn-${idx}`}
                                            className={`w-8 h-4 flex items-center justify-center ${(!tBase || !pBase) ? 'text-slate-600' :
                                                isMismatch ? 'text-red-400' : 'text-slate-500'
                                                }`}
                                        >
                                            {tBase && pBase ? (isValidPair ? '│' : '╳') : '·'}
                                        </div>
                                    );
                                })}
                            </div>
                        )}

                        {/* Primer Strand (3'→5') */}
                        {!singleStrandMode && (
                            <div className="flex items-center gap-1">
                                <span className="text-xs text-slate-500 w-16">3'</span>
                                {primerStrand.map((base, idx) => (
                                    <button
                                        key={`primer-${idx}`}
                                        onClick={() => !enforceWC && cycleBase('primer', idx)}
                                        disabled={enforceWC}
                                        className={`w-8 h-8 rounded text-xs font-bold text-white transition-all ${enforceWC ? 'opacity-70 cursor-not-allowed' : 'hover:scale-110'
                                            } ${base ? BASE_COLORS[base] : 'bg-slate-700 border-2 border-dashed border-slate-500'
                                            }`}
                                        title={enforceWC ? 'Disable WC pairing to edit' : `Position ${idx + 1}: Click to cycle bases`}
                                    >
                                        {base || '—'}
                                    </button>
                                ))}
                                <span className="text-xs text-slate-500 w-16 text-right">5'</span>
                                <span className="text-xs text-slate-400 ml-4">Primer</span>
                            </div>
                        )}
                    </div>

                    {/* Legend */}
                    <div className="mt-6 flex flex-wrap gap-3 text-xs text-slate-400">
                        <span className="font-medium">Legend:</span>
                        {bases.map(base => (
                            <span key={base} className="flex items-center gap-1">
                                <span className={`w-4 h-4 rounded ${BASE_COLORS[base!]}`} />
                                {base}
                            </span>
                        ))}
                        <span className="flex items-center gap-1">
                            <span className="w-4 h-4 rounded bg-slate-700 border border-dashed border-slate-500" />
                            Gap
                        </span>
                    </div>

                    {/* Preview */}
                    <div className="mt-4 p-3 bg-slate-800 rounded-lg">
                        <div className="text-xs text-slate-400 mb-1">Preview:</div>
                        <div className="font-mono text-sm text-white">
                            Template: 5'-{templateStrand.map(b => b || '_').join('')}-3'
                        </div>
                        {!singleStrandMode && (
                            <div className="font-mono text-sm text-white">
                                Primer: &nbsp; 3'-{primerStrand.map(b => b || '_').join('')}-5'
                            </div>
                        )}
                    </div>

                    {/* Sequence Text Input */}
                    <div className="mt-4">
                        <label className="text-xs text-slate-400 block mb-1">
                            Paste Sequence (auto-syncs with editor, auto-detects DNA/RNA)
                        </label>
                        <textarea
                            value={sequenceText}
                            onChange={(e) => handleSequenceTextChange(e.target.value)}
                            placeholder="Paste sequence: ATCGATCG or AUCGAUCG..."
                            className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 
                                       text-white font-mono text-sm resize-y min-h-[60px]
                                       focus:ring-2 focus:ring-blue-500 outline-none
                                       placeholder:text-slate-500"
                        />
                    </div>
                </div>

                {/* Footer */}
                <div className="px-6 py-4 border-t border-slate-700 flex justify-end gap-3">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 text-slate-400 hover:text-white transition-colors"
                    >
                        Cancel
                    </button>
                    <button
                        onClick={handleSubmit}
                        className="px-4 py-2 bg-blue-500 text-white rounded-lg hover:bg-blue-600 transition-colors"
                    >
                        Add to Complex
                    </button>
                </div>
            </div>
        </div >
    );
}
