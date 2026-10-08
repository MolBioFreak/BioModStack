import type { StructureViewerHostProps } from './StructureViewerHost';
import { CompactStructureWorkbench } from './workbench/CompactStructureWorkbench';
import { StandardStructureWorkbench } from './workbench/StandardStructureWorkbench';

export interface StructureWorkbenchProps extends StructureViewerHostProps { readonly mode?: 'compact' | 'standard'; }

export function StructureWorkbench({ mode = 'standard', ...props }: StructureWorkbenchProps) {
    return mode === 'compact' ? <CompactStructureWorkbench {...props} /> : <StandardStructureWorkbench {...props} />;
}
