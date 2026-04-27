/**
 * HPLC Data Processor Page
 */

import { useState } from 'react';
import { ChromatogramAnalysis, CalibrationCurve, HplcQuantification } from './ChromatogramAnalysis';
import { EmpowerImport } from './EmpowerImport';

type AnalysisType = 'chromatogram' | 'calibration' | 'quantify' | 'empower';

export function HplcPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('chromatogram');

    const analysisOptions: { id: AnalysisType; label: string }[] = [
        { id: 'empower', label: 'Empower Import' },
        { id: 'chromatogram', label: 'Chromatogram Analysis' },
        { id: 'calibration', label: 'Calibration Curve' },
        { id: 'quantify', label: 'Sample Quantification' },
    ];

    return (
        <div className="p-6">
            <div className="mb-6">
                <h2 className="text-2xl font-bold text-text-primary">HPLC Data Processor</h2>
                <p className="text-text-secondary">Peak detection, baseline correction, fitting, integration, and quantification</p>
            </div>

            {/* Analysis Type Selector */}
            <div className="mb-6 flex flex-wrap gap-2">
                {analysisOptions.map(option => (
                    <button
                        key={option.id}
                        onClick={() => setActiveAnalysis(option.id)}
                        className={`px-4 py-2 text-sm font-medium transition-colors ${activeAnalysis === option.id
                                ? 'bg-accent-primary text-white'
                                : 'bg-bg-secondary text-text-secondary hover:text-text-primary border border-border-primary'
                            }`}
                    >
                        {option.label}
                    </button>
                ))}
            </div>

            {/* Active Analysis Panel */}
            <div className="border border-border-primary bg-bg-secondary p-4">
                {activeAnalysis === 'chromatogram' && <ChromatogramAnalysis />}
                {activeAnalysis === 'calibration' && <CalibrationCurve />}
                {activeAnalysis === 'quantify' && <HplcQuantification />}
                {activeAnalysis === 'empower' && <EmpowerImport />}
            </div>
        </div>
    );
}

export { ChromatogramAnalysis, CalibrationCurve, HplcQuantification } from './ChromatogramAnalysis';
export { EmpowerImport } from './EmpowerImport';
