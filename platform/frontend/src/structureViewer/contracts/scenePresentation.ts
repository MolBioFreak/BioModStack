import type { ViewerMeasurement } from './measurements.js';
import type { ResidueRef } from './structureIdentity.js';

export type StructureComponentType = 'protein' | 'dna' | 'rna' | 'ligand' | 'glycan' | 'ion' | 'water' | 'unknown';

export type StructureRepresentationKind = 'cartoon' | 'surface' | 'ball-and-stick' | 'spacefill' | 'line' | 'gaussian-surface';

export interface StructureRepresentationState {
    readonly representationId: string;
    readonly documentId: string;
    readonly kind: StructureRepresentationKind;
    readonly visible: boolean;
    readonly opacity: number;
    readonly selectionSetId?: string;
}

export interface StructureLayerState {
    readonly layerId: string;
    readonly metricId?: string;
    readonly selectionSetId?: string;
    readonly visible: boolean;
    readonly opacity: number;
    readonly order: number;
    readonly palette?: string;
}

export interface StructureRgbColor { readonly r: number; readonly g: number; readonly b: number; }

export interface StructurePresentationQuery {
    readonly documentId: string;
    readonly entityId?: string | undefined;
    readonly labelAsymId?: string | undefined;
    readonly authAsymId?: string | undefined;
    readonly startLabelSeqId?: number | undefined;
    readonly endLabelSeqId?: number | undefined;
    readonly startAuthSeqId?: number | undefined;
    readonly endAuthSeqId?: number | undefined;
    readonly insertionCode?: string | null | undefined;
    readonly labelAtomIds?: readonly string[] | undefined;
    readonly authAtomIds?: readonly string[] | undefined;
    readonly altLoc?: string | undefined;
    readonly componentTypes?: readonly StructureComponentType[] | undefined;
    readonly color?: StructureRgbColor | string | number | null | undefined;
    readonly focus?: boolean | undefined;
    readonly tooltip?: string | undefined;
    readonly opacity?: number | undefined;
}

export interface StructureSelectionSet {
    readonly selectionSetId: string;
    readonly label: string;
    readonly residues: readonly ResidueRef[];
}

export interface StructureFilterState {
    readonly entityTypes?: readonly StructureComponentType[];
    readonly chainIds?: readonly string[];
    readonly residueRange?: readonly [number, number];
    readonly metricId?: string;
    readonly metricRange?: readonly [number, number];
    readonly includeMissing?: boolean;
    readonly neighborhoodAngstrom?: number;
}

export interface StructureCameraState {
    readonly mode: 'perspective' | 'orthographic';
    readonly target?: readonly [number, number, number];
    readonly position?: readonly [number, number, number];
    readonly up?: readonly [number, number, number];
    readonly radius?: number;
}

export interface StructureScenePresentation {
    readonly representations?: readonly StructureRepresentationState[];
    readonly layers?: readonly StructureLayerState[];
    readonly selection?: readonly StructureSelectionSet[];
    readonly hover?: ResidueRef;
    readonly filters?: StructureFilterState;
    readonly camera?: StructureCameraState;
    readonly measurements?: readonly ViewerMeasurement[];
    readonly colorQueries?: readonly StructurePresentationQuery[];
    readonly tooltipQueries?: readonly StructurePresentationQuery[];
    /** Exact structure components that remain loaded but are hidden from presentation. */
    readonly hiddenQueries?: readonly StructurePresentationQuery[];
    readonly nonSelectedColor?: StructureRgbColor | string | number;
}
