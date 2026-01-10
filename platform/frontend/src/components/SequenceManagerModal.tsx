/**
 * SequenceManagerModal - Popup modal for creating/editing/managing user sequences
 * 
 * Features:
 * - List view of saved sequences with search
 * - Create new sequence with name, sequence, description, organism, UniProt ID
 * - Edit existing sequences
 * - Delete sequences with confirmation
 * - Sequence validation (amino acid characters only)
 */

import { SequenceManager } from './SequenceManager';
import type { UserSequence } from '../lib/api';

interface SequenceManagerModalProps {
    isOpen: boolean;
    onClose: () => void;
    onSelect?: (sequence: UserSequence) => void;
    initialSequence?: string;  // Pre-fill when saving from textarea
    initialName?: string;
}

export function SequenceManagerModal({
    isOpen,
    onClose,
    onSelect,
    initialSequence = '',
    initialName = ''
}: SequenceManagerModalProps) {
    if (!isOpen) return null;

    return (
        <div className="fixed inset-0 bg-black/60 backdrop-blur-sm flex items-center justify-center p-4 z-50">
            <div className="bg-slate-900 border border-slate-700 rounded-xl w-full max-w-3xl max-h-[85vh] flex flex-col shadow-2xl">
                {/* Header */}
                <div className="p-5 border-b border-slate-700 flex justify-between items-center bg-slate-800/50 rounded-t-xl">
                    <div className="flex items-center gap-3">
                        <div className="w-10 h-10 rounded-lg bg-emerald-500/20 flex items-center justify-center text-emerald-400 text-xl">
                            🧬
                        </div>
                        <div>
                            <h3 className="font-semibold text-slate-200 text-lg">
                                Sequence Library
                            </h3>
                            <p className="text-xs text-slate-500">
                                Manage your saved amino acid sequences
                            </p>
                        </div>
                    </div>
                    <button
                        onClick={onClose}
                        className="p-2 hover:bg-slate-700 rounded-lg text-slate-400 hover:text-white transition-colors"
                    >
                        ✕
                    </button>
                </div>

                {/* Content */}
                <SequenceManager
                    onSelect={onSelect}
                    onClose={onClose}
                    initialSequence={initialSequence}
                    initialName={initialName}
                />
            </div>
        </div>
    );
}
