import type { StructureViewerHostProps } from './StructureViewerHost';
import { StructureViewerErrorBoundary } from './StructureViewerErrorBoundary';
import { CompactStructureWorkbench } from './workbench/CompactStructureWorkbench';
import { StandardStructureWorkbench } from './workbench/StandardStructureWorkbench';

export interface StructureWorkbenchProps extends StructureViewerHostProps { readonly mode?: 'compact' | 'standard'; }

export function StructureWorkbench({ mode = 'standard', ...props }: StructureWorkbenchProps) {
    const resetKey = `${mode}:${props.structureUrl ?? 'inline'}:${props.structureData?.length ?? 0}:${props.format ?? 'pdb'}`;
    return (
        <StructureViewerErrorBoundary resetKey={resetKey} height={props.height}>
            {mode === 'compact'
                ? <CompactStructureWorkbench {...props} />
                : <StandardStructureWorkbench {...props} />}
        </StructureViewerErrorBoundary>
    );
}
