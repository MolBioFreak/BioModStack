import type { WorkflowKey } from '../../lib/nanoporeWorkflow';
export type { WorkflowKey } from '../../lib/nanoporeWorkflow';

type WorkflowBadge = 'REVIEW ONLY';

type WorkflowChoice = {
    key: WorkflowKey;
    title: string;
    input: string;
    result: string;
    badge?: WorkflowBadge;
};

const WORKFLOW_PANEL = 'rounded-2xl border border-[var(--border-primary)] bg-[var(--bg-secondary)] p-5 shadow-[var(--shadow-card)]';

const PLASMID_METHODS = [
    { key: 'clone', title: 'Assembly and validation report', description: 'EPI2ME wf-clone-validation: de novo assembly, validation report and construct check. Optional methylation tables from POD5 or tagged BAM.' },
    { key: 'plasmidQc', title: 'Plasmid read QC', description: 'Per-base support, consensus and multimer check against the reference, from POD5, BAM or FASTQ. Optional methylation tables from POD5 or tagged BAM. No assembly.' },
    { key: 'constructScreening', title: 'Construct screening', description: 'Align reads to the expected construct and review screening results. Optional assembly and methylation tables.' },
] as const;

type WorkflowGroup = { label: string; hint: string; choices: readonly WorkflowChoice[] };

const WORKFLOW_GROUPS: readonly WorkflowGroup[] = [
    {
        label: 'General DNA analysis and RNA basecalling',
        hint: 'DNA accepts raw signal or existing reads; RNA starts from POD5.',
        choices: [
            {
                key: 'dna',
                title: 'DNA analysis',
                input: 'DNA POD5, BAM or FASTQ',
                result: 'General DNA reads, optional reference QC and tandem methylation analysis; no plasmid assumption required',
            },
            {
                key: 'rna',
                title: 'Basecall RNA',
                input: 'RNA POD5',
                result: 'RNA004 simplex calls BAM, trimming and provenance',
            },
        ],
    },
    {
        label: 'Analyse reads against a reference',
        hint: 'Needs a reference sequence: paste or upload one, or pick it from this Project or Molecular Tools.',
        choices: [
            {
                key: 'clone',
                title: 'Plasmid analysis',
                input: 'POD5, BLOW5, BAM or FASTQ + reference',
                result: 'Assembly report, plasmid read QC or construct screening (choose below), with optional methylation',
            },
            {
                key: 'fastqQc',
                title: 'FASTQ read QC',
                input: 'FASTQ + reference',
                result: 'Read-length, quality and alignment QC only. No consensus or per-base support; use Plasmid analysis for those',
            },
            {
                key: 'modified',
                title: 'Methylation analysis',
                input: 'DNA POD5, BLOW5 or modification-tagged BAM + reference',
                result: 'Per-site methylation tables and modification summaries',
            },
        ],
    },
    {
        label: 'Review pooled samples',
        hint: 'Assign reads from a pooled FASTQ to candidate sequences.',
        choices: [
            {
                key: 'pooledAssignment',
                title: 'Assign pooled FASTQ references',
                input: 'FASTQ + 2-96 candidate sequences',
                result: 'Assigns reads to candidates for review; consensus runs after you release a target',
                badge: 'REVIEW ONLY',
            },
        ],
    },
];

const neutralCard = 'border-[var(--border-primary)] bg-[var(--bg-primary)]';
const selectedCard = 'border-[var(--accent-primary)] bg-[color-mix(in_srgb,var(--accent-primary)_10%,var(--bg-secondary))] ring-1 ring-[var(--accent-primary)]';

type NanoporeWorkflowChooserProps = {
    selectedWorkflow: WorkflowKey;
    onSelect: (workflow: WorkflowKey) => void;
};

