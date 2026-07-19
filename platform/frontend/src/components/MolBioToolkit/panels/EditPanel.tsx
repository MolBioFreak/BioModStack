/**
 * EditPanel - Basic sequence editing operations
 * Provides tools for inserting, deleting, replacing, and reversing sequences
 */

import { useState, useCallback, useMemo } from 'react';
import type { SequenceData, SelectionInfo } from '../types';
import {
    applyDeleteEdit,
    applyInsertEdit,
    applyReplaceEdit,
    applyTransformEdit,
} from '../utils/sequenceEdits';
import {
    sequenceUnitLabel,
} from '../utils/nucleotides';
import { createSelectionSnapshot } from '../utils/selectionActions';

interface EditPanelProps {
    sequenceData: SequenceData;
    selection: SelectionInfo | null;
    onSequenceChange: (newData: SequenceData, actionLabel?: string) => void;
}

function changeCaseInRanges(
    sequence: string,
    ranges: Array<{ start: number; end: number }>,
    mode: 'upper' | 'lower',
): string {
    return ranges.reduce((current, range) => {
        const selected = current.slice(range.start, range.end);
        const changed = mode === 'upper' ? selected.toUpperCase() : selected.toLowerCase();
        return current.slice(0, range.start) + changed + current.slice(range.end);
    }, sequence);
}

