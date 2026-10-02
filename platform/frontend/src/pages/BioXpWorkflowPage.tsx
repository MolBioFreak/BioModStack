import { Link } from 'react-router-dom';
import { BioXpWellPipettingPanel } from '../components/BioXpWellPipettingPanel';

/** Standalone authoring: deliberately does not mount a robot connection owner. */
export function BioXpWorkflowPage() {
    return <div className="mx-auto w-full max-w-6xl space-y-4 p-4 sm:p-6" aria-label="BioXP workflows workspace">
        <header className="flex flex-wrap items-start justify-between gap-3">
            <div>
                <h1 className="text-2xl font-semibold text-content-primary">BioXP Workflows</h1>
                <p className="mt-1 text-sm text-content-secondary">Create, edit and save ordered workflows. No robot connection is needed.</p>
            </div>
            <Link to="/bioxp" className="rounded border border-slate-600 px-3 py-2 text-sm">Robot controls</Link>
        </header>
        <BioXpWellPipettingPanel workflowAuthoring generation={0} connected={false} />
    </div>;
}
