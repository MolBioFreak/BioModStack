import { useState } from 'react';
import { ProjectAttachmentDialog } from '../project-manager/ProjectAttachmentDialog';

export const NGS_ANALYSIS_ADAPTER = 'bms.ngs.analysis-reference.adapter.v1';

/** Add a run's published result to a Project when it has no construct-QC manifest to attach by. */
export function NativeResultProjectAttach({ jobId, label }: { jobId: string; label: string }) {
    const [open, setOpen] = useState(false);
    return (
        <>
            <button type="button" onClick={() => setOpen(true)} data-testid="native-result-add-to-project"
                className="rounded border border-[var(--accent-primary)]/50 px-2 py-1 text-[10px] font-semibold text-[var(--accent-primary)]">
                Add to Project
            </button>
            <ProjectAttachmentDialog
                open={open}
                source={{ adapterId: NGS_ANALYSIS_ADAPTER, entityId: jobId, label, availability: 'available' }}
                onClose={() => setOpen(false)}
            />
        </>
    );
}
