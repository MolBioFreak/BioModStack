/**
 * BMS qPCR Workbench Page
 */

import { useState } from 'react';
import {
    AssayPanel,
    AssayStatusStrip,
    AssaySubnavGrid,
    AssayWorkbenchIntro,
    type AssayNavItem,
} from '../assay/AssayWorkbenchPrimitives';
import { RawDataImport } from './RawDataImport';
import { DeltaCqPanel, DeltaDeltaCqPanel } from './DeltaCqPanels';
import { StandardCurvePanel, QuantificationPanel, AnovaDunnettPanel } from './QuantificationPanels';

type AnalysisType = 'import' | 'deltacq' | 'deltadeltacq' | 'stdcurve' | 'quantify' | 'anova';

const analysisOptions: Array<AssayNavItem<AnalysisType>> = [
    {
        id: 'import',
        label: 'Instrument Import',
        status: 'QuantStudio / StepOnePlus',
        description: 'Import EDS/Excel/CSV; plate, curve, replicate QC.',
    },
    {
        id: 'deltacq',
        label: 'ΔCq Analysis',
        status: 'Reference-normalized',
        description: 'Cq rows + explicit reference/target genes.',
    },
    {
        id: 'deltadeltacq',
        label: 'ΔΔCq & Fold Change',
        status: 'Control-group required',
        description: 'Control/treatment groups → fold-change.',
    },
    {
        id: 'stdcurve',
        label: 'Standard Curve',
        status: 'MIQE metrics',
        description: 'Slope, efficiency, R², residuals, flags.',
    },
    {
        id: 'quantify',
        label: 'Absolute Quantification',
        status: 'Sample IDs required',
        description: 'Unknowns against standards; sample IDs required.',
    },
    {
        id: 'anova',
        label: 'ANOVA + Dunnett',
        status: 'Named groups only',
        description: 'Control + named treatment groups.',
    },
];

const statusItems = [
    {
        title: 'Source of truth',
        value: '/api/assay-analytics qPCR routes',
        tone: 'accent' as const,
    },
    {
        title: 'Supported inputs',
        value: 'EDS, XLS/XLSX, or CSV instrument exports',
    },
    {
        title: 'Visualization',
        value: 'Plotly plate maps, curves, standard curves, and QC tables',
    },
    {
        title: 'Data policy',
        value: 'BMS does not preload built-in assay rows',
        tone: 'warning' as const,
    },
];

export function QpcrPage() {
    const [activeAnalysis, setActiveAnalysis] = useState<AnalysisType>('import');

    return (
        <div className="space-y-6 p-6">
            <AssayWorkbenchIntro
                eyebrow="BMS qPCR Workbench"
                title="QuantStudio / StepOnePlus analysis on real assay data"
                description="Instrument exports or pasted Cq tables; no built-in rows."
            >
                <AssayStatusStrip items={statusItems} columnsClass="sm:grid-cols-2 xl:grid-cols-4" />
            </AssayWorkbenchIntro>

            <AssaySubnavGrid
                items={analysisOptions}
                activeId={activeAnalysis}
                onChange={setActiveAnalysis}
                columnsClass="sm:grid-cols-2 xl:grid-cols-3"
            />

            <AssayPanel className="p-4">
                <div hidden={activeAnalysis !== 'import'}>
                    <RawDataImport />
                </div>
                <div hidden={activeAnalysis !== 'deltacq'}>
                    <DeltaCqPanel />
                </div>
                <div hidden={activeAnalysis !== 'deltadeltacq'}>
                    <DeltaDeltaCqPanel />
                </div>
                <div hidden={activeAnalysis !== 'stdcurve'}>
                    <StandardCurvePanel />
                </div>
                <div hidden={activeAnalysis !== 'quantify'}>
                    <QuantificationPanel />
                </div>
                <div hidden={activeAnalysis !== 'anova'}>
                    <AnovaDunnettPanel />
                </div>
            </AssayPanel>
        </div>
    );
}
