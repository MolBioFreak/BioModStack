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
import { AnalyticalQcWorkbench } from '../assay/AnalyticalQcWorkbench';
import { ChromatogramAnalysis, CalibrationCurve, HplcQuantification } from './ChromatogramAnalysis';
import { EmpowerImport } from './EmpowerImport';

type AnalysisType = 'empower' | 'qc' | 'chromatogram' | 'calibration' | 'quantify';

const analysisOptions: Array<AssayNavItem<AnalysisType>> = [
    {
        id: 'empower',
        label: 'Empower Import',
        status: 'Waters / Empower',
        description: 'AIA/ARW/ZIP/CSV, peaks, SST.',
    },
    {
        id: 'qc',
        label: 'QC + Cross-run Stats',
        status: 'Clean + compare',
        description: 'Clean, group, compare.',
    },
    {
        id: 'chromatogram',
        label: 'Chromatogram Analysis',
        status: 'Signal + peak picking',
        description: 'Baseline, peaks, integration.',
    },
    {
        id: 'calibration',
        label: 'Calibration Curve',
        status: 'Explicit standards',
        description: 'Concentration vs area.',
    },
    {
        id: 'quantify',
        label: 'Sample Quantification',
        status: 'Sample IDs required',
        description: 'Peak areas vs calibration.',
    },
];

const statusItems = [
    {
        title: 'Source of truth',
        value: '/api/assay-analytics chromatography routes',
        tone: 'accent' as const,
    },
    {
        title: 'Empower DB/RAW',
        value: 'Export AIA .cdf/.arw or CSV/ASCII first',
        tone: 'warning' as const,
    },
    {
        title: 'Visualization',
        value: 'Chromatograms, calibration, SST, isoforms',
    },
];

export function HplcPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('empower');

    return (
        <div className="space-y-6 p-6">
            <AssayWorkbenchIntro
                eyebrow="BMS Chromatography Workbench"
                title="Waters / Empower chromatography"
                description="Imports, peaks, calibration, isoforms."
            >
                <AssayStatusStrip items={statusItems} />
            </AssayWorkbenchIntro>

            <AssaySubnavGrid
                items={analysisOptions}
                activeId={activeAnalysis}
                onChange={setActiveAnalysis}
                columnsClass="sm:grid-cols-2 xl:grid-cols-5"
            />

            <AssayPanel className="p-4">
                <div hidden={activeAnalysis !== 'empower'}>
                    <EmpowerImport />
                </div>
                <div hidden={activeAnalysis !== 'qc'}>
                    <AnalyticalQcWorkbench />
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
