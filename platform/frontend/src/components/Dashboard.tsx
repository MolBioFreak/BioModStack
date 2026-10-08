import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { fetchJobs, cancelJob, resubmitJob, fetchJobLogs, resumeJob, deleteJobPermanently, forceRunJob } from '../lib/api';
import type { JobLogs, Job } from '../lib/api';

import { QuickViewer } from './QuickViewer';
import { JobQueuePanel } from './JobQueuePanel';
import { GpuSchedulerControls } from './dashboard/SystemResources';
import { DashboardTelemetry } from './dashboard/DashboardTelemetry';
import { JobQueueTable } from './dashboard/JobQueueTable';
import { JobFilters } from './dashboard/JobFilters';
import { StructureReorchestratePanel } from './dashboard/StructureReorchestratePanel';
import {
    buildStructureReorchestrateOverrides,
    deriveStructureReorchestrateSettings,
    isStructureReorchestrateJob,
    type StructureReorchestrateSettings,
} from './dashboard/reorchestrateStructureSettings';

const isNgsJob = (job: Pick<Job, 'model_id' | 'mode'>): boolean => {
    const modelId = (job.model_id || '').toLowerCase();
    const mode = (job.mode || '').toLowerCase();
    return (
        modelId === 'nanopore' ||
        modelId.includes('nanopore') ||
        mode === 'methylation_analysis' ||
        mode === 'nanopore_methylation'
    );
};

interface ResumeSettingsForm {
    rfantibodyNumDesigns: number;
    seqsPerDesign: number;
    rfantibodyDiffusionSteps: number;
    rfantibodyGuideScale: number;
    fampnnMaxPsce: number;
    fampnnMaxResiduePsce: number;
    boltzMaxBinderRmsd: number;
    boltzMinPtmInterface: number;
    runMaturation: boolean;
    runThermoMPNN: boolean;
    runAnarciiPost: boolean;
}

type ResumeNumericField =
    | 'rfantibodyNumDesigns'
    | 'seqsPerDesign'
    | 'rfantibodyDiffusionSteps'
    | 'rfantibodyGuideScale'
    | 'fampnnMaxPsce'
    | 'fampnnMaxResiduePsce'
    | 'boltzMaxBinderRmsd'
    | 'boltzMinPtmInterface';

const DEFAULT_RESUME_SETTINGS_FORM: ResumeSettingsForm = {
    rfantibodyNumDesigns: 10,
    seqsPerDesign: 8,
    rfantibodyDiffusionSteps: 50,
    rfantibodyGuideScale: 10,
    fampnnMaxPsce: 2.0,
    fampnnMaxResiduePsce: 4.0,
    boltzMaxBinderRmsd: 2.0,
    boltzMinPtmInterface: 0.5,
    runMaturation: false,
    runThermoMPNN: false,
    runAnarciiPost: false,
};

const toNumber = (value: unknown, fallback: number): number => {
    const parsed = Number(value);
    return Number.isFinite(parsed) ? parsed : fallback;
};

const toBoolean = (value: unknown, fallback = false): boolean => {
    if (typeof value === 'boolean') return value;
    if (typeof value === 'string') {
        const normalized = value.trim().toLowerCase();
        if (['true', '1', 'yes', 'on'].includes(normalized)) return true;
        if (['false', '0', 'no', 'off'].includes(normalized)) return false;
    }
    if (typeof value === 'number') return value !== 0;
    return fallback;
};

const clamp = (value: number, min: number, max: number): number =>
    Math.max(min, Math.min(max, value));

const formatResumeStageLabel = (stage: string): string => {
    const normalized = stage.trim().toLowerCase();
    switch (normalized) {
        case 'auto':
            return 'Auto';
        case 'rfantibody':
            return 'RFantibody';
        case 'fampnn':
            return 'FAMPNN';
        case 'caliby':
            return 'Caliby';
        case 'structure_validation':
            return 'Structure Validation';
        case 'boltzgen':
            return 'BoltzGen';
        case 'post_rfantibody':
            return 'RFantibody Review';
        case 'post_fampnn':
            return 'FAMPNN Review';
        case 'post_caliby':
            return 'Caliby Review';
        case 'post_structure_validation':
            return 'Structure Validation Review';
        case 'post_boltzgen':
            return 'BoltzGen Review';
        case 'post_ppiflow_generator':
            return 'PPIFlow Review';
        default:
            return stage.replace(/_/g, ' ');
    }
};

const mapAwaitingStageToResumeStage = (awaitingStage?: string | null): string => {
    switch ((awaitingStage || '').trim().toLowerCase()) {
        case 'post_rfantibody':
            return 'rfantibody';
        case 'post_fampnn':
            return 'fampnn';
        case 'post_caliby':
            return 'caliby';
        case 'post_structure_validation':
            return 'structure_validation';
        case 'post_boltzgen':
            return 'boltzgen';
        case 'post_ppiflow_generator':
            return 'ppiflow';
        default:
            return 'auto';
    }
};

