import { normalizeNanoporeWorkflowChoice, type WorkflowKey } from '../../lib/nanoporeWorkflow';
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
    { key: 'clone', title: 'Assembly and validation report', description: 'Vendor wf-clone-validation report, assembly and construct evidence.' },
    { key: 'plasmidQc', title: 'Plasmid read QC', description: 'BNS read QC, alignment and multimer evidence without the vendor assembly report.' },
    { key: 'constructScreening', title: 'Construct screening', description: 'Compare reads against the saved construct and review screening results.' },
] as const;

const WORKFLOW_CHOICES: readonly WorkflowChoice[] = [
    {
        key: 'clone',
        title: 'Plasmid analysis',
        input: 'POD5, BAM, or FASTQ + saved MolBio revision',
        result: 'Choose an assembly report, read QC, or construct screening below',
    },
    {
        key: 'fastqQc',
        title: 'ONT FASTQ QC',
        input: 'FASTQ + saved MolBio revision',
        result: 'Read and alignment QC only; no clone report',
    },
    {
        key: 'dna',
        title: 'Basecall DNA',
        input: 'DNA POD5',
        result: 'DNA reads, with optional paired strands, barcodes and modification calls',
    },
    {
        key: 'rna',
        title: 'Basecall RNA',
        input: 'RNA POD5',
        result: 'RNA004 simplex calls BAM, trimming and provenance',
    },
    {
        key: 'modified',
        title: 'Methylation analysis',
        input: 'DNA POD5 or modification-tagged BAM + saved reference',
        result: 'Per-site methylation tables and modification summaries',
    },
    {
        key: 'pooledAssignment',
        title: 'Assign pooled FASTQ references',
        input: 'FASTQ + 2-96 exact saved revisions',
        result: 'Review-only assignment; explicit target release gates consensus',
        badge: 'REVIEW ONLY',
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
    const current = normalizeNanoporeWorkflowChoice(selectedWorkflow);
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
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-3 gap-3">
                {WORKFLOW_CHOICES.map((workflow) => {
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
            <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/50 p-3 text-xs text-[var(--text-secondary)]">
                <strong className="text-[var(--text-primary)]">How to use this page:</strong> choose a workflow → choose the requested POD5, BAM, or FASTQ input → select a saved MolBio sequence and exact revision when required → enter a job name → submit. Completed jobs appear through <strong className="text-[var(--text-primary)]">Runs</strong>; no completed sequencing results are shown until an input is actually processed. Selecting a workflow never starts an instrument run.
            </div>
        </section>
    );
}
