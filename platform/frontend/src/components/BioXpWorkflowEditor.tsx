import { BioXpWellPipettingPanel } from './BioXpWellPipettingPanel';
import { BioXpMethodsWorkspace } from './BioXpMethodsWorkspace';
import { BioXpWorkflowMutationProvider } from './BioXpWorkflowMutationOwner';

/** Offline authoring with a separate explicit saved-snapshot Run in Review. */
export function BioXpWorkflowEditor({ generation = 0, connected = false, controlsEnabled = false, visible = true }: { visible?: boolean; generation?: number; connected?: boolean; controlsEnabled?: boolean } = {}) {
    return <BioXpWorkflowMutationProvider><section className="w-full space-y-4" aria-label="BioXP workflows workspace">
        <header>
            <h2 className="text-xl font-semibold">Workflows</h2>
            <p className="mt-1 text-sm text-content-secondary">Plan on the deck, configure each action, and save your sequence. Editing does not send robot commands.</p>
        </header>
        <BioXpMethodsWorkspace visible={visible} generation={generation} connected={connected} controlsEnabled={controlsEnabled} />
        <details><summary>Existing saved deck workflows</summary>
        <BioXpWellPipettingPanel visible={visible} workflowAuthoring generation={generation} connected={connected} controlsEnabled={controlsEnabled} />
        </details>
    </section></BioXpWorkflowMutationProvider>;
}
