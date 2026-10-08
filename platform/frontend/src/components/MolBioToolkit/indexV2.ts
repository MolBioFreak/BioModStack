/**
 * MolBioToolkit - Barrel file for exports
 */

// Main components
export { MolBioToolkitV2 } from './MolBioToolkitV2';
export { SequenceViewer, EMPTY_SEQUENCE, DEFAULT_VISIBILITY } from './SequenceViewer';
export { SequenceHeader } from './SequenceHeader';
export { VisibilityPanel } from './VisibilityPanel';
export { ExportDropdown } from './ExportDropdown';

// Hooks
export { useSequenceHistory } from './hooks/useSequenceHistory';
export { useSequenceOperations, useMolBioOperations } from './hooks/useSequenceOperations';

// Types
export type {
    Feature,
    Primer,
    Translation,
    SequenceData,
    VisibilityState,
    SelectionInfo,
    NucleotideSequenceResponse,
    NucleotideSequenceListItem,
    EnzymeInfo,
    EnzymeFilter,
    DigestFragment,
    PCRProduct,
    ActivePanel,
    HighlightedRegion
} from './types';

// Default export for backwards compatibility route
export { default } from './MolBioToolkitV2';
