/**
 * BMS Chromatography Workbench Page
 */

import { useState } from 'react';
import {
    AssayPanel,
    AssayStatusStrip,
    AssaySubnavGrid,
    AssayWorkbenchIntro,
    type AssayNavItem,
} from '../assay/AssayWorkbenchPrimitives';
import { ChromatogramAnalysis, CalibrationCurve, HplcQuantification } from './ChromatogramAnalysis';
import { EmpowerImport } from './EmpowerImport';

type AnalysisType = 'chromatogram' | 'calibration' | 'quantify' | 'empower';

const analysisOptions: Array<AssayNavItem<AnalysisType>> = [
    {
        id: 'empower',
        label: 'Empower AIA/ARW/CSV Import',
        status: 'Waters chromatogram review',
        description: 'Import real Empower AIA .cdf, ARW chromatogram text, ZIP batches, or CSV/ASCII peak-table exports; review chromatograms, peaks, SST summaries, and plasmid tracking logs.',
    },
    {
        id: 'chromatogram',
        label: 'Chromatogram Analysis',
        status: 'Signal + peak picking',
        description: 'Analyze real time/signal arrays with baseline correction, peak detection, integration, and Plotly traces.',
    },
    {
        id: 'calibration',
        label: 'Calibration Curve',
        status: 'Explicit standards',
        description: 'Fit concentration versus area from pasted standard levels with optional through-origin regression.',
    },
    {
        id: 'quantify',
        label: 'Sample Quantification',
        status: 'Sample IDs required',
        description: 'Quantify real sample peak areas against a calibration series; each unknown must carry a real identifier.',
    },
];

const statusItems = [
    {
        title: 'Source of truth',
        value: '/api/assay-analytics chromatography routes',
        tone: 'accent' as const,
    },
    {
        title: 'Unsupported containers',
        value: 'Proprietary Empower DB/RAW files: export AIA .cdf/.arw or CSV/ASCII first',
        tone: 'warning' as const,
    },
    {
        title: 'Visualization',
        value: 'Plotly chromatograms, calibration fits, SST and isoform tables',
    },
];

export function HplcPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('empower');

    return (
        <div className="space-y-6 p-6">
            <AssayWorkbenchIntro
                eyebrow="BMS Chromatography Workbench"
                title="Waters / Empower chromatography and plasmid isoform review"
                description="Work from real Empower 3 AIA .cdf, ARW chromatogram text, ZIP batches, CSV/ASCII exports, or pasted chromatogram/calibration data. Proprietary Empower database/RAW containers still require an Empower-side export to AIA .cdf/.arw or CSV/ASCII before BMS analysis."
            >
                <AssayStatusStrip items={statusItems} />
            </AssayWorkbenchIntro>

            <AssaySubnavGrid
                items={analysisOptions}
                activeId={activeAnalysis}
                onChange={setActiveAnalysis}
                columnsClass="sm:grid-cols-2 xl:grid-cols-4"
            />

            <AssayPanel className="p-4">
                <div hidden={activeAnalysis !== 'empower'}>
                    <EmpowerImport />
                </div>
                <div hidden={activeAnalysis !== 'chromatogram'}>
                    <ChromatogramAnalysis />
                </div>
                <div hidden={activeAnalysis !== 'calibration'}>
                    <CalibrationCurve />
                </div>
                <div hidden={activeAnalysis !== 'quantify'}>
                    <HplcQuantification />
                </div>
            </AssayPanel>
        </div>
    );
}
