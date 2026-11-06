/**
 * ExportDropdown - Export sequence in multiple formats
 */

import { useState, useRef, useEffect, useLayoutEffect, useId } from 'react';
import { createPortal } from 'react-dom';
import type { HistoryEntry } from './hooks/useSequenceHistory';
import { hasStickyDigestEnds } from '../../lib/retainedDigestFragment';
import type { RestrictionDigestFragment } from '../../lib/restrictionAnalysis';
import {
    buildPrimersTsv,
    genbankFeature,
    genbankPrimerFeatures,
} from './utils/exportData';

interface ExportDropdownProps {
    sequenceData: {
        name: string;
        description?: string;
        sequence: string;
        digestFragment?: RestrictionDigestFragment;
        features?: Array<{
            name: string;
            type: string;
            start: number;
            end: number;
            strand: number;
            color?: string;
            description?: string;
            notes?: Record<string, unknown>;
            qualifiers?: Record<string, unknown>;
            provenance?: Record<string, unknown>;
            segments?: Array<{ start: number; end: number }>;
        }>;
        primers?: Array<{
            name: string;
            sequence: string;
            sequenceType?: 'dna' | 'rna';
            start: number;
            end: number;
            strand: 1 | -1;
            tm?: number;
            gc_percent?: number;
            binding_status?: 'needs_review';
            binding_warning?: string;
            sites?: Array<{
                start: number;
                end: number;
                strand: 1 | -1;
            }>;
        }>;
        circular?: boolean;
        sequenceType?: 'dna' | 'rna' | 'protein';
    };
    historyJournal?: HistoryEntry[];
    onExportToProject?: () => void;
    projectExportDisabled?: boolean;
    onExportBranch?: () => Promise<void>;
    branchExportDisabled?: boolean;
    className?: string;
}

function describeEnds(fragment: RestrictionDigestFragment): string {
    const end = (e: RestrictionDigestFragment['left_end']) => e.kind === 'blunt' || e.kind === 'natural'
        ? e.kind
        : `${e.kind === 'five_prime_overhang' ? "5'" : "3'"} overhang ${e.overhang_sequence_5to3 ?? ''} (${e.protruding_strand ?? '?'} strand)`;
    return `[sticky ends: left ${end(fragment.left_end)}; right ${end(fragment.right_end)}; top strand only]`;
}

