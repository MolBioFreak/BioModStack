import { BioXpWellPipettingPanel } from './BioXpWellPipettingPanel';

/** Saved authoring in the robot workspace; never submits physical commands. */
export function BioXpWorkflowEditor() {
    return <section className="w-full space-y-4" aria-label="BioXP workflows workspace">
        <header>
            <h2 className="text-xl font-semibold">Workflows</h2>
            <p className="mt-1 text-sm text-content-secondary">Plan on the deck, configure each action, and save your sequence. Editing does not send robot commands.</p>
        </header>
        <BioXpWellPipettingPanel workflowAuthoring generation={0} connected={false} />
    </section>;
}