export function NanoporeWorkflowChooser({
    selectedWorkflow,
    onSelect,
}: NanoporeWorkflowChooserProps) {
    const current = selectedWorkflow;
    const plasmidMethod = PLASMID_METHODS.find((method) => method.key === current);
    return (
        <section
            className={`${WORKFLOW_PANEL} space-y-3`}
            aria-label="Choose an NGS workflow"
        >
            <div>
                <h2 className="text-base font-semibold text-[var(--text-primary)]">Choose what you want to do</h2>
                <p className="text-sm text-[var(--text-secondary)] mt-1">
                    Choose a task, then configure its input and options below.
                </p>
            </div>
            {WORKFLOW_GROUPS.map((group) => (
                <div key={group.label} className="space-y-2" role="group" aria-label={group.label}>
                    <div className="flex flex-wrap items-baseline gap-x-2">
                        <h3 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">{group.label}</h3>
                        <span className="text-xs text-[var(--text-secondary)]">{group.hint}</span>
                    </div>
                    <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                        {group.choices.map((workflow) => {
                    const selected = workflow.key === 'clone' ? Boolean(plasmidMethod) : current === workflow.key;
                    return (
                        <button
                            key={workflow.key}
                            type="button"
                            data-ngs-workflow-key={workflow.key}
                            onClick={() => { if (!selected) onSelect(workflow.key); }}
                            aria-pressed={selected}
                            className={`relative min-h-[104px] rounded-xl border p-3 text-left transition-all motion-reduce:transform-none motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] hover:-translate-y-0.5 motion-reduce:hover:translate-y-0 hover:border-[var(--border-secondary)] hover:bg-[var(--bg-tertiary)] ${selected ? selectedCard : neutralCard}`}
                        >
                            {workflow.badge && (
                                <span className="absolute right-2 top-2 rounded-full border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-2 py-0.5 text-[9px] font-bold tracking-wide text-[var(--text-secondary)]">
                                    {workflow.badge}
                                </span>
                            )}
                            <div className={`font-medium text-sm text-[var(--text-primary)] ${workflow.badge ? 'pr-20' : ''}`}>
                                {workflow.title}
                            </div>
                            <div className="text-xs text-[var(--text-secondary)] mt-1"><strong>Provide:</strong> {workflow.input}</div>
                            <div className="text-xs text-[var(--text-secondary)] mt-1"><strong>Get:</strong> {workflow.result}</div>
                        </button>
                    );
                })}
                    </div>
                </div>
            ))}
            {plasmidMethod && <div className="rounded-xl border border-[var(--border-primary)] bg-[var(--bg-primary)] p-3">
                <label className="block text-sm font-medium text-[var(--text-primary)]">
                    Plasmid analysis method
                    <select aria-label="Plasmid analysis method" value={plasmidMethod.key}
                        onChange={(event) => onSelect(event.target.value as WorkflowKey)}
                        className="mt-2 block w-full rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)] px-3 py-2 text-sm text-[var(--text-primary)]">
                        {PLASMID_METHODS.map((method) => <option key={method.key} value={method.key}>{method.title}</option>)}
                    </select>
                </label>
                <p className="mt-2 text-sm text-[var(--text-secondary)]">{plasmidMethod.description}</p>
            </div>}
            <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/50 p-3 text-xs text-[var(--text-secondary)]" data-testid="ngs-how-to-use">
                <strong className="text-[var(--text-primary)]">How to use this page</strong>
                <ol className="mt-1 list-decimal space-y-0.5 pl-5">
                    <li>Choose a task above.</li>
                    <li>Choose your reads: POD5 or BLOW5 raw signal, an existing BAM, or FASTQ.</li>
                    <li>If the task needs a reference sequence, paste or upload it, or pick one already saved in this Project or in Molecular Tools.</li>
                    <li>Optional: pick modified bases and turn on methylation tables (POD5, BLOW5 or tagged BAM only).</li>
                    <li>Name the job and submit.</li>
                </ol>
                <p className="mt-1">Progress and results appear under <strong className="text-[var(--text-primary)]">Runs</strong>. Nothing here starts the sequencer.</p>
            </div>
        </section>
    );
}
