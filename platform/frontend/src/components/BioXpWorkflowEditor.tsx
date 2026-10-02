import { BioXpWellPipettingPanel } from './BioXpWellPipettingPanel';

/** Saved authoring in the robot workspace; never submits physical commands. */
export function BioXpWorkflowEditor() {
    return <section className="w-full space-y-4" aria-label="BioXP workflows workspace">
        <header>
            <h2 className="text-xl font-semibold">Workflows</h2>
            <p className="mt-1 text-sm text-slate-400">Build and save a sequence of robot steps. You can edit without a robot connection.</p>
            <p className="mt-1 text-sm text-slate-400">Name the workflow, choose a step type, fill in its settings, then append it to the list. Save workflow stores the draft; it does not run the robot.</p>
        </header>
        <BioXpWellPipettingPanel workflowAuthoring generation={0} connected={false} />
    </section>;
}