export function EditPanel({
    sequenceData,
    selection,
    onSequenceChange
}: EditPanelProps) {
    const [insertText, setInsertText] = useState('');
    const [replaceText, setReplaceText] = useState('');
    const [insertPosition, setInsertPosition] = useState<'start' | 'end' | 'cursor'>('cursor');
    const [activeTab, setActiveTab] = useState<'insert' | 'edit' | 'transform'>('edit');

    const isRNA = sequenceData.sequenceType === 'rna';
    const sequenceType = isRNA ? 'rna' : 'dna';
    const unitLabel = sequenceUnitLabel(sequenceType);

    const selectionSnapshot = useMemo(() => createSelectionSnapshot(
        selection,
        sequenceData.sequence,
        sequenceData.circular,
    ), [selection, sequenceData.circular, sequenceData.sequence]);
    const wrapsOrigin = Boolean(selectionSnapshot?.placement.wrapsOrigin);
    const contiguousSelection = selectionSnapshot && !wrapsOrigin
        ? selectionSnapshot.placement
        : null;
    const selectedSequence = selectionSnapshot?.sequence || '';

    // Validate sequence input
    const validateSequence = useCallback((seq: string): boolean => {
        const validChars = isRNA ? /^[ACGUNRYMKSWHBVDacgunrymkswhbvd\s]*$/ : /^[ACGTNRYMKSWHBVDacgtnrymkswhbvd\s]*$/;
        return validChars.test(seq);
    }, [isRNA]);

    // Clean sequence input
    const cleanSequence = useCallback((seq: string): string => {
        return seq.replace(/\s+/g, '').toUpperCase();
    }, []);

    // Insert sequence
    const handleInsert = useCallback(() => {
        if (!insertText.trim()) return;
        const cleanedInsert = cleanSequence(insertText);
        if (!validateSequence(cleanedInsert)) {
            alert('Invalid characters in sequence. Only ACGT (or ACGU for RNA) and IUPAC codes are allowed.');
            return;
        }

        let position: number;

        if (insertPosition === 'start') {
            position = 0;
        } else if (insertPosition === 'end') {
            position = sequenceData.sequence.length;
        } else {
            position = selection?.start ?? sequenceData.sequence.length;
        }

        onSequenceChange(
            applyInsertEdit(sequenceData, position, cleanedInsert),
            `Insert ${cleanedInsert.length}${unitLabel} at position ${position + 1}`,
        );
        setInsertText('');
    }, [insertText, insertPosition, selection, sequenceData, onSequenceChange, cleanSequence, validateSequence, unitLabel]);

    // Delete selection
    const handleDelete = useCallback(() => {
        if (!contiguousSelection) return;
        const { start, end } = contiguousSelection;
        onSequenceChange(
            applyDeleteEdit(sequenceData, start, end),
            `Delete ${end - start}${unitLabel} from position ${start + 1}`,
        );
    }, [contiguousSelection, sequenceData, onSequenceChange, unitLabel]);

    // Replace selection
    const handleReplace = useCallback(() => {
        if (!contiguousSelection || !replaceText.trim()) return;
        const cleanedReplace = cleanSequence(replaceText);
        if (!validateSequence(cleanedReplace)) {
            alert('Invalid characters in sequence.');
            return;
        }

        const { start, end } = contiguousSelection;
        onSequenceChange(
            applyReplaceEdit(sequenceData, start, end, cleanedReplace),
            `Replace ${end - start}${unitLabel} with ${cleanedReplace.length}${unitLabel}`,
        );
        setReplaceText('');
    }, [contiguousSelection, replaceText, sequenceData, onSequenceChange, cleanSequence, validateSequence, unitLabel]);

    // Reverse complement
    const handleReverseComplement = useCallback(() => {
        if (!contiguousSelection) return;
        const { start, end } = contiguousSelection;
        onSequenceChange(
            applyTransformEdit(sequenceData, start, end, 'reverse_complement'),
            `Reverse complement ${end - start}${unitLabel}`,
        );
    }, [contiguousSelection, sequenceData, onSequenceChange, unitLabel]);

    // Reverse only (no complement)
    const handleReverse = useCallback(() => {
        if (!contiguousSelection) return;
        const { start, end } = contiguousSelection;
        onSequenceChange(
            applyTransformEdit(sequenceData, start, end, 'reverse'),
            `Reverse ${end - start}${unitLabel}`,
        );
    }, [contiguousSelection, sequenceData, onSequenceChange, unitLabel]);

    // Complement only (no reverse)
    const handleComplement = useCallback(() => {
        if (!contiguousSelection) return;
        const { start, end } = contiguousSelection;
        onSequenceChange(
            applyTransformEdit(sequenceData, start, end, 'complement'),
            `Complement ${end - start}${unitLabel}`,
        );
    }, [contiguousSelection, sequenceData, onSequenceChange, unitLabel]);

    // Convert to uppercase
    const handleUppercase = useCallback(() => {
        if (!selectionSnapshot) {
            onSequenceChange({
                ...sequenceData,
                sequence: sequenceData.sequence.toUpperCase()
            }, 'Convert to uppercase');
        } else {
            const newSequence = changeCaseInRanges(sequenceData.sequence, selectionSnapshot.ranges, 'upper');
            onSequenceChange({
                ...sequenceData,
                sequence: newSequence
            }, `Uppercase ${selectionSnapshot.length}${unitLabel}`);
        }
    }, [selectionSnapshot, sequenceData, onSequenceChange, unitLabel]);

    // Convert to lowercase
    const handleLowercase = useCallback(() => {
        if (!selectionSnapshot) {
            onSequenceChange({
                ...sequenceData,
                sequence: sequenceData.sequence.toLowerCase()
            }, 'Convert to lowercase');
        } else {
            const newSequence = changeCaseInRanges(sequenceData.sequence, selectionSnapshot.ranges, 'lower');
            onSequenceChange({
                ...sequenceData,
                sequence: newSequence
            }, `Lowercase ${selectionSnapshot.length}${unitLabel}`);
        }
    }, [selectionSnapshot, sequenceData, onSequenceChange, unitLabel]);

    // DNA to RNA conversion
    const handleToRNA = useCallback(() => {
        if (isRNA) return;
        const newSequence = sequenceData.sequence.replace(/T/g, 'U').replace(/t/g, 'u');
        onSequenceChange({
            ...sequenceData,
            sequence: newSequence,
            sequenceType: 'rna',
            primers: (sequenceData.primers || []).map((primer) => ({
                ...primer,
                sequence: primer.sequence.replace(/T/g, 'U').replace(/t/g, 'u'),
            })),
            translations: []
        }, 'Convert DNA to RNA');
    }, [sequenceData, onSequenceChange, isRNA]);

    // RNA to DNA conversion
    const handleToDNA = useCallback(() => {
        if (!isRNA) return;
        const newSequence = sequenceData.sequence.replace(/U/g, 'T').replace(/u/g, 't');
        onSequenceChange({
            ...sequenceData,
            sequence: newSequence,
            sequenceType: 'dna',
            primers: (sequenceData.primers || []).map((primer) => ({
                ...primer,
                sequence: primer.sequence.replace(/U/g, 'T').replace(/u/g, 't'),
            })),
            translations: []
        }, 'Convert RNA to DNA');
    }, [sequenceData, onSequenceChange, isRNA]);

    const selectionLength = selectionSnapshot?.length || 0;

    return (
        <div className="edit-panel p-3 space-y-4 text-sm">
            <h4 className="font-semibold text-slate-200">Sequence Editor</h4>

            {/* Tab buttons */}
            <div className="flex gap-1 text-xs">
                {(['edit', 'insert', 'transform'] as const).map(tab => (
                    <button
                        key={tab}
                        onClick={() => setActiveTab(tab)}
                        className={`px-3 py-1.5 rounded capitalize transition-colors ${activeTab === tab
                                ? 'bg-blue-600 text-white'
                                : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                            }`}
                    >
                        {tab}
                    </button>
                ))}
            </div>

            {/* Selection info */}
            <div className="p-2 bg-slate-800 rounded text-xs">
                {selectionSnapshot ? (
                    <div className="space-y-1">
                        <div className="flex justify-between">
                            <span className="text-slate-400">Selection:</span>
                            <span className="text-emerald-400 font-mono">
                                {selectionSnapshot.coordinateLabel}
                            </span>
                        </div>
                        <div className="flex justify-between">
                            <span className="text-slate-400">Length:</span>
                            <span className="text-blue-400 font-mono">{selectionLength} {unitLabel}</span>
                        </div>
                        {selectedSequence.length <= 50 && (
                            <div className="mt-1 font-mono text-slate-300 break-all">
                                {selectedSequence}
                            </div>
                        )}
                    </div>
                ) : (
                    <span className="text-slate-500">No selection - select a region in the viewer</span>
                )}
            </div>
            {wrapsOrigin && (
                <div className="rounded border border-amber-700/40 bg-amber-500/10 px-2 py-1.5 text-xs text-amber-200">
                    Origin-spanning delete, replace, reverse, and complement edits are disabled because they would redefine the construct origin. Case conversion remains safe; rotate the origin before destructive edits.
                </div>
            )}

            {/* Edit tab */}
            {activeTab === 'edit' && (
                <div className="space-y-3">
                    {/* Delete */}
                    <button
                        onClick={handleDelete}
                        disabled={!contiguousSelection}
                        className="w-full py-2 bg-red-600 hover:bg-red-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded font-medium transition-colors"
                    >
                        Delete Selection ({selectionLength} {unitLabel})
                    </button>

                    {/* Replace */}
                    <div className="space-y-2">
                        <label className="text-xs text-slate-400">Replace selection with:</label>
                        <textarea
                            value={replaceText}
                            onChange={(e) => setReplaceText(e.target.value)}
                            placeholder="Enter replacement sequence..."
                            className="w-full px-2 py-1.5 bg-slate-800 border border-slate-600 rounded text-sm font-mono resize-none focus:outline-none focus:border-blue-500"
                            rows={2}
                        />
                        <button
                            onClick={handleReplace}
                            disabled={!contiguousSelection || !replaceText.trim()}
                            className="w-full py-2 bg-amber-600 hover:bg-amber-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded font-medium transition-colors"
                        >
                            Replace
                        </button>
                    </div>

                    {/* Case conversion */}
                    <div className="flex gap-2">
                        <button
                            onClick={handleUppercase}
                            className="flex-1 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-xs transition-colors"
                        >
                            UPPERCASE
                        </button>
                        <button
                            onClick={handleLowercase}
                            className="flex-1 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-xs transition-colors"
                        >
                            lowercase
                        </button>
                    </div>
                </div>
            )}

            {/* Insert tab */}
            {activeTab === 'insert' && (
                <div className="space-y-3">
                    <div className="space-y-2">
                        <label className="text-xs text-slate-400">Insert position:</label>
                        <div className="flex gap-1">
                            {(['start', 'cursor', 'end'] as const).map(pos => (
                                <button
                                    key={pos}
                                    onClick={() => setInsertPosition(pos)}
                                    className={`flex-1 py-1.5 rounded text-xs capitalize transition-colors ${insertPosition === pos
                                            ? 'bg-blue-600 text-white'
                                            : 'bg-slate-700 hover:bg-slate-600 text-slate-300'
                                        }`}
                                >
                                    {pos === 'cursor' ? `Cursor (${selection?.start ?? 'end'})` : pos}
                                </button>
                            ))}
                        </div>
                    </div>

                    <div className="space-y-2">
                        <label className="text-xs text-slate-400">Sequence to insert:</label>
                        <textarea
                            value={insertText}
                            onChange={(e) => setInsertText(e.target.value)}
                            placeholder="Paste or type sequence..."
                            className="w-full px-2 py-1.5 bg-slate-800 border border-slate-600 rounded text-sm font-mono resize-none focus:outline-none focus:border-blue-500"
                            rows={3}
                        />
                        {insertText && (
                            <div className="text-xs text-slate-400">
                                {cleanSequence(insertText).length} {unitLabel} to insert
                            </div>
                        )}
                    </div>

                    <button
                        onClick={handleInsert}
                        disabled={!insertText.trim()}
                        className="w-full py-2 bg-emerald-600 hover:bg-emerald-500 disabled:bg-slate-600 disabled:cursor-not-allowed rounded font-medium transition-colors"
                    >
                        Insert Sequence
                    </button>
                </div>
            )}

            {/* Transform tab */}
            {activeTab === 'transform' && (
                <div className="space-y-3">
                    <div className="text-xs text-slate-400 mb-2">
                        {wrapsOrigin
                            ? 'Rotate the construct origin before transforming this selection.'
                            : selectionSnapshot ? 'Transform selected region:' : 'Select a region to transform'}
                    </div>

                    {/* Reverse/Complement operations */}
                    <div className="grid grid-cols-2 gap-2">
                        <button
                            onClick={handleReverseComplement}
                            disabled={!contiguousSelection}
                            className="py-2 bg-accent hover:bg-accent disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-xs font-medium transition-colors"
                        >
                            Reverse Complement
                        </button>
                        <button
                            onClick={handleReverse}
                            disabled={!contiguousSelection}
                            className="py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-xs font-medium transition-colors"
                        >
                            Reverse Only
                        </button>
                        <button
                            onClick={handleComplement}
                            disabled={!contiguousSelection}
                            className="py-2 bg-slate-700 hover:bg-slate-600 disabled:bg-slate-600 disabled:cursor-not-allowed rounded text-xs font-medium transition-colors"
                        >
                            Complement Only
                        </button>
                    </div>

                    {/* DNA/RNA conversion */}
                    <div className="border-t border-slate-700 pt-3">
                        <div className="text-xs text-slate-400 mb-2">Sequence type conversion:</div>
                        <div className="flex gap-2">
                            <button
                                onClick={handleToDNA}
                                disabled={!isRNA}
                                className={`flex-1 py-2 rounded text-xs font-medium transition-colors ${!isRNA
                                        ? 'bg-blue-600 text-white cursor-default'
                                        : 'bg-slate-700 hover:bg-blue-600 text-slate-300'
                                    }`}
                            >
                                DNA (T)
                            </button>
                            <button
                                onClick={handleToRNA}
                                disabled={isRNA}
                                className={`flex-1 py-2 rounded text-xs font-medium transition-colors ${isRNA
                                        ? 'bg-orange-600 text-white cursor-default'
                                        : 'bg-slate-700 hover:bg-orange-600 text-slate-300'
                                    }`}
                            >
                                RNA (U)
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Sequence stats */}
            <div className="border-t border-slate-700 pt-3">
                <div className="grid grid-cols-2 gap-2 text-xs">
                    <div className="flex justify-between">
                        <span className="text-slate-400">Total:</span>
                        <span className="font-mono">{sequenceData.sequence.length.toLocaleString()} {unitLabel}</span>
                    </div>
                    <div className="flex justify-between">
                        <span className="text-slate-400">Type:</span>
                        <span className="uppercase">{sequenceData.sequenceType || 'DNA'}</span>
                    </div>
                </div>
            </div>
        </div>
    );
}
