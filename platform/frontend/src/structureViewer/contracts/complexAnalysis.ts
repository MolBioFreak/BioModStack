import type { StructureComponentType } from './scenePresentation.js';

/**
 * A chain-level inventory derived by BioModStack analysis. This is deliberately
 * not an exact assembly/operator component instance.
 */
export interface DerivedStructureComponent {
    readonly documentId: string;
    readonly chainId: string;
    readonly componentType: StructureComponentType;
    readonly length: number;
    readonly provenance: string;
    readonly identityScope: 'derived-chain';
}
