/**
 * ExportDropdown - Export sequence in multiple formats
 */

import { useState, useRef, useEffect } from 'react';
import { jsonToGenbank } from '@teselagen/bio-parsers';
import type { HistoryEntry } from './hooks/useSequenceHistory';
import {
    buildPrimersTsv,
    canonicalizeExportablePrimer,
} from './utils/exportData';

interface ExportDropdownProps {
    sequenceData: {
        name: string;
        description?: string;
        sequence: string;
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
    className?: string;
}

function featureLocations(feature: NonNullable<ExportDropdownProps['sequenceData']['features']>[number]) {
    return (feature.segments && feature.segments.length > 0
        ? feature.segments
        : [{ start: feature.start, end: feature.end }]).map((segment) => ({
            start: segment.start,
            end: segment.end,
        }));
}

function formatNotes(feature: NonNullable<ExportDropdownProps['sequenceData']['features']>[number]) {
    return {
        ...(feature.notes || {}),
        ...(feature.qualifiers || {}),
        provenance: feature.provenance || undefined,
    };
}

export function ExportDropdown({ sequenceData, historyJournal = [], className }: ExportDropdownProps) {
    const [isOpen, setIsOpen] = useState(false);
    const dropdownRef = useRef<HTMLDivElement>(null);

    // Close dropdown when clicking outside
    useEffect(() => {
        function handleClickOutside(event: MouseEvent) {
            if (dropdownRef.current && !dropdownRef.current.contains(event.target as Node)) {
                setIsOpen(false);
            }
        }
        document.addEventListener('mousedown', handleClickOutside);
        return () => document.removeEventListener('mousedown', handleClickOutside);
    }, []);

    const exportAs = (format: 'genbank' | 'fasta' | 'json' | 'features_tsv' | 'primers_tsv' | 'history_txt') => {
        let content: string;
        let extension: string;

        if (format === 'fasta') {
            const header = sequenceData.description
                ? `>${sequenceData.name} ${sequenceData.description}`
                : `>${sequenceData.name}`;
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
            content = jsonToGenbank({
                name: sequenceData.name,
                description: sequenceData.description,
                sequence: sequenceData.sequence,
                circular: sequenceData.circular,
                type: sequenceData.sequenceType === 'rna' ? 'RNA' : 'DNA',
                features: (sequenceData.features || []).map((feature) => ({
                    ...feature,
                    locations: featureLocations(feature),
                    notes: formatNotes(feature),
                })),
                primers: (sequenceData.primers || []).map((primer) => (
                    canonicalizeExportablePrimer(
                        primer,
                        sequenceData.sequence.length,
                        Boolean(sequenceData.circular),
                    )
                )),
            }) || '';
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
    };

    return (
        <div ref={dropdownRef} className={`relative inline-block ${className || ''}`}>
            <button
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

            {isOpen && (
                <div className="absolute right-0 mt-1 w-48 bg-slate-700 border border-slate-600 rounded shadow-lg z-50">
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
                </div>
            )}
        </div>
    );
}
