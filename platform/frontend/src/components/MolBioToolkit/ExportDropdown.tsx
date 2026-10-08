/**
 * ExportDropdown - Export sequence in multiple formats
 */

import { useState, useRef, useEffect } from 'react';

interface ExportDropdownProps {
    sequenceData: {
        name: string;
        sequence: string;
        features?: Array<{
            name: string;
            type: string;
            start: number;
            end: number;
            strand: number;
        }>;
        circular?: boolean;
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
            content = `>${sequenceData.name}\n${sequenceData.sequence.match(/.{1,80}/g)?.join('\n') || ''}`;
            extension = 'fasta';
        } else {
            // Simple GenBank format
            const date = new Date().toLocaleDateString('en-US', {
                day: '2-digit',
                month: 'short',
                year: 'numeric'
            }).toUpperCase().replace(',', '');

            const lines = [
                `LOCUS       ${sequenceData.name.substring(0, 16).padEnd(16)} ${sequenceData.sequence.length} bp    DNA     ${sequenceData.circular ? 'circular' : 'linear'}   UNK ${date}`,
                `DEFINITION  ${sequenceData.name}`,
                `ACCESSION   .`,
                `VERSION     .`,
                `KEYWORDS    .`,
                `SOURCE      .`,
                `  ORGANISM  .`,
                `FEATURES             Location/Qualifiers`
            ];

            // Add features
            if (sequenceData.features) {
                for (const feat of sequenceData.features) {
                    const location = feat.strand === -1
                        ? `complement(${feat.start + 1}..${feat.end + 1})`
                        : `${feat.start + 1}..${feat.end + 1}`;
                    lines.push(`     ${feat.type.padEnd(16)}${location}`);
                    lines.push(`                     /label="${feat.name}"`);
                }
            }

            // Add origin (sequence)
            lines.push('ORIGIN');
            const seq = sequenceData.sequence.toLowerCase();
            for (let i = 0; i < seq.length; i += 60) {
                const lineNum = (i + 1).toString().padStart(9);
                const chunk = seq.slice(i, i + 60).match(/.{1,10}/g)?.join(' ') || '';
                lines.push(`${lineNum} ${chunk}`);
            }
            lines.push('//');

            content = lines.join('\n');
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
