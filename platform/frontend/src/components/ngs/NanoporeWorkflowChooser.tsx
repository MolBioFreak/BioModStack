export type WorkflowKey =
    | 'clone'
    | 'plasmidQc'
    | 'constructScreening'
    | 'fastqQc'
    | 'bamQc'
    | 'dna'
    | 'rna'
    | 'duplex'
    | 'modified'
    | 'barcode'
    | 'pooledAssignment';

type WorkflowBadge = 'VENDOR REPORT' | 'REVIEW ONLY';

type WorkflowChoice = {
    key: WorkflowKey;
    title: string;
    input: string;
    result: string;
    badge?: WorkflowBadge;
};

const WORKFLOW_PANEL = 'rounded-2xl border border-[var(--border-primary)] bg-[color-mix(in_srgb,var(--bg-secondary)_75%,#000)] p-5 shadow-[0_18px_45px_rgba(0,0,0,0.32)]';

const WORKFLOW_CHOICES: readonly WorkflowChoice[] = [
    {
        key: 'clone',
        title: 'Validate a known plasmid / clone',
        input: 'POD5, BAM, or FASTQ + saved MolBio revision',
        result: 'Vendor wf-clone-validation HTML report, assembly, construct evidence',
        badge: 'VENDOR REPORT',
    },
    {
        key: 'plasmidQc',
        title: 'QC plasmid reads',
        input: 'POD5, BAM, or FASTQ + saved MolBio revision',
        result: 'BMS plasmid QC, alignment and generic multimer evidence',
    },
    {
        key: 'constructScreening',
        title: 'Screen a construct',
        input: 'POD5, BAM, or FASTQ + saved MolBio revision',
        result: 'BMS construct-screening evidence; not a competing clone report',
    },
    {
        key: 'fastqQc',
        title: 'ONT FASTQ QC',
        input: 'FASTQ + saved MolBio revision',
        result: 'Read and alignment QC only; no clone report',
    },
    {
        key: 'bamQc',
        title: 'Analyze aligned plasmid BAM',
        input: 'BAM + saved MolBio revision',
        result: 'BMS plasmid QC and alignment evidence',
    },
    {
        key: 'dna',
        title: 'Basecall DNA simplex',
        input: 'DNA POD5',
        result: 'Dorado calls BAM, summary and runtime provenance',
    },
    {
        key: 'rna',
        title: 'Basecall RNA',
        input: 'RNA POD5',
        result: 'RNA004 simplex calls BAM, trimming and provenance',
    },
    {
        key: 'duplex',
        title: 'Basecall DNA duplex',
        input: 'DNA POD5 + validated read-pairs file',
        result: 'Duplex BAM with retained stereo model provenance',
    },
    {
        key: 'modified',
        title: 'Call modified bases',
        input: 'DNA POD5',
        result: 'HAC simplex calls plus modkit tables',
    },
    {
        key: 'barcode',
        title: 'Classify and demultiplex RBK114',
        input: 'DNA POD5',
        result: 'Canonical barcodeNN units and demux outputs',
    },
    {
        key: 'pooledAssignment',
        title: 'Assign pooled FASTQ references',
        input: 'FASTQ + 2-96 exact saved revisions',
        result: 'Review-only assignment; explicit target release gates consensus',
        badge: 'REVIEW ONLY',
    },
];

const neutralCard = 'border-[var(--border-primary)] bg-[color-mix(in_srgb,var(--bg-secondary)_75%,#000)]';
const selectedCard = 'border-[var(--accent-primary)] bg-[color-mix(in_srgb,var(--accent-primary)_12%,transparent)] ring-1 ring-[var(--accent-primary)]';

type NanoporeWorkflowChooserProps = {
    selectedWorkflow: WorkflowKey;
    onSelect: (workflow: WorkflowKey) => void;
};

export function NanoporeWorkflowChooser({
    selectedWorkflow,
    onSelect,
}: NanoporeWorkflowChooserProps) {
    return (
        <section
            className={`${WORKFLOW_PANEL} space-y-3`}
            aria-label="Choose an NGS workflow"
        >
            <div>
                <h2 className="text-base font-semibold text-[var(--text-primary)]">Choose what you want to do</h2>
                <p className="text-sm text-[var(--text-secondary)] mt-1">
                    Choose one path first. The form below then exposes only its compatible inputs and locked runtime settings.
                </p>
            </div>
            <div className="grid grid-cols-1 md:grid-cols-2 xl:grid-cols-6 gap-3">
                {WORKFLOW_CHOICES.map((workflow) => {
                    const selected = selectedWorkflow === workflow.key;
                    return (
                        <button
                            key={workflow.key}
                            type="button"
                            data-ngs-workflow-key={workflow.key}
                            onClick={() => onSelect(workflow.key)}
                            aria-pressed={selected}
                            className={`relative min-h-[104px] rounded-xl border p-3 text-left transition-all motion-reduce:transform-none motion-reduce:transition-none focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--accent-primary)] hover:-translate-y-0.5 motion-reduce:hover:translate-y-0 hover:border-[var(--border-secondary)] hover:bg-[var(--bg-tertiary)] ${selected ? selectedCard : neutralCard}`}
                            style={selected ? { boxShadow: '0 0 24px color-mix(in srgb, var(--accent-primary) 24%, transparent)' } : undefined}
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
            <div className="rounded border border-[var(--border-primary)] bg-[var(--bg-tertiary)]/50 p-3 text-xs text-[var(--text-secondary)]">
                <strong className="text-[var(--text-primary)]">How to use this page:</strong> choose a workflow → choose the requested POD5, BAM, or FASTQ input → select a saved MolBio sequence and exact revision when required → enter a job name → submit. Completed jobs appear through <strong className="text-[var(--text-primary)]">Runs</strong>; no completed sequencing results are shown until an input is actually processed. Selecting a workflow never starts an instrument run.
            </div>
        </section>
    );
}