export function Dashboard() {
    const queryClient = useQueryClient();

    const [quickViewJobId, setQuickViewJobId] = useState<string | null>(null);
    const [logsModalJobId, setLogsModalJobId] = useState<string | null>(null);
    const [logsData, setLogsData] = useState<JobLogs | null>(null);
    const [logsLoading, setLogsLoading] = useState(false);
    const [resumeSettingsJob, setResumeSettingsJob] = useState<Job | null>(null);
    const [resumeSettingsFromStage, setResumeSettingsFromStage] = useState<string>('auto');
    const [resumeSettingsNameSuffix, setResumeSettingsNameSuffix] = useState<string>('retuned');
    const [resumeSettingsForm, setResumeSettingsForm] = useState<ResumeSettingsForm>(DEFAULT_RESUME_SETTINGS_FORM);
    const [structureReorchestrateSettings, setStructureReorchestrateSettings] = useState<StructureReorchestrateSettings | null>(null);
    const [resumeDialogMode, setResumeDialogMode] = useState<'resume' | 'reorchestrate'>('resume');
    const [resumeSettingsError, setResumeSettingsError] = useState<string | null>(null);
    const [search, setSearch] = useState('');
    const [statusFilter, setStatusFilter] = useState('all');
    const [showNgsJobs, setShowNgsJobs] = useState(true);
    // Debug mode is read from localStorage (toggled via Layout's DebugMenu or browser dev tools)
    const debugMode = (() => {
        try {
            return localStorage.getItem('orchestrator_debug_mode') === 'true';
        } catch {
            return false;
        }
    })();

    const closeResumeSettingsModal = () => {
        setResumeSettingsJob(null);
        setResumeSettingsError(null);
        setStructureReorchestrateSettings(null);
        setResumeDialogMode('resume');
    };

    const { data: jobsData, isLoading: jobsLoading } = useQuery({
        queryKey: ['jobs'],
        queryFn: () => fetchJobs(),
        refetchInterval: 3000,
        refetchIntervalInBackground: false,
        refetchOnWindowFocus: false,
    });

    const cancelMutation = useMutation({
        mutationFn: (jobId: string) => cancelJob(jobId),
        onSuccess: () => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
        },
    });

    const handleCancel = (jobId: string, jobName: string) => {
        if (confirm(`Cancel job "${jobName}"?`)) {
            cancelMutation.mutate(jobId);
        }
    };

    const resubmitMutation = useMutation({
        mutationFn: (jobId: string) => resubmitJob(jobId),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            alert(`Job resubmitted! New job: ${response.data.new_job_name}`);
        },
        onError: (error: UntypedApiValue) => {
            alert(`Resubmit failed: ${error.response?.data?.detail || error.message}`);
        }
    });

    const handleResubmit = (jobId: string, jobName: string) => {
        if (confirm(`Resubmit job "${jobName}"?`)) {
            resubmitMutation.mutate(jobId);
        }
    };

    const handleViewLogs = async (jobId: string) => {
        setLogsLoading(true);
        setLogsModalJobId(jobId);
        try {
            const response = await fetchJobLogs(jobId);
            setLogsData(response.data);
        } catch (error) {
            console.error('Failed to fetch logs:', error);
            setLogsData(null);
        } finally {
            setLogsLoading(false);
        }
    };

    const resumeMutation = useMutation({
        mutationFn: ({
            jobId,
            fromStage,
            paramOverrides,
            nameSuffix,
        }: {
            jobId: string;
            fromStage?: string;
            paramOverrides?: Record<string, unknown>;
            nameSuffix?: string;
        }) => resumeJob(jobId, fromStage, paramOverrides, nameSuffix),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            const successPrefix = resumeDialogMode === 'reorchestrate' ? 'Job re-orchestrated!' : 'Job resumed!';
            closeResumeSettingsModal();
            const note = response.data.resume_stage_note ? `\nNote: ${response.data.resume_stage_note}` : '';
            alert(`${successPrefix} New job: ${response.data.new_job_name}\nResuming from: ${response.data.resume_from_stage}${note}`);
        },
        onError: (error: UntypedApiValue) => {
            const failurePrefix = resumeDialogMode === 'reorchestrate' ? 'Re-orchestrate failed' : 'Resume failed';
            alert(`${failurePrefix}: ${error.response?.data?.detail || error.message}`);
        }
    });

    const handleResume = (job: Job) => {
        if (job.status === 'awaiting_input' && job.awaiting_payload?.resume_direct) {
            setResumeDialogMode('resume');
            resumeMutation.mutate({ jobId: job.id });
            return;
        }
        if (job.status === 'awaiting_input') {
            handleResumeWithSettings(job);
            return;
        }
        const completed = job.completed_stages || [];
        const resumePoint = completed.length > 0 ? `after ${completed[completed.length - 1]}` : 'from start (using cache)';

        if (confirm(`Resume job "${job.name}" ${resumePoint}?`)) {
            setResumeDialogMode('resume');
            resumeMutation.mutate({ jobId: job.id });
        }
    };

    const handleResumeWithSettings = (job: Job) => {
        const p = job.params || {};
        const structureRetryJob = isStructureReorchestrateJob(job);
        setResumeDialogMode(structureRetryJob ? 'reorchestrate' : 'resume');
        setResumeSettingsJob(job);
        setResumeSettingsFromStage(mapAwaitingStageToResumeStage(job.awaiting_stage));
        setResumeSettingsNameSuffix(job.status === 'awaiting_input' ? 'continued' : structureRetryJob ? 'reorchestrated' : 'retuned');
        setStructureReorchestrateSettings(structureRetryJob ? deriveStructureReorchestrateSettings(job) : null);
        setResumeSettingsForm({
            rfantibodyNumDesigns: clamp(Math.round(toNumber(p.rfantibody_num_designs, DEFAULT_RESUME_SETTINGS_FORM.rfantibodyNumDesigns)), 1, 64),
            seqsPerDesign: clamp(Math.round(toNumber(p.seqs_per_design, DEFAULT_RESUME_SETTINGS_FORM.seqsPerDesign)), 1, 32),
            rfantibodyDiffusionSteps: clamp(Math.round(toNumber(p.rfantibody_diffusion_steps, DEFAULT_RESUME_SETTINGS_FORM.rfantibodyDiffusionSteps)), 20, 200),
            rfantibodyGuideScale: clamp(Math.round(toNumber(p.rfantibody_guide_scale, DEFAULT_RESUME_SETTINGS_FORM.rfantibodyGuideScale)), 1, 50),
            fampnnMaxPsce: clamp(toNumber(p.fampnn_max_psce, DEFAULT_RESUME_SETTINGS_FORM.fampnnMaxPsce), 0.1, 8),
            fampnnMaxResiduePsce: clamp(toNumber(p.fampnn_max_residue_psce, DEFAULT_RESUME_SETTINGS_FORM.fampnnMaxResiduePsce), 0.1, 12),
            boltzMaxBinderRmsd: clamp(toNumber(p.boltz_max_binder_rmsd, DEFAULT_RESUME_SETTINGS_FORM.boltzMaxBinderRmsd), 0.1, 6),
            boltzMinPtmInterface: clamp(toNumber(p.boltz_min_ptm_interface, DEFAULT_RESUME_SETTINGS_FORM.boltzMinPtmInterface), 0, 1),
            runMaturation: !!p.run_maturation,
            runThermoMPNN: !!(p.run_thermompnn ?? p.run_stability_scoring),
            runAnarciiPost: !!p.run_anarcii_post,
        });
        setResumeSettingsError(null);
    };

    const applyResumePreset = (preset: 'more_designs' | 'relax_filter' | 'strict_filter') => {
        setResumeSettingsForm((prev) => {
            if (preset === 'more_designs') {
                return {
                    ...prev,
                    rfantibodyNumDesigns: clamp(prev.rfantibodyNumDesigns + 4, 1, 64),
                    seqsPerDesign: clamp(prev.seqsPerDesign + 2, 1, 32),
                };
            }
            if (preset === 'relax_filter') {
                return {
                    ...prev,
                    fampnnMaxPsce: 3.0,
                    fampnnMaxResiduePsce: 5.0,
                    boltzMaxBinderRmsd: 2.5,
                };
            }
            return {
                ...prev,
                fampnnMaxPsce: 1.5,
                fampnnMaxResiduePsce: 3.0,
                boltzMaxBinderRmsd: 1.5,
            };
        });
        setResumeSettingsError(null);
    };

    const setResumeNumberField = (field: ResumeNumericField, value: number) => {
        setResumeSettingsForm((prev) => ({ ...prev, [field]: value }));
    };

    const submitResumeWithSettings = () => {
        if (!resumeSettingsJob) return;
        setResumeSettingsError(null);

        let parsedOverrides: Record<string, unknown> = {};
        if (isStructureReorchestrateJob(resumeSettingsJob)) {
            if (!structureReorchestrateSettings) {
                setResumeSettingsError('Structure retry settings are missing. Close and reopen the re-orchestrate dialog.');
                return;
            }
            parsedOverrides = buildStructureReorchestrateOverrides(resumeSettingsJob, structureReorchestrateSettings);
        } else {
            const p = resumeSettingsJob.params || {};
            const maybeSetNumber = (key: string, nextValue: number) => {
                const prevRaw = p[key];
                const prevValue = Number(prevRaw);
                if (prevRaw === undefined || !Number.isFinite(prevValue) || Math.abs(prevValue - nextValue) > 1e-9) {
                    parsedOverrides[key] = nextValue;
                }
            };
            const maybeSetBool = (key: string, nextValue: boolean) => {
                const prevRaw = p[key];
                if (prevRaw === undefined || toBoolean(prevRaw) !== nextValue) {
                    parsedOverrides[key] = nextValue;
                }
            };

            maybeSetNumber('rfantibody_num_designs', Math.round(resumeSettingsForm.rfantibodyNumDesigns));
            maybeSetNumber('seqs_per_design', Math.round(resumeSettingsForm.seqsPerDesign));
            maybeSetNumber('rfantibody_diffusion_steps', Math.round(resumeSettingsForm.rfantibodyDiffusionSteps));
            maybeSetNumber('rfantibody_guide_scale', Math.round(resumeSettingsForm.rfantibodyGuideScale));
            maybeSetNumber('fampnn_max_psce', Number(resumeSettingsForm.fampnnMaxPsce.toFixed(2)));
            maybeSetNumber('fampnn_max_residue_psce', Number(resumeSettingsForm.fampnnMaxResiduePsce.toFixed(2)));
            maybeSetNumber('boltz_max_binder_rmsd', Number(resumeSettingsForm.boltzMaxBinderRmsd.toFixed(2)));
            maybeSetNumber('boltz_min_ptm_interface', Number(resumeSettingsForm.boltzMinPtmInterface.toFixed(2)));
            maybeSetBool('run_maturation', resumeSettingsForm.runMaturation);
            maybeSetBool('run_thermompnn', resumeSettingsForm.runThermoMPNN);
            maybeSetBool('run_stability_scoring', resumeSettingsForm.runThermoMPNN);
            maybeSetBool('run_anarcii_post', resumeSettingsForm.runAnarciiPost);
        }

        const effectiveStage = resumeSettingsFromStage === 'auto' ? undefined : resumeSettingsFromStage;
        const effectiveSuffix = resumeSettingsNameSuffix.trim() || undefined;

        resumeMutation.mutate({
            jobId: resumeSettingsJob.id,
            fromStage: effectiveStage,
            paramOverrides: parsedOverrides,
            nameSuffix: effectiveSuffix,
        });
    };

    // DEBUG: Permanent delete mutation
    const deleteMutation = useMutation({
        mutationFn: (jobId: string) => deleteJobPermanently(jobId),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            alert(`Deleted! ${response.data.children_deleted} children, ${response.data.directories_deleted.length} directories removed`);
        },
        onError: (error: UntypedApiValue) => {
            alert(`Delete failed: ${error.response?.data?.detail || error.message}`);
        }
    });

    const handleDelete = (jobId: string) => {
        // Confirmation is already done in the button onClick
        deleteMutation.mutate(jobId);
    };

    const navigate = useNavigate();

    const handleClone = (job: Job) => {
        // Store job params in localStorage for the submit form to pick up
        const cloneData = {
            name: `${job.name}_clone`,
            model_id: job.model_id,
            mode: job.mode,
            params: job.params || {}
        };
        localStorage.setItem('clonedJobData', JSON.stringify(cloneData));
        // Navigate to submit page
        navigate('/submit');
    };

    // Force-run mutation (debug feature)
    const forceRunMutation = useMutation({
        mutationFn: (jobId: string) => forceRunJob(jobId),
        onSuccess: (response) => {
            queryClient.invalidateQueries({ queryKey: ['jobs'] });
            alert(`Force-launched on GPU ${response.data.gpu_id}`);
        },
        onError: (error: UntypedApiValue) => {
            alert(`Force-run failed: ${error.response?.data?.detail || error.message}`);
        }
    });

    const handleForceRun = (jobId: string) => {
        forceRunMutation.mutate(jobId);
    };

    const isStructureReorchestrateModal = !!resumeSettingsJob && isStructureReorchestrateJob(resumeSettingsJob);

    return (
        <div className="min-h-screen bg-slate-950 px-6 pt-3 pb-6">
            {/* System Overview & GPU Status */}
            <section className="mb-6">
                <DashboardTelemetry />
            </section>

            <section className="relative mb-8">
                <div className="pointer-events-none absolute inset-x-3 bottom-0 top-4 rounded-[2rem] border border-slate-800/80 bg-slate-900/70 shadow-[0_30px_90px_rgba(2,6,23,0.45)]" />
                <div className="relative rounded-[2rem] border border-[var(--border-primary)] bg-[var(--bg-secondary)]/74 p-3 shadow-2xl shadow-black/10 md:p-4">
                    <div className="grid gap-6 xl:grid-cols-2 xl:items-start">
                        <div className="order-2 xl:order-1">
                            <QuickViewer
                                selectedJobId={quickViewJobId}
                                onJobChange={setQuickViewJobId}
                            />
                        </div>
                        <div className="order-1 space-y-4 xl:order-2">
                            <GpuSchedulerControls className="m-0" />
                            <JobQueuePanel className="m-0" />
                        </div>
                    </div>
                </div>
            </section>

            {/* Logs Modal - Full screen popup */}
            {logsModalJobId && (
                <LogsModal
                    logs={logsData}
                    loading={logsLoading}
                    onClose={() => {
                        setLogsModalJobId(null);
                        setLogsData(null);
                    }}
                />
            )}

            {resumeSettingsJob && (
                <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
                    <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-3xl max-h-[85vh] flex flex-col">
                        <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
                            <div>
                                <h2 className="text-xl font-semibold text-slate-100">
                                    {isStructureReorchestrateModal ? 'Re-orchestrate Job' : 'Resume With Settings'}
                                </h2>
                                <p className="text-sm text-slate-400 mt-1">
                                    {resumeSettingsJob.name}
                                </p>
                            </div>
                            <button
                                onClick={closeResumeSettingsModal}
                                className="text-slate-400 hover:text-slate-200 text-2xl font-light transition-colors"
                                disabled={resumeMutation.isPending}
                            >
                                ✕
                            </button>
                        </div>

                        <div className="p-6 overflow-auto space-y-4">
                            <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                                <label className="text-sm text-slate-300">
                                    {isStructureReorchestrateModal ? 'Re-orchestrate From' : 'Resume Stage Hint'}
                                    <select
                                        value={resumeSettingsFromStage}
                                        onChange={(e) => setResumeSettingsFromStage(e.target.value)}
                                        className="mt-1 w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-slate-100"
                                        disabled={resumeMutation.isPending}
                                    >
                                        <option value="auto">auto</option>
                                        {(resumeSettingsJob.all_stages || []).map((stage) => (
                                            <option key={stage} value={stage}>{formatResumeStageLabel(stage)}</option>
                                        ))}
                                    </select>
                                    {resumeSettingsJob.awaiting_stage && (
                                        <p className="mt-1 text-xs text-slate-500">
                                            Currently paused at {formatResumeStageLabel(resumeSettingsJob.awaiting_stage)}.
                                        </p>
                                    )}
                                    <p className="mt-1 text-xs text-slate-500">
                                        {isStructureReorchestrateModal
                                            ? 'Cache-based re-orchestration still reuses matching tasks; this hint does not strictly force a stage restart yet.'
                                            : 'Cache-based resume reuses matching tasks; this hint does not strictly force stage restart yet.'}
                                    </p>
                                </label>
                                <label className="text-sm text-slate-300">
                                    New Job Name Suffix
                                    <input
                                        type="text"
                                        value={resumeSettingsNameSuffix}
                                        onChange={(e) => setResumeSettingsNameSuffix(e.target.value)}
                                        placeholder={isStructureReorchestrateModal ? 'reorchestrated' : 'retuned'}
                                        className="mt-1 w-full bg-slate-800 border border-slate-600 rounded px-3 py-2 text-slate-100"
                                        disabled={resumeMutation.isPending}
                                    />
                                </label>
                            </div>

                            {isStructureReorchestrateModal && structureReorchestrateSettings && (
                                <>
                                    <StructureReorchestratePanel
                                        settings={structureReorchestrateSettings}
                                        onChange={setStructureReorchestrateSettings}
                                        disabled={resumeMutation.isPending}
                                    />
                                    {resumeSettingsError && (
                                        <p className="text-sm text-red-400">{resumeSettingsError}</p>
                                    )}
                                </>
                            )}

                            {!isStructureReorchestrateModal && (
                                <div>
                                <div className="flex items-center justify-between mb-3">
                                    <p className="text-sm text-slate-300">Tuning Controls</p>
                                    <div className="flex items-center gap-2">
                                        <button
                                            onClick={() => applyResumePreset('more_designs')}
                                            className="px-2 py-1 text-xs bg-blue-500/20 text-blue-300 hover:bg-blue-500/30 rounded"
                                            disabled={resumeMutation.isPending}
                                        >
                                            More Designs
                                        </button>
                                        <button
                                            onClick={() => applyResumePreset('relax_filter')}
                                            className="px-2 py-1 text-xs bg-amber-500/20 text-amber-300 hover:bg-amber-500/30 rounded"
                                            disabled={resumeMutation.isPending}
                                        >
                                            Relax Filter
                                        </button>
                                        <button
                                            onClick={() => applyResumePreset('strict_filter')}
                                            className="px-2 py-1 text-xs bg-violet-500/20 text-violet-300 hover:bg-violet-500/30 rounded"
                                            disabled={resumeMutation.isPending}
                                        >
                                            Tighten Filter
                                        </button>
                                    </div>
                                </div>
                                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>RFantibody Designs</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.rfantibodyNumDesigns}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={1}
                                                max={64}
                                                step={1}
                                                value={resumeSettingsForm.rfantibodyNumDesigns}
                                                onChange={(e) => setResumeNumberField('rfantibodyNumDesigns', clamp(Math.round(toNumber(e.target.value, 10)), 1, 64))}
                                                className="w-full accent-cyan-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={1}
                                                max={64}
                                                value={resumeSettingsForm.rfantibodyNumDesigns}
                                                onChange={(e) => setResumeNumberField('rfantibodyNumDesigns', clamp(Math.round(toNumber(e.target.value, 10)), 1, 64))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>Sequences per Design</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.seqsPerDesign}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={1}
                                                max={32}
                                                step={1}
                                                value={resumeSettingsForm.seqsPerDesign}
                                                onChange={(e) => setResumeNumberField('seqsPerDesign', clamp(Math.round(toNumber(e.target.value, 8)), 1, 32))}
                                                className="w-full accent-cyan-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={1}
                                                max={32}
                                                value={resumeSettingsForm.seqsPerDesign}
                                                onChange={(e) => setResumeNumberField('seqsPerDesign', clamp(Math.round(toNumber(e.target.value, 8)), 1, 32))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>Diffusion Steps</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.rfantibodyDiffusionSteps}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={20}
                                                max={200}
                                                step={1}
                                                value={resumeSettingsForm.rfantibodyDiffusionSteps}
                                                onChange={(e) => setResumeNumberField('rfantibodyDiffusionSteps', clamp(Math.round(toNumber(e.target.value, 50)), 20, 200))}
                                                className="w-full accent-blue-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={20}
                                                max={200}
                                                value={resumeSettingsForm.rfantibodyDiffusionSteps}
                                                onChange={(e) => setResumeNumberField('rfantibodyDiffusionSteps', clamp(Math.round(toNumber(e.target.value, 50)), 20, 200))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>Guide Scale</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.rfantibodyGuideScale}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={1}
                                                max={20}
                                                step={1}
                                                value={resumeSettingsForm.rfantibodyGuideScale}
                                                onChange={(e) => setResumeNumberField('rfantibodyGuideScale', clamp(Math.round(toNumber(e.target.value, 10)), 1, 50))}
                                                className="w-full accent-blue-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={1}
                                                max={20}
                                                value={resumeSettingsForm.rfantibodyGuideScale}
                                                onChange={(e) => setResumeNumberField('rfantibodyGuideScale', clamp(Math.round(toNumber(e.target.value, 10)), 1, 50))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>FAMPNN Max Avg PSCE</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.fampnnMaxPsce.toFixed(2)}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={0.1}
                                                max={8}
                                                step={0.1}
                                                value={resumeSettingsForm.fampnnMaxPsce}
                                                onChange={(e) => setResumeNumberField('fampnnMaxPsce', clamp(toNumber(e.target.value, 2), 0.1, 8))}
                                                className="w-full accent-amber-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={0.1}
                                                max={8}
                                                step={0.1}
                                                value={resumeSettingsForm.fampnnMaxPsce}
                                                onChange={(e) => setResumeNumberField('fampnnMaxPsce', clamp(toNumber(e.target.value, 2), 0.1, 8))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>FAMPNN Max Residue PSCE</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.fampnnMaxResiduePsce.toFixed(2)}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={0.1}
                                                max={12}
                                                step={0.1}
                                                value={resumeSettingsForm.fampnnMaxResiduePsce}
                                                onChange={(e) => setResumeNumberField('fampnnMaxResiduePsce', clamp(toNumber(e.target.value, 4), 0.1, 12))}
                                                className="w-full accent-amber-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={0.1}
                                                max={12}
                                                step={0.1}
                                                value={resumeSettingsForm.fampnnMaxResiduePsce}
                                                onChange={(e) => setResumeNumberField('fampnnMaxResiduePsce', clamp(toNumber(e.target.value, 4), 0.1, 12))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>Boltz Max Binder RMSD</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.boltzMaxBinderRmsd.toFixed(2)}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={0.1}
                                                max={6}
                                                step={0.1}
                                                value={resumeSettingsForm.boltzMaxBinderRmsd}
                                                onChange={(e) => setResumeNumberField('boltzMaxBinderRmsd', clamp(toNumber(e.target.value, 2), 0.1, 6))}
                                                className="w-full accent-emerald-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={0.1}
                                                max={6}
                                                step={0.1}
                                                value={resumeSettingsForm.boltzMaxBinderRmsd}
                                                onChange={(e) => setResumeNumberField('boltzMaxBinderRmsd', clamp(toNumber(e.target.value, 2), 0.1, 6))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                    <div className="bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                        <div className="flex items-center justify-between text-sm text-slate-300 mb-2">
                                            <span>Boltz Min pTM Interface</span>
                                            <span className="font-mono text-slate-400">{resumeSettingsForm.boltzMinPtmInterface.toFixed(2)}</span>
                                        </div>
                                        <div className="flex items-center gap-3">
                                            <input
                                                type="range"
                                                min={0}
                                                max={1}
                                                step={0.01}
                                                value={resumeSettingsForm.boltzMinPtmInterface}
                                                onChange={(e) => setResumeNumberField('boltzMinPtmInterface', clamp(toNumber(e.target.value, 0.5), 0, 1))}
                                                className="w-full accent-emerald-400"
                                                disabled={resumeMutation.isPending}
                                            />
                                            <input
                                                type="number"
                                                min={0}
                                                max={1}
                                                step={0.01}
                                                value={resumeSettingsForm.boltzMinPtmInterface}
                                                onChange={(e) => setResumeNumberField('boltzMinPtmInterface', clamp(toNumber(e.target.value, 0.5), 0, 1))}
                                                className="w-20 bg-slate-900 border border-slate-600 rounded px-2 py-1 text-slate-100 text-sm"
                                                disabled={resumeMutation.isPending}
                                            />
                                        </div>
                                    </div>
                                </div>
                                <div className="mt-3 bg-slate-800/40 border border-slate-700 rounded-lg p-3">
                                    <p className="text-sm text-slate-300 mb-2">Optional Stages</p>
                                    <div className="grid grid-cols-1 md:grid-cols-3 gap-2 text-sm text-slate-300">
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={resumeSettingsForm.runMaturation}
                                                onChange={(e) => setResumeSettingsForm((prev) => ({ ...prev, runMaturation: e.target.checked }))}
                                                className="rounded bg-slate-900 border-slate-600"
                                                disabled={resumeMutation.isPending}
                                            />
                                            PPIFlow maturation
                                        </label>
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={resumeSettingsForm.runThermoMPNN}
                                                onChange={(e) => setResumeSettingsForm((prev) => ({ ...prev, runThermoMPNN: e.target.checked }))}
                                                className="rounded bg-slate-900 border-slate-600"
                                                disabled={resumeMutation.isPending}
                                            />
                                            ThermoMPNN scoring
                                        </label>
                                        <label className="flex items-center gap-2">
                                            <input
                                                type="checkbox"
                                                checked={resumeSettingsForm.runAnarciiPost}
                                                onChange={(e) => setResumeSettingsForm((prev) => ({ ...prev, runAnarciiPost: e.target.checked }))}
                                                className="rounded bg-slate-900 border-slate-600"
                                                disabled={resumeMutation.isPending}
                                            />
                                            ANARCII post-annotation
                                        </label>
                                    </div>
                                </div>
                                {resumeSettingsError && (
                                    <p className="mt-2 text-sm text-red-400">{resumeSettingsError}</p>
                                )}
                                </div>
                            )}
                        </div>

                        <div className="flex justify-end gap-2 px-6 py-4 border-t border-slate-700">
                            <button
                                onClick={closeResumeSettingsModal}
                                className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg transition-colors"
                                disabled={resumeMutation.isPending}
                            >
                                Cancel
                            </button>
                            <button
                                onClick={submitResumeWithSettings}
                                className="px-4 py-2 bg-emerald-600 hover:bg-emerald-500 text-white rounded-lg transition-colors disabled:opacity-50"
                                disabled={resumeMutation.isPending}
                            >
                                {resumeMutation.isPending
                                    ? (isStructureReorchestrateModal ? 'Re-orchestrating...' : 'Resuming...')
                                    : (isStructureReorchestrateModal ? 'Re-orchestrate Job' : 'Resume Job')}
                            </button>
                        </div>
                    </div>
                </div>
            )}

            {/* Jobs Section */}
            <section>
                <div className="flex items-center justify-between mb-4">
                    <h2 className="text-xl font-semibold text-slate-200">Recent Jobs</h2>
                    <span className="text-sm text-slate-400">
                        {jobsData?.data.total ?? 0} total jobs
                    </span>
                </div>

                <JobFilters
                    search={search}
                    onSearchChange={setSearch}
                    status={statusFilter}
                    onStatusChange={setStatusFilter}
                    showNgsJobs={showNgsJobs}
                    onShowNgsJobsChange={setShowNgsJobs}
                />

                {(() => {
                    const filteredJobs = (jobsData?.data.jobs || []).filter((job: Job) => {
                        const matchesSearch = search === '' ||
                            job.name.toLowerCase().includes(search.toLowerCase()) ||
                            job.id.includes(search);
                        const matchesStatus = statusFilter === 'all' ||
                            job.status === statusFilter ||
                            (statusFilter === 'awaiting_input' && !!job.awaiting_input);
                        const matchesNgs = showNgsJobs || !isNgsJob(job);
                        return matchesSearch && matchesStatus && matchesNgs;
                    });

                    return (
                        <div className="space-y-3">
                            <JobQueueTable
                                jobs={filteredJobs}
                                loading={jobsLoading}
                                onCancel={handleCancel}
                                onResubmit={handleResubmit}
                                onResume={handleResume}
                                onResumeWithSettings={handleResumeWithSettings}
                                onViewLogs={handleViewLogs}
                                onViewQuick={setQuickViewJobId}
                                onClone={handleClone}
                                onDelete={handleDelete}
                                onForceRun={handleForceRun}
                                quickViewJobId={quickViewJobId}
                                debugMode={debugMode}
                            />
                            <div className="flex items-center justify-between px-2 text-sm text-slate-400">
                                <span>
                                    Showing {filteredJobs.length} filtered jobs
                                </span>
                                <span>
                                    Scroll inside the jobs panel to browse the full queue
                                </span>
                            </div>
                        </div>
                    );
                })()}
            </section>
        </div >
    );
}