export function ExportDropdown({ sequenceData, historyJournal = [], className, onExportToProject, projectExportDisabled = false, onExportBranch, branchExportDisabled = false }: ExportDropdownProps) {
    const [isOpen, setIsOpen] = useState(false);
    const [exportError, setExportError] = useState<string | null>(null);
    const dropdownRef = useRef<HTMLDivElement>(null);
    const buttonRef = useRef<HTMLButtonElement>(null);
    const popupRef = useRef<HTMLDivElement>(null);
    const popupId = useId();
    const [position, setPosition] = useState({ left: 8, top: 8 });

    useLayoutEffect(() => {
        if (!isOpen) return;
        const reposition = () => {
            const anchor = buttonRef.current?.getBoundingClientRect();
            const popup = popupRef.current?.getBoundingClientRect();
            if (!anchor || !popup) return;
            setPosition({
                left: Math.max(8, Math.min(anchor.right - popup.width, window.innerWidth - popup.width - 8)),
                top: Math.max(8, Math.min(anchor.bottom + 4, window.innerHeight - popup.height - 8)),
            });
        };
        reposition();
        popupRef.current?.querySelector('button')?.focus();
        window.addEventListener('resize', reposition);
        window.addEventListener('scroll', reposition, true);
        return () => {
            window.removeEventListener('resize', reposition);
            window.removeEventListener('scroll', reposition, true);
        };
    }, [isOpen]);

    useEffect(() => {
        if (!isOpen) return;
        const outside = (event: MouseEvent | FocusEvent) => {
            if (!dropdownRef.current?.contains(event.target as Node)
                && !popupRef.current?.contains(event.target as Node)) setIsOpen(false);
        };
        const escape = (event: KeyboardEvent) => {
            if (event.key === 'Escape') {
                event.preventDefault();
                setIsOpen(false);
                buttonRef.current?.focus();
            }
        };
        document.addEventListener('mousedown', outside);
        document.addEventListener('focusin', outside);
        document.addEventListener('keydown', escape);
        return () => {
            document.removeEventListener('mousedown', outside);
            document.removeEventListener('focusin', outside);
            document.removeEventListener('keydown', escape);
        };
    }, [isOpen]);

    const sticky = hasStickyDigestEnds(sequenceData.digestFragment);
    const stickyNote = sticky && sequenceData.digestFragment ? describeEnds(sequenceData.digestFragment) : '';

    const exportAs = async (format: 'genbank' | 'fasta' | 'json' | 'features_tsv' | 'primers_tsv' | 'history_txt') => {
        setExportError(null);
        try {
            let content: string;
            let extension: string;

            if (format === 'fasta') {
                const header = [`>${sequenceData.name}`, sequenceData.description, stickyNote].filter(Boolean).join(' ');
                content = `${header}\n${sequenceData.sequence.match(/.{1,80}/g)?.join('\n') || ''}`;
                extension = 'fasta';
            } else if (format === 'json') {
                content = JSON.stringify({
                    ...sequenceData,
                    historyJournal,
                }, null, 2);
                extension = 'molbio.json';
            } else if (format === 'features_tsv') {
                const rows = (sequenceData.features || []).map((feature) => [
                    feature.name,
                    feature.type,
                    feature.start + 1,
                    feature.end,
                    feature.strand,
                    (feature.segments || []).map((segment) => `${segment.start + 1}-${segment.end}`).join(';'),
                    feature.description || '',
                ].join('\t'));
                content = ['name\ttype\tstart\tend\tstrand\tsegments\tdescription', ...rows].join('\n');
                extension = 'features.tsv';
            } else if (format === 'primers_tsv') {
                content = buildPrimersTsv(
                    sequenceData.primers || [],
                    sequenceData.sequenceType === 'rna' ? 'rna' : 'dna',
                    sequenceData.sequence.length,
                    Boolean(sequenceData.circular),
                );
                extension = 'primers.tsv';
            } else if (format === 'history_txt') {
                content = historyJournal.length === 0
                    ? 'No history entries recorded for this workspace.\n'
                    : historyJournal.map((entry) => `${entry.timestamp}\t${entry.label}\t${entry.summary}`).join('\n');
                extension = 'history.txt';
            } else {
                const { jsonToGenbank } = await import('@teselagen/bio-parsers');
                const length = sequenceData.sequence.length;
                const circular = Boolean(sequenceData.circular);
                content = jsonToGenbank({
                    name: sequenceData.name,
                    description: sequenceData.description,
                    sequence: sequenceData.sequence,
                    circular: sequenceData.circular,
                    type: sequenceData.sequenceType === 'rna' ? 'RNA' : 'DNA',
                    comments: stickyNote ? [`${stickyNote} Only the top strand is encoded.`] : undefined,
                    features: [
                        ...(sequenceData.features || []).map(genbankFeature),
                        ...genbankPrimerFeatures(sequenceData.primers || [], length, circular),
                    ],
                }, { inclusive1BasedEnd: true }) || '';
                extension = 'gb';
            }

            // Download
            const blob = new Blob([content], { type: 'text/plain' });
            const url = URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `${sequenceData.name.replace(/[^a-z0-9]/gi, '_')}.${extension}`;
            document.body.appendChild(link);
            link.click();
            document.body.removeChild(link);
            URL.revokeObjectURL(url);

            setIsOpen(false);
            buttonRef.current?.focus();
        } catch (error) {
            setExportError(error instanceof Error ? error.message : 'Export failed');
        }
    };

    return (
        <div ref={dropdownRef} className={`relative inline-block ${className || ''}`}>
            <button
                ref={buttonRef}
                type="button"
                aria-expanded={isOpen}
                aria-controls={isOpen ? popupId : undefined}
                onClick={() => setIsOpen(!isOpen)}
                className="flex items-center gap-1 px-3 py-1.5 bg-slate-700 hover:bg-slate-600 rounded text-sm text-slate-200 transition-colors"
            >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16v1a3 3 0 003 3h10a3 3 0 003-3v-1m-4-4l-4 4m0 0l-4-4m4 4V4" />
                </svg>
                Export
                <svg className="w-3 h-3" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                </svg>
            </button>

            {exportError && <p role="alert">Could not export: {exportError}</p>}
            {isOpen && createPortal(
                <div ref={popupRef} id={popupId} role="group" aria-label="Export formats"
                    style={{ ...position, maxWidth: 'calc(100vw - 16px)', maxHeight: 'calc(100vh - 16px)' }}
                    className="fixed w-56 overflow-y-auto bg-slate-700 border border-slate-600 rounded shadow-lg z-[100]">
                    {sticky && <p className="border-b border-slate-600 px-3 py-2 text-xs text-slate-300">Sticky ends: FASTA and GenBank encode only the top strand; the ends are noted in the header. Toolkit JSON keeps both.</p>}
                    {onExportToProject && <button type="button" disabled={projectExportDisabled} onClick={() => { setIsOpen(false); onExportToProject(); }} className="w-full border-b border-slate-600 px-3 py-2 text-left text-sm font-semibold text-cyan-300 hover:bg-slate-600 disabled:opacity-50" title={projectExportDisabled ? 'Save this sequence before exporting it to a Project' : 'Add this saved sequence and optional history to a Project'}>Export to Project…</button>}
                    <button
                        onClick={() => exportAs('genbank')}
                        className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 transition-colors"
                    >
                        GenBank (.gb)
                    </button>
                    <button
                        onClick={() => exportAs('fasta')}
                        className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 transition-colors"
                    >
                        FASTA (.fasta)
                    </button>
                    <button
                        onClick={() => exportAs('json')}
                        className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 transition-colors"
                    >
                        Toolkit JSON (.molbio.json)
                    </button>
                    <button
                        onClick={() => exportAs('features_tsv')}
                        className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 transition-colors"
                    >
                        Features TSV (.tsv)
                    </button>
                    <button
                        onClick={() => exportAs('primers_tsv')}
                        className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 transition-colors"
                    >
                        Primers TSV (.tsv)
                    </button>
                    <button
                        onClick={() => exportAs('history_txt')}
                        className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 transition-colors"
                    >
                        History Text (.txt)
                    </button>
                    {onExportBranch && <button type="button" disabled={branchExportDisabled}
                        title={branchExportDisabled ? 'Save this sequence first to export its branch' : 'Export this molecule with its derivation'}
                        onClick={async () => {
                            setExportError(null);
                            try { await onExportBranch(); setIsOpen(false); buttonRef.current?.focus(); }
                            catch (error) { setExportError(error instanceof Error ? error.message : 'Export failed'); }
                        }}
                        className="w-full text-left px-3 py-2 text-sm text-slate-200 hover:bg-slate-600 transition-colors disabled:opacity-50"
                    >
                        Branch (.molbio-branch.json)
                    </button>}
                </div>, document.body
            )}
        </div>
    );
}
