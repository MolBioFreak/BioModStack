/**
 * ExportDropdown - Export sequence in multiple formats
 */

import { useState, useRef, useEffect } from 'react';
import { jsonToGenbank } from '@teselagen/bio-parsers';

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
        }>;
        primers?: Array<{
            name: string;
            sequence: string;
            start: number;
            end: number;
            strand: number;
            tm?: number;
            gc_percent?: number;
        }>;
        circular?: boolean;
        sequenceType?: 'dna' | 'rna' | 'protein';
    };
    className?: string;
}

export function ExportDropdown({ sequenceData, className }: ExportDropdownProps) {
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

    const exportAs = (format: 'genbank' | 'fasta') => {
        let content: string;
        let extension: string;

        if (format === 'fasta') {
            const header = sequenceData.description
                ? `>${sequenceData.name} ${sequenceData.description}`
                : `>${sequenceData.name}`;
            content = `${header}\n${sequenceData.sequence.match(/.{1,80}/g)?.join('\n') || ''}`;
            extension = 'fasta';
        } else {
            content = jsonToGenbank({
                name: sequenceData.name,
                description: sequenceData.description,
                sequence: sequenceData.sequence,
                circular: sequenceData.circular,
                type: sequenceData.sequenceType === 'rna' ? 'RNA' : 'DNA',
                features: sequenceData.features || [],
                primers: sequenceData.primers || [],
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
                <div className="absolute right-0 mt-1 w-40 bg-slate-700 border border-slate-600 rounded shadow-lg z-50">
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
                </div>
            )}
        </div>
    );
}
