/**
 * SequenceManager - Component for creating/editing/managing user sequences
 * 
 * Features:
 * - List view of saved sequences with search
 * - Create new sequence with name, sequence, description, organism, UniProt ID
 * - Edit existing sequences
 * - Delete sequences with confirmation
 * - Sequence validation (amino acid characters only)
 */

import { useState, useEffect } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import {
    fetchUserSequences,
    createUserSequence,
    updateUserSequence,
    deleteUserSequence,
} from '../lib/api';
import type { UserSequence, UserSequenceCreate } from '../lib/api';

export interface SequenceManagerProps {
    onSelect?: (sequence: UserSequence) => void;
    onClose?: () => void;
    initialSequence?: string;  // Pre-fill when saving from textarea
    initialName?: string;
}

export function SequenceManager({
    onSelect,
    onClose,
    initialSequence = '',
    initialName = ''
}: SequenceManagerProps) {
    const queryClient = useQueryClient();

    // View mode: 'list' or 'edit'
    const [mode, setMode] = useState<'list' | 'edit'>('list');
    const [editingSequence, setEditingSequence] = useState<UserSequence | null>(null);
    const [searchQuery, setSearchQuery] = useState('');
    const [formError, setFormError] = useState('');

    // Form state
    const [formName, setFormName] = useState('');
    const [formSequence, setFormSequence] = useState('');
    const [formDescription, setFormDescription] = useState('');
    const [formOrganism, setFormOrganism] = useState('');
    const [formUniprot, setFormUniprot] = useState('');
    const [formNcbi, setFormNcbi] = useState('');

    // Fetch sequences
    const { data: sequences = [], isLoading } = useQuery({
        queryKey: ['user-sequences', searchQuery],
        queryFn: () => fetchUserSequences(searchQuery || undefined),
        select: (res) => res.data,
    });

    // Create mutation
    const createMutation = useMutation({
        mutationFn: createUserSequence,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['user-sequences'] });
            resetForm();
            setMode('list');
        },
        onError: (err: any) => {
            setFormError(err.response?.data?.detail || 'Failed to create sequence');
        }
    });

    // Update mutation
    const updateMutation = useMutation({
        mutationFn: ({ id, data }: { id: string; data: Partial<UserSequenceCreate> }) =>
            updateUserSequence(id, data),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['user-sequences'] });
            resetForm();
            setMode('list');
        },
        onError: (err: any) => {
            setFormError(err.response?.data?.detail || 'Failed to update sequence');
        }
    });

    // Delete mutation
    const deleteMutation = useMutation({
        mutationFn: deleteUserSequence,
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['user-sequences'] });
        }
    });

    // Import presets mutation
    const importPresetsMutation = useMutation({
        mutationFn: () => fetch('/api/user-sequences/import-presets', { method: 'POST' }).then(r => r.json()),
        onSuccess: (data) => {
            queryClient.invalidateQueries({ queryKey: ['user-sequences'] });
            alert(`Imported ${data.imported} sequences from presets (${data.skipped} already existed)`);
        },
        onError: (err: any) => {
            alert('Failed to import presets: ' + (err.message || 'Unknown error'));
        }
    });

    // Reset form
    const resetForm = () => {
        setFormName('');
        setFormSequence('');
        setFormDescription('');
        setFormOrganism('');
        setFormUniprot('');
        setFormNcbi('');
        setFormError('');
        setEditingSequence(null);
    };

    // Initialize form when opening in create mode with initial data
    useEffect(() => {
        if (initialSequence && mode === 'list') {
            setFormSequence(initialSequence.toUpperCase().replace(/[^A-Z]/g, ''));
            setFormName(initialName);
            setMode('edit');
        }
    }, [initialSequence, initialName]);

    // Populate form when editing
    useEffect(() => {
        if (editingSequence) {
            setFormName(editingSequence.name);
            setFormSequence(editingSequence.sequence);
            setFormDescription(editingSequence.description || '');
            setFormOrganism(editingSequence.organism || '');
            setFormUniprot(editingSequence.uniprot_id || '');
            setFormNcbi(editingSequence.ncbi_id || '');
        }
    }, [editingSequence]);

    // Handle form submission
    const handleSubmit = () => {
        setFormError('');

        // Validate
        if (!formName.trim()) {
            setFormError('Name is required');
            return;
        }
        if (!formSequence.trim()) {
            setFormError('Sequence is required');
            return;
        }

        const cleanSequence = formSequence.toUpperCase().replace(/[^A-Z]/g, '');
        const validAA = new Set('ACDEFGHIKLMNPQRSTVWY');
        const invalidChars = [...new Set(cleanSequence)].filter(c => !validAA.has(c));

        if (invalidChars.length > 0) {
            setFormError(`Invalid amino acid characters: ${invalidChars.join(', ')}`);
            return;
        }

        const data: UserSequenceCreate = {
            name: formName.trim(),
            sequence: cleanSequence,
            description: formDescription.trim() || undefined,
            organism: formOrganism.trim() || undefined,
            uniprot_id: formUniprot.trim() || undefined,
            ncbi_id: formNcbi.trim() || undefined,
        };

        if (editingSequence) {
            updateMutation.mutate({ id: editingSequence.id, data });
        } else {
            createMutation.mutate(data);
        }
    };

    // Handle delete
    const handleDelete = (seq: UserSequence) => {
        if (confirm(`Delete sequence "${seq.name}"? This cannot be undone.`)) {
            deleteMutation.mutate(seq.id);
        }
    };

    // Handle close wrapper
    const handleClose = () => {
        resetForm();
        setMode('list');
        if (onClose) onClose();
    };

    return (
        <div className="flex-1 flex flex-col h-full min-h-[400px]">
            {/* Header - Only showing title here might be redundant if parent has tabs. 
                 But let's include a small header for List Mode actions if needed. 
                 Actually, the 'New Sequence' button is in the list view.
                 Let's stick to just content. */}

            <div className="flex-1 overflow-y-auto p-1">
                {mode === 'list' ? (
                    /* List View */
                    <div className="space-y-4">
                        {/* Search and Add */}
                        <div className="flex gap-3">
                            <input
                                type="text"
                                value={searchQuery}
                                onChange={(e) => setSearchQuery(e.target.value)}
                                placeholder="Search sequences..."
                                className="flex-1 bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm focus:ring-2 focus:ring-emerald-500 outline-none"
                            />
                            <button
                                onClick={() => { resetForm(); setMode('edit'); }}
                                className="px-4 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium transition-colors flex items-center gap-2"
                            >
                                <span>+</span> New Sequence
                            </button>
                            {sequences.length === 0 && (
                                <button
                                    onClick={() => importPresetsMutation.mutate()}
                                    disabled={importPresetsMutation.isPending}
                                    className="px-4 py-2.5 bg-blue-600/20 hover:bg-blue-600/30 text-blue-400 rounded-lg font-medium transition-colors flex items-center gap-2 border border-blue-600/30"
                                >
                                    {importPresetsMutation.isPending ? '...' : '📥'} Import Presets
                                </button>
                            )}
                        </div>

                        {/* Sequence List */}
                        {isLoading ? (
                            <div className="flex items-center justify-center py-12">
                                <div className="animate-spin rounded-full h-8 w-8 border-b-2 border-emerald-500" />
                            </div>
                        ) : sequences.length === 0 ? (
                            <div className="text-center py-12 text-slate-500">
                                <div className="text-4xl mb-3">📭</div>
                                <p>No saved sequences yet</p>
                                <p className="text-sm mt-1">Click "New Sequence" to add one</p>
                            </div>
                        ) : (
                            <div className="space-y-2">
                                {sequences.map((seq) => (
                                    <div
                                        key={seq.id}
                                        className="bg-slate-800/50 border border-slate-700 rounded-lg p-4 hover:border-slate-600 transition-colors group"
                                    >
                                        <div className="flex items-start justify-between gap-4">
                                            <div className="flex-1 min-w-0">
                                                <div className="flex items-center gap-2 mb-1 flex-wrap">
                                                    <h4 className="font-medium text-slate-200 truncate">{seq.name}</h4>
                                                    <span className="text-xs bg-slate-700 px-2 py-0.5 rounded text-slate-400">
                                                        {seq.length} aa
                                                    </span>
                                                    {seq.is_preset && (
                                                        <span className="text-xs bg-accent/20 text-accent px-2 py-0.5 rounded">
                                                            Preset
                                                        </span>
                                                    )}
                                                    {seq.organism && (
                                                        <span className="text-xs bg-blue-500/20 text-blue-400 px-2 py-0.5 rounded">
                                                            {seq.organism}
                                                        </span>
                                                    )}
                                                </div>
                                                {/* Database Links Row */}
                                                <div className="flex items-center gap-2 mb-1.5">
                                                    {seq.uniprot_id && (
                                                        <a
                                                            href={`https://www.uniprot.org/uniprotkb/${seq.uniprot_id}`}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-0.5 bg-slate-800 px-2 py-0.5 rounded"
                                                        >
                                                            UniProt ↗
                                                        </a>
                                                    )}
                                                    {seq.ncbi_id && (
                                                        <a
                                                            href={seq.ncbi_id.match(/^[0-9]+$/)
                                                                ? `https://www.ncbi.nlm.nih.gov/gene/${seq.ncbi_id}`
                                                                : `https://www.ncbi.nlm.nih.gov/protein/${seq.ncbi_id}`}
                                                            target="_blank"
                                                            rel="noopener noreferrer"
                                                            className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-0.5 bg-slate-800 px-2 py-0.5 rounded"
                                                        >
                                                            NCBI ↗
                                                        </a>
                                                    )}
                                                </div>
                                                {seq.description && (
                                                    <p className="text-xs text-slate-500 mb-2 line-clamp-1">{seq.description}</p>
                                                )}
                                                <p className="text-xs font-mono text-slate-600 truncate">
                                                    {seq.sequence.slice(0, 60)}...
                                                </p>
                                            </div>
                                            <div className="flex items-center gap-2 opacity-0 group-hover:opacity-100 transition-opacity">
                                                {/* Copy Button */}
                                                <button
                                                    onClick={() => {
                                                        navigator.clipboard.writeText(seq.sequence);
                                                        alert('Sequence copied to clipboard!');
                                                    }}
                                                    className="px-3 py-1.5 bg-slate-700/50 hover:bg-slate-600 text-slate-300 text-sm rounded-lg transition-colors"
                                                    title="Copy sequence to clipboard"
                                                >
                                                    📋
                                                </button>
                                                {onSelect && (
                                                    <button
                                                        onClick={() => { onSelect(seq); handleClose(); }}
                                                        className="px-3 py-1.5 bg-emerald-600 hover:bg-emerald-500 text-white text-sm rounded-lg transition-colors"
                                                    >
                                                        Use
                                                    </button>
                                                )}
                                                <button
                                                    onClick={() => { setEditingSequence(seq); setMode('edit'); }}
                                                    className="px-3 py-1.5 bg-slate-700 hover:bg-slate-600 text-slate-200 text-sm rounded-lg transition-colors"
                                                >
                                                    Edit
                                                </button>
                                                <button
                                                    onClick={() => handleDelete(seq)}
                                                    className="px-3 py-1.5 bg-red-500/20 hover:bg-red-500/30 text-red-400 text-sm rounded-lg transition-colors"
                                                >
                                                    Delete
                                                </button>
                                            </div>
                                        </div>
                                    </div>
                                ))}
                            </div>
                        )}
                    </div>
                ) : (
                    /* Edit/Create View */
                    <div className="space-y-5">
                        {/* Back button */}
                        <button
                            onClick={() => { resetForm(); setMode('list'); }}
                            className="text-slate-400 hover:text-white text-sm flex items-center gap-1 transition-colors"
                        >
                            ← Back to list
                        </button>

                        {/* Form */}
                        <div className="grid grid-cols-1 md:grid-cols-2 gap-5">
                            {/* Name */}
                            <div className="col-span-full md:col-span-1">
                                <label className="block text-sm font-medium text-slate-400 mb-1">
                                    Name <span className="text-red-400">*</span>
                                </label>
                                <input
                                    type="text"
                                    value={formName}
                                    onChange={(e) => setFormName(e.target.value)}
                                    placeholder="e.g., Human TdT"
                                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm focus:ring-2 focus:ring-emerald-500 outline-none"
                                />
                            </div>

                            {/* Organism */}
                            <div>
                                <label className="block text-sm font-medium text-slate-400 mb-1">
                                    Organism
                                </label>
                                <input
                                    type="text"
                                    value={formOrganism}
                                    onChange={(e) => setFormOrganism(e.target.value)}
                                    placeholder="e.g., Homo sapiens"
                                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm focus:ring-2 focus:ring-emerald-500 outline-none"
                                />
                            </div>

                            {/* Database Identifiers */}
                            <div className="col-span-full">
                                <label className="block text-sm font-medium text-slate-400 mb-2">
                                    Database Identifiers
                                </label>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    {/* UniProt ID */}
                                    <div className="flex gap-2">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-1 mb-1">
                                                <span className="text-xs text-slate-500">UniProt</span>
                                                {formUniprot && (
                                                    <a
                                                        href={`https://www.uniprot.org/uniprotkb/${formUniprot}`}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-0.5"
                                                    >
                                                        ↗ Open
                                                    </a>
                                                )}
                                            </div>
                                            <input
                                                type="text"
                                                value={formUniprot}
                                                onChange={(e) => setFormUniprot(e.target.value.toUpperCase())}
                                                placeholder="e.g., P04053"
                                                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-emerald-500 outline-none font-mono"
                                            />
                                        </div>
                                    </div>
                                    {/* NCBI ID */}
                                    <div className="flex gap-2">
                                        <div className="flex-1">
                                            <div className="flex items-center gap-1 mb-1">
                                                <span className="text-xs text-slate-500">NCBI Gene/Protein</span>
                                                {formNcbi && (
                                                    <a
                                                        href={formNcbi.match(/^[0-9]+$/)
                                                            ? `https://www.ncbi.nlm.nih.gov/gene/${formNcbi}`
                                                            : `https://www.ncbi.nlm.nih.gov/protein/${formNcbi}`}
                                                        target="_blank"
                                                        rel="noopener noreferrer"
                                                        className="text-xs text-blue-400 hover:text-blue-300 flex items-center gap-0.5"
                                                    >
                                                        ↗ Open
                                                    </a>
                                                )}
                                            </div>
                                            <input
                                                type="text"
                                                value={formNcbi}
                                                onChange={(e) => setFormNcbi(e.target.value)}
                                                placeholder="e.g., 1791 or NP_004079"
                                                className="w-full bg-slate-800 border border-slate-700 rounded-lg px-3 py-2 text-white text-sm focus:ring-2 focus:ring-emerald-500 outline-none font-mono"
                                            />
                                        </div>
                                    </div>
                                </div>
                            </div>

                            {/* Description */}
                            <div className="col-span-full">
                                <label className="block text-sm font-medium text-slate-400 mb-1">
                                    Description
                                </label>
                                <input
                                    type="text"
                                    value={formDescription}
                                    onChange={(e) => setFormDescription(e.target.value)}
                                    placeholder="Brief description of this sequence..."
                                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-2.5 text-white text-sm focus:ring-2 focus:ring-emerald-500 outline-none"
                                />
                            </div>

                            {/* Sequence */}
                            <div className="col-span-full">
                                <div className="flex justify-between items-center mb-1">
                                    <label className="text-sm font-medium text-slate-400">
                                        Amino Acid Sequence <span className="text-red-400">*</span>
                                    </label>
                                    <span className="text-xs text-slate-500">
                                        {formSequence.replace(/[^A-Za-z]/g, '').length} aa
                                    </span>
                                </div>
                                <textarea
                                    value={formSequence}
                                    onChange={(e) => setFormSequence(e.target.value.toUpperCase())}
                                    placeholder="Paste or enter amino acid sequence (A-Z only)..."
                                    rows={8}
                                    className="w-full bg-slate-800 border border-slate-700 rounded-lg px-4 py-3 text-white text-sm font-mono focus:ring-2 focus:ring-emerald-500 outline-none resize-y"
                                />
                                <p className="text-xs text-slate-500 mt-1">
                                    Single-letter amino acid codes only. Whitespace and numbers will be stripped.
                                </p>
                            </div>
                        </div>

                        {/* Error */}
                        {formError && (
                            <div className="bg-red-500/20 border border-red-500/30 rounded-lg px-4 py-3 text-red-400 text-sm">
                                {formError}
                            </div>
                        )}

                        {/* Actions */}
                        <div className="flex justify-end gap-3 pt-3 border-t border-slate-700">
                            <button
                                onClick={() => { resetForm(); setMode('list'); }}
                                className="px-5 py-2.5 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg font-medium transition-colors"
                            >
                                Cancel
                            </button>
                            <button
                                onClick={handleSubmit}
                                disabled={createMutation.isPending || updateMutation.isPending}
                                className="px-5 py-2.5 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg font-medium transition-colors disabled:opacity-50"
                            >
                                {createMutation.isPending || updateMutation.isPending
                                    ? 'Saving...'
                                    : editingSequence
                                        ? 'Update Sequence'
                                        : 'Save Sequence'
                                }
                            </button>
                        </div>
                    </div>
                )}
            </div>
        </div>
    );
}