function LogsModal({
    logs,
    loading,
    onClose
}: {
    logs: JobLogs | null;
    loading: boolean;
    onClose: () => void;
}) {
    const [activeTab, setActiveTab] = useState<'parsed' | 'command' | 'stderr' | 'nextflow'>('parsed');

    return (
        <div className="fixed inset-0 z-50 flex items-center justify-center p-4 bg-black/70 backdrop-blur-sm">
            <div className="bg-slate-900 border border-slate-700 rounded-xl shadow-2xl w-full max-w-4xl max-h-[80vh] flex flex-col">
                {/* Header */}
                <div className="flex items-center justify-between px-6 py-4 border-b border-slate-700">
                    <div>
                        <h2 className="text-xl font-semibold text-slate-100">Job Logs</h2>
                        {logs && (
                            <p className="text-sm text-slate-400 mt-1">
                                {logs.job_name} • Exit code: {logs.exit_code ?? 'N/A'}
                            </p>
                        )}
                    </div>
                    <button
                        onClick={onClose}
                        className="text-slate-400 hover:text-slate-200 text-2xl font-light transition-colors"
                    >
                        ✕
                    </button>
                </div>

                {/* Tabs */}
                <div className="flex border-b border-slate-700 px-4">
                    {[
                        { id: 'parsed' as const, label: 'Parsed Error' },
                        { id: 'command' as const, label: 'Command Log' },
                        { id: 'stderr' as const, label: 'Stderr' },
                        { id: 'nextflow' as const, label: 'Nextflow Log' },
                    ].map(tab => (
                        <button
                            key={tab.id}
                            onClick={() => setActiveTab(tab.id)}
                            className={`px-4 py-3 text-sm font-medium transition-colors ${activeTab === tab.id
                                ? 'text-blue-400 border-b-2 border-blue-400 -mb-px'
                                : 'text-slate-400 hover:text-slate-200'
                                }`}
                        >
                            {tab.label}
                        </button>
                    ))}
                </div>

                {/* Content */}
                <div className="flex-1 overflow-auto p-4 min-h-[300px]">
                    {loading ? (
                        <div className="flex items-center justify-center h-full text-slate-400">
                            <span className="animate-spin mr-2">⟳</span> Loading logs...
                        </div>
                    ) : !logs ? (
                        <div className="flex items-center justify-center h-full text-slate-400">
                            Failed to load logs
                        </div>
                    ) : (
                        <pre className="text-sm text-slate-300 font-mono whitespace-pre-wrap break-words">
                            {activeTab === 'parsed' && (
                                logs.parsed_error || <span className="text-slate-500 italic">No specific error extracted</span>
                            )}
                            {activeTab === 'command' && (
                                logs.command_log || <span className="text-slate-500 italic">No command log available</span>
                            )}
                            {activeTab === 'stderr' && (
                                logs.command_err || <span className="text-slate-500 italic">No stderr output</span>
                            )}
                            {activeTab === 'nextflow' && (
                                logs.nextflow_log || <span className="text-slate-500 italic">No Nextflow log available</span>
                            )}
                        </pre>
                    )}
                </div>

                {/* Footer */}
                <div className="flex justify-end px-6 py-4 border-t border-slate-700">
                    <button
                        onClick={onClose}
                        className="px-4 py-2 bg-slate-700 hover:bg-slate-600 text-slate-200 rounded-lg transition-colors"
                    >
                        Close
                    </button>
                </div>
            </div>
        </div>
    );
}
