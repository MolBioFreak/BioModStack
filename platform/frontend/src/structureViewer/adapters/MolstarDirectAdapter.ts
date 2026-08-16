import type { Loci } from 'molstar/lib/mol-model/loci';
import {
    Queries,
    StructureElement,
    StructureProperties,
    StructureSelection,
} from 'molstar/lib/mol-model/structure';
import type { Structure } from 'molstar/lib/mol-model/structure';
import { to_mmCIF } from 'molstar/lib/mol-model/structure/export/mmcif';
import { Grid, Volume } from 'molstar/lib/mol-model/volume';
import { StructureQuery } from 'molstar/lib/mol-model/structure/query/query';
import { getElementMoleculeType } from 'molstar/lib/mol-model/structure/util';
import {
    clearStructureOverpaint,
    setStructureOverpaint,
} from 'molstar/lib/mol-plugin-state/helpers/structure-overpaint';
import { clearStructureTransparency, setStructureTransparency } from 'molstar/lib/mol-plugin-state/helpers/structure-transparency';
import { StateTransforms } from 'molstar/lib/mol-plugin-state/transforms';
import { createVolumeRepresentationParams } from 'molstar/lib/mol-plugin-state/helpers/volume-representation-params';
import type { LociLabelProvider } from 'molstar/lib/mol-plugin-state/manager/loci-label';
import { Asset } from 'molstar/lib/mol-util/assets';
import { Color } from 'molstar/lib/mol-util/color/color';
import { Mat4, Vec3 } from 'molstar/lib/mol-math/linear-algebra';
import { Box3D } from 'molstar/lib/mol-math/geometry';
import { PluginCommands } from 'molstar/lib/mol-plugin/commands';
import type { PluginUIContext } from 'molstar/lib/mol-plugin-ui/context';


import { createDirectMolstarEngineOwner } from '../runtime/createDirectMolstarEngineOwner';
import type { MolstarEngineOwner } from '../runtime/MolstarEngineOwner';
import type { MDPlaybackState, MDSceneState } from '../contracts/mdTrajectory';
import { assessMeasurement, type ViewerMeasurement } from '../contracts/measurements';
import type { StructureCameraState, StructureRepresentationKind, StructureRepresentationState, StructureScenePresentation, StructureComponentType } from '../contracts/scenePresentation';
import { resolveSceneRenderingProfile, type StructureSceneState } from '../contracts/sceneState';
import {
    absoluteContourValue,
    type SpatialVolumeDescriptorV1,
    type VolumePresentationStateV1,
    type VolumeRegistrationV1,
    type VolumeSegmentationV1,
} from '../contracts/spatialVolumes';
import {
    viewerCancelled,
    viewerError,
    viewerOk,
    viewerUnsupported,
    type ViewerResult,
} from '../contracts/viewerResults';

export type MolstarDirectDocumentFormat = 'mmcif' | 'pdb' | 'sdf';

export interface MolstarDirectDocument {
    readonly id: string;
    readonly url: string;
    readonly format: MolstarDirectDocumentFormat;
    readonly isBinary?: boolean;
    readonly assemblyId?: string;
}

export interface MolstarDirectQuery {
    readonly document_id?: string;
    readonly entity_id?: string;
    readonly struct_asym_id?: string;
    readonly auth_asym_id?: string;
    readonly residue_number?: number;
    readonly auth_residue_number?: number;
    readonly auth_seq_id?: number;
    readonly start_residue_number?: number;
    readonly end_residue_number?: number;
    readonly start_auth_residue_number?: number;
    readonly end_auth_residue_number?: number;
    readonly auth_ins_code_id?: string;
    readonly atoms?: readonly string[];
    readonly auth_atoms?: readonly string[];
    readonly atom_id?: readonly number[];
    readonly alt_loc_id?: string;
    readonly component_types?: readonly StructureComponentType[];
    readonly color?: string | number | { r: number; g: number; b: number };
    readonly focus?: boolean;
    readonly tooltip?: string;
    readonly opacity?: number;
}

export interface MolstarDirectPresentation {
    readonly colorSelections?: readonly MolstarDirectQuery[];
    readonly nonSelectedColor?: string | number | { r: number; g: number; b: number };
    readonly tooltipSelections?: readonly MolstarDirectQuery[];
    readonly hiddenSelections?: readonly MolstarDirectQuery[];
    readonly representations?: readonly StructureRepresentationState[];
}

const representationKind = (name: string): StructureRepresentationKind | undefined => ({
    cartoon: 'cartoon',
    'molecular-surface': 'surface',
    'ball-and-stick': 'ball-and-stick',
    spacefill: 'spacefill',
    line: 'line',
    'gaussian-surface': 'gaussian-surface',
}[name] as StructureRepresentationKind | undefined);

export interface MolstarDirectResidueClick {
    readonly documentId: string;
    readonly labelAsymId: string;
    readonly authAsymId: string;
    readonly labelSeqId: number;
    readonly authSeqId: number;
    readonly insertionCode: string;
    readonly sceneGeneration: number;
}

export interface MolstarDirectAdapterDiagnostics {
    readonly disposed: boolean;
    readonly pluginDisposed: boolean;
    readonly structureCount: number;
    readonly hasCanvas3d: boolean;
    readonly completedSceneGeneration: number;
    readonly measurementCount: number;
}

const adapterRegistry = new WeakMap<HTMLElement, MolstarDirectAdapter>();

const isPluginDisposed = (plugin: PluginUIContext | undefined): boolean => (
    (plugin as unknown as { disposed?: boolean } | undefined)?.disposed === true
);

// MoleculeType is an ambient const enum in Mol* 4.5 and has no runtime export.
// Keep the pinned discriminants local so Vite never emits an invalid ESM import.
const MOLSTAR_45_MOLECULE_TYPE = Object.freeze({
    Other: 1,
    Water: 2,
    Ion: 3,
    Protein: 5,
    RNA: 6,
    DNA: 7,
    Saccharide: 9,
});

const componentTypeForLocation = (location: StructureElement.Location): StructureComponentType => {
    const entityType = StructureProperties.entity.type(location);
    const subtype = StructureProperties.entity.subtype(location).toLowerCase();
    if (entityType === 'water') return 'water';
    if (entityType === 'branched' || subtype.includes('oligosaccharide')) return 'glycan';
    if (subtype === 'ion') return 'ion';
    if (entityType === 'polymer') {
        if (/polypeptide|cyclic-pseudo-peptide|peptide-like/.test(subtype)) return 'protein';
        if (subtype === 'polydeoxyribonucleotide') return 'dna';
        if (subtype === 'polyribonucleotide') return 'rna';
    }
    switch (getElementMoleculeType(location.unit, location.element)) {
        case MOLSTAR_45_MOLECULE_TYPE.Protein: return 'protein';
        case MOLSTAR_45_MOLECULE_TYPE.DNA: return 'dna';
        case MOLSTAR_45_MOLECULE_TYPE.RNA: return 'rna';
        case MOLSTAR_45_MOLECULE_TYPE.Saccharide: return 'glycan';
        case MOLSTAR_45_MOLECULE_TYPE.Ion: return 'ion';
        case MOLSTAR_45_MOLECULE_TYPE.Water: return 'water';
        case MOLSTAR_45_MOLECULE_TYPE.Other: return entityType === 'non-polymer' ? 'ligand' : 'unknown';
        default: return 'unknown';
    }
};

const queryLoci = (params: readonly MolstarDirectQuery[], structure: Structure): StructureElement.Loci => {
    const queries = params.map((param) => {
        return Queries.generators.atoms({
            ...(param.entity_id ? {
                entityTest: (location) => StructureProperties.entity.id(location.element) === param.entity_id,
            } : {}),
            ...((param.struct_asym_id || param.auth_asym_id) ? {
                chainTest: (location) => (
                    (!param.struct_asym_id
                        || StructureProperties.chain.label_asym_id(location.element) === param.struct_asym_id)
                    && (!param.auth_asym_id
                        || StructureProperties.chain.auth_asym_id(location.element) === param.auth_asym_id)
                ),
            } : {}),
            ...((param.residue_number !== undefined
                || param.auth_seq_id !== undefined
                || param.auth_residue_number !== undefined
                || param.start_residue_number !== undefined
                || param.end_residue_number !== undefined
                || param.start_auth_residue_number !== undefined
                || param.end_auth_residue_number !== undefined
                || param.auth_ins_code_id !== undefined) ? {
                residueTest: (location) => {
                    const labelSeqId = StructureProperties.residue.label_seq_id(location.element);
                    const authSeqId = StructureProperties.residue.auth_seq_id(location.element);
                    const insertionCode = StructureProperties.residue.pdbx_PDB_ins_code(location.element);
                    if (param.residue_number !== undefined && labelSeqId !== param.residue_number) return false;
                    if ((param.start_residue_number === undefined) !== (param.end_residue_number === undefined)) return false;
                    if (param.start_residue_number !== undefined
                        && (labelSeqId < param.start_residue_number || labelSeqId > param.end_residue_number!)) return false;
                    if (param.auth_seq_id !== undefined && authSeqId !== param.auth_seq_id) return false;
                    if (param.auth_residue_number !== undefined && authSeqId !== param.auth_residue_number) return false;
                    if ((param.start_auth_residue_number === undefined) !== (param.end_auth_residue_number === undefined)) return false;
                    if (param.start_auth_residue_number !== undefined
                        && (authSeqId < param.start_auth_residue_number || authSeqId > param.end_auth_residue_number!)) return false;
                    if (param.auth_ins_code_id !== undefined && insertionCode !== param.auth_ins_code_id) return false;
                    return true;
                },
            } : {}),
            ...((param.atoms || param.auth_atoms || param.atom_id || param.alt_loc_id !== undefined || param.component_types?.length) ? {
                atomTest: (location) => (
                    (!param.component_types?.length
                        || param.component_types.includes(componentTypeForLocation(location.element)))
                    && (!param.atoms
                        || param.atoms.includes(StructureProperties.atom.label_atom_id(location.element)))
                    && (!param.auth_atoms
                        || param.auth_atoms.includes(StructureProperties.atom.auth_atom_id(location.element)))
                    && (!param.atom_id
                        || param.atom_id.includes(StructureProperties.atom.id(location.element)))
                    && (param.alt_loc_id === undefined
                        || StructureProperties.atom.label_alt_id(location.element) === param.alt_loc_id)
                ),
            } : {}),
        });
    });
    const selection = StructureQuery.run(Queries.combinators.merge(queries), structure);
    return StructureSelection.toLociWithSourceUnits(selection);
};

const normalizeColor = (
    value: MolstarDirectPresentation['nonSelectedColor'] | MolstarDirectQuery['color'],
    fallback = Color.fromRgb(255, 112, 3),
): Color => {
    if (typeof value === 'number') return Color(value);
    if (typeof value === 'string' && /^#[0-9a-f]{6}$/i.test(value)) return Color.fromHexStyle(value);
    if (value && typeof value === 'object') {
        const rgb = value as { r?: number; g?: number; b?: number };
        return Color.fromRgb(rgb.r ?? 0, rgb.g ?? 0, rgb.b ?? 0);
    }
    return fallback;
};

const VIEWER_RANGE_BYTES = 64 * 1024 * 1024;

const fetchVerifiedArtifactBytes = async (url: string, byteLength: number, expectedSha256: string, signal: AbortSignal): Promise<Uint8Array> => {
    const bytes = new Uint8Array(byteLength);
    for (let start = 0; start < byteLength; start += VIEWER_RANGE_BYTES) {
        const end = Math.min(byteLength - 1, start + VIEWER_RANGE_BYTES - 1);
        const response = await fetch(url, { credentials: 'same-origin', signal, headers: { Range: `bytes=${start}-${end}` } });
        if (response.status !== 206) throw new Error(`Bounded artifact delivery required HTTP 206, received ${response.status}`);
        if (response.headers.get('content-range') !== `bytes ${start}-${end}/${byteLength}`) throw new Error('Volume artifact Content-Range mismatch');
        const chunk = new Uint8Array(await response.arrayBuffer());
        if (chunk.byteLength !== end - start + 1) throw new Error('Volume artifact range length mismatch');
        bytes.set(chunk, start);
    }
    const digest = await crypto.subtle.digest('SHA-256', bytes.slice().buffer);
    const actual = [...new Uint8Array(digest)].map((byte) => byte.toString(16).padStart(2, '0')).join('');
    if (actual !== expectedSha256) throw new Error('Volume artifact SHA-256 mismatch');
    return bytes;
};

export interface MolstarDirectProbe { readonly diagnostics: MolstarDirectAdapterDiagnostics; }
export const getMolstarDirectProbeForElement = (element: HTMLElement): MolstarDirectProbe | undefined => {
    const adapter = adapterRegistry.get(element);
    return adapter ? { get diagnostics() { return adapter.diagnostics; } } : undefined;
};

export interface MolstarDirectAdapterOptions {
    readonly hideControls?: boolean;
    readonly alphafoldView?: boolean;
    readonly backgroundColor?: string;
    /** Runtime-only authorized artifact transport; never persisted in scene state. */
    readonly resolveViewerArtifactUrl?: (artifactId: string) => string;
}

interface DirectVolumeEntry {
    readonly descriptor: SpatialVolumeDescriptorV1;
    readonly sourceRef: string;
    activeRef: string;
    transformRef?: string;
    representationRef?: string;
    segmentation?: VolumeSegmentationV1;
}

interface DirectMolecularDynamicsEntry {
    readonly replica: number;
    readonly modelRef: string;
    readonly frameCount: number;
}

export class MolstarDirectAdapterCancelledError extends Error {
    constructor() {
        super('Mol* adapter operation was cancelled');
        this.name = 'MolstarDirectAdapterCancelledError';
    }
}

export class MolstarDirectAdapter {
    private readonly owner: MolstarEngineOwner<PluginUIContext>;
    private readonly backgroundColor: string;
    private readonly resolveViewerArtifactUrl?: (artifactId: string) => string;
    private plugin: PluginUIContext | undefined;
    private disposedPlugin: PluginUIContext | undefined;
    private target: HTMLElement | undefined;
    private sceneGeneration = 0;
    private completedSceneGeneration = 0;
    private presentationGeneration = 0;
    private disposed = false;
    private hasSelection = false;
    private hasOverpaint = false;
    private hasTransparency = false;
    private hasTooltips = false;
    private tooltipProvider: LociLabelProvider | undefined;
    private residueClickHandler: ((residue: MolstarDirectResidueClick) => void) | undefined;
    private clickSubscription: { unsubscribe(): void } | undefined;
    private documentStructures = new WeakMap<Structure, string>();
    private measurementSelectionRefs: string[] = [];
    private measurementGeneration = 0;
    private sceneQueue: Promise<void> = Promise.resolve();
    private presentationQueue: Promise<void> = Promise.resolve();
    private measurementQueue: Promise<void> = Promise.resolve();
    private readonly volumes = new Map<string, DirectVolumeEntry>();
    private molecularDynamics: DirectMolecularDynamicsEntry | undefined;

    constructor({
        hideControls = true,
        alphafoldView = false,
        backgroundColor = '#0f172a',
        resolveViewerArtifactUrl,
    }: MolstarDirectAdapterOptions = {}) {
        this.backgroundColor = backgroundColor;
        this.resolveViewerArtifactUrl = resolveViewerArtifactUrl;
        this.owner = createDirectMolstarEngineOwner({ hideControls, alphafoldView });
    }


    async mount(target: HTMLElement): Promise<void> {
        if (this.disposed) throw new MolstarDirectAdapterCancelledError();
        this.target = target;
        adapterRegistry.set(target, this);

        const result = await this.owner.initialize(target);
        if (result.status === 'cancelled') throw new MolstarDirectAdapterCancelledError();
        if (result.status === 'error') throw result.error;
        if (this.disposed) throw new MolstarDirectAdapterCancelledError();
        if (!result.plugin.canvas3d) {
            this.owner.dispose();
            throw new Error('Mol* WebGL canvas failed to initialize');
        }

        this.plugin = result.plugin;
        this.disposedPlugin = undefined;
        try {
            result.plugin.managers.interactivity.setProps({ granularity: 'residue' });
            this.clickSubscription = result.plugin.behaviors.interaction.click.subscribe(({ current }) => {
                const loci = current.loci;
                if (!StructureElement.Loci.is(loci)) return;
                const location = StructureElement.Loci.getFirstLocation(loci);
                if (!location) return;
                const labelAsymId = StructureProperties.chain.label_asym_id(location);
                const authAsymId = StructureProperties.chain.auth_asym_id(location);
                const labelSeqId = StructureProperties.residue.label_seq_id(location);
                const authSeqId = StructureProperties.residue.auth_seq_id(location);
                const insertionCode = StructureProperties.residue.pdbx_PDB_ins_code(location);
                const documentId = this.documentStructures.get(loci.structure);
                if (!documentId || !labelAsymId || !authAsymId || !Number.isInteger(labelSeqId) || !Number.isInteger(authSeqId)) return;
                this.residueClickHandler?.({
                    documentId,
                    labelAsymId,
                    authAsymId,
                    labelSeqId,
                    authSeqId,
                    insertionCode,
                    sceneGeneration: this.completedSceneGeneration,
                });
            });
            await this.setBackground(this.backgroundColor);
        } catch (error) {
            this.dispose();
            throw error;
        }
    }

    setResidueClickHandler(handler: ((residue: MolstarDirectResidueClick) => void) | undefined): void {
        this.residueClickHandler = handler;
    }

    loadScene(
        documents: readonly MolstarDirectDocument[],
        scene: Pick<StructureSceneState, 'molecularDynamics' | 'renderingProfile'>,
    ): Promise<void> {
        this.requirePlugin();
        const generation = ++this.sceneGeneration;
        this.presentationGeneration += 1;
        this.measurementGeneration += 1;
        this.hasSelection = false;
        this.hasOverpaint = false;
        this.hasTransparency = false;
        this.hasTooltips = false;

        const task = this.sceneQueue.then(async () => {
            if (!this.isSceneCurrent(generation)) throw new MolstarDirectAdapterCancelledError();
            const plugin = this.requirePlugin();
            try {
                if (this.tooltipProvider) plugin.managers.lociLabels.removeProvider(this.tooltipProvider);
                this.tooltipProvider = undefined;
                await plugin.clear();
                this.assertSceneCurrent(generation);
                this.volumes.clear();
                this.molecularDynamics = undefined;
                this.documentStructures = new WeakMap<Structure, string>();
                this.measurementSelectionRefs = [];

                const documentsToLoad = scene.molecularDynamics?.playbackCapability.supported ? [] : documents;
                for (const document of documentsToLoad) {
                    const existingStructures = new Set(
                        plugin.managers.structure.hierarchy.current.structures
                            .flatMap((entry) => entry.cell.obj?.data ? [entry.cell.obj.data] : []),
                    );
                    const data = await plugin.builders.data.download({
                        url: Asset.Url(document.url),
                        isBinary: document.isBinary ?? false,
                    }, { state: { isGhost: true } });
                    this.assertSceneCurrent(generation);

                    const trajectory = await plugin.builders.structure.parseTrajectory(data, document.format);
                    this.assertSceneCurrent(generation);

                    await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default', {
                        structure: document.assemblyId
                            ? { name: 'assembly', params: { id: document.assemblyId } }
                            : { name: 'model', params: {} },
                        showUnitcell: false,
                        representationPreset: resolveSceneRenderingProfile(scene),
                    });
                    this.assertSceneCurrent(generation);
                    for (const entry of plugin.managers.structure.hierarchy.current.structures) {
                        const structure = entry.cell.obj?.data;
                        if (structure && !existingStructures.has(structure)) {
                            this.documentStructures.set(structure, document.id);
                        }
                    }
                }
                this.completedSceneGeneration = generation;
            } catch (error) {
                if (!this.isSceneCurrent(generation)) throw new MolstarDirectAdapterCancelledError();
                try {
                    await plugin.clear();
                } catch {
                    // Preserve the original load error while making a best-effort rollback.
                }
                throw error;
            }
        });
        this.sceneQueue = task.catch(() => undefined);
        return task;
    }

    /**
     * Load the governed active replica through Mol* 4.5's public GRO/XTC state transforms.
     * Artifact URLs are resolved at this runtime boundary and never copied into MDSceneState.
     */
    async loadMolecularDynamics(state: MDSceneState): Promise<ViewerResult<void>> {
        const replica = state.replicas.find((item) => item.replica === state.activeReplica);
        if (!replica || replica.trajectoryFormat !== 'xtc') {
            return viewerUnsupported('Direct Mol* trajectory playback requires an active XTC replica', 'trajectories');
        }
        if (!this.resolveViewerArtifactUrl) {
            return viewerUnsupported('Direct Mol* trajectory playback requires a runtime artifact URL resolver', 'trajectories');
        }

        const generation = this.sceneGeneration;
        const plugin = this.requirePlugin();
        try {
            const topologyData = await plugin.builders.data.download({
                url: Asset.Url(this.resolveViewerArtifactUrl(replica.topologyArtifactId)),
                isBinary: false,
                label: `MD replica ${replica.replica} GRO topology`,
            }, { state: { isGhost: true } });
            this.assertSceneCurrent(generation);
            const topologyTrajectory = await plugin.builders.structure.parseTrajectory(topologyData, 'gro');
            this.assertSceneCurrent(generation);
            const topologyModel = await plugin.builders.structure.createModel(topologyTrajectory, { modelIndex: 0 });
            this.assertSceneCurrent(generation);

            const coordinateData = await plugin.builders.data.download({
                url: Asset.Url(this.resolveViewerArtifactUrl(replica.trajectoryArtifactId)),
                isBinary: true,
                label: `MD replica ${replica.replica} XTC coordinates`,
            }, { state: { isGhost: true } });
            this.assertSceneCurrent(generation);
            const coordinates = await plugin.state.data.build()
                .to(coordinateData)
                .apply(StateTransforms.Model.CoordinatesFromXtc)
                .commit();
            this.assertSceneCurrent(generation);
            const trajectory = await plugin.build().toRoot()
                .apply(StateTransforms.Model.TrajectoryFromModelAndCoordinates, {
                    modelRef: topologyModel.ref,
                    coordinatesRef: coordinates.ref,
                }, { dependsOn: [topologyModel.ref, coordinates.ref] })
                .commit();
            this.assertSceneCurrent(generation);
            const preset = await plugin.builders.structure.hierarchy.applyPreset(trajectory, 'default', {
                structure: { name: 'model', params: {} },
                showUnitcell: false,
                representationPreset: 'atomic-detail',
            });
            this.assertSceneCurrent(generation);

            const modelRef = preset?.model.ref;
            const frameCount = trajectory.cell?.obj?.data.frameCount;
            if (!modelRef || !frameCount || frameCount < 1) {
                throw new Error('Mol* did not create an addressable XTC model trajectory');
            }
            this.molecularDynamics = { replica: replica.replica, modelRef, frameCount };
            for (const entry of plugin.managers.structure.hierarchy.current.structures) {
                const structure = entry.cell.obj?.data;
                if (structure) this.documentStructures.set(structure, `md:replica:${replica.replica}`);
            }
            return viewerOk(undefined);
        } catch (error) {
            if (!this.isSceneCurrent(generation)) throw new MolstarDirectAdapterCancelledError();
            try {
                await plugin.clear();
            } catch {
                // Preserve the parse/load error while leaving no partial trajectory scene behind.
            }
            this.molecularDynamics = undefined;
            return viewerError(error);
        }
    }

    /** Select an XTC frame strictly by the bounded Mol* model/display index. */
    async selectMolecularDynamicsDisplayFrame(displayFrame: number): Promise<ViewerResult<void>> {
        const molecularDynamics = this.molecularDynamics;
        if (!molecularDynamics) return viewerUnsupported('No direct Mol* XTC trajectory is loaded', 'trajectories');
        if (!Number.isInteger(displayFrame) || displayFrame < 0 || displayFrame >= molecularDynamics.frameCount) {
            return viewerUnsupported(`Display frame ${displayFrame} is outside the loaded XTC range`, 'trajectories');
        }
        try {
            await this.requirePlugin().build()
                .to(molecularDynamics.modelRef)
                .update({ modelIndex: displayFrame })
                .commit();
            return viewerOk(undefined);
        } catch (error) {
            return viewerError(error);
        }
    }

    async setMolecularDynamicsPlayback(playback: MDPlaybackState): Promise<ViewerResult<void>> {
        if (playback.selectedFrame) return this.selectMolecularDynamicsDisplayFrame(playback.selectedFrame.displayFrame);
        if (playback.state === 'playing') {
            return viewerUnsupported('Direct Mol* playback currently accepts explicit bounded display-frame selection only', 'trajectories');
        }
        return this.molecularDynamics
            ? viewerOk(undefined)
            : viewerUnsupported('No direct Mol* XTC trajectory is loaded', 'trajectories');
    }

    setMeasurements(measurements: readonly ViewerMeasurement[]): Promise<ViewerResult<void>> {
        const ids = measurements.map((measurement) => measurement.measurementId);
        if (new Set(ids).size !== ids.length) {
            return Promise.resolve(viewerUnsupported('Measurement IDs must be unique', 'measurements'));
        }
        for (const measurement of measurements) {
            const assessment = assessMeasurement(measurement);
            if (assessment.status !== 'ok') return Promise.resolve(assessment);
            for (const point of measurement.points) {
                if (point.modelId || point.sourceEntityId || point.sourceInstanceId
                    || point.assemblyId || point.operatorInstanceId || point.atomIndex !== undefined) {
                    return Promise.resolve(viewerUnsupported(
                        'Direct Mol* measurements do not yet support model, source-instance, assembly/operator, or engine atom-index identity',
                        'measurement-identity',
                    ));
                }
            }
        }

        const sceneGeneration = this.sceneGeneration;
        const measurementGeneration = ++this.measurementGeneration;
        const task = this.measurementQueue.then(async (): Promise<ViewerResult<void>> => {
            await this.sceneQueue;
            if (!this.isSceneCurrent(sceneGeneration) || measurementGeneration !== this.measurementGeneration) {
                return viewerCancelled('Measurement reconciliation was superseded');
            }
            const plugin = this.requirePlugin();
            const planned: Array<{ measurement: ViewerMeasurement; locis: StructureElement.Loci[] }> = [];
            for (const measurement of measurements) {
                const locis: StructureElement.Loci[] = [];
                for (const point of measurement.points) {
                    const structureEntry = plugin.managers.structure.hierarchy.current.structures.find((entry) => {
                        const structure = entry.cell.obj?.data;
                        return structure ? this.documentStructures.get(structure) === point.documentId : false;
                    });
                    const structure = structureEntry?.cell.obj?.data;
                    if (!structure) return viewerUnsupported(`Measurement document ${point.documentId} is not loaded in this scene`, 'measurements');
                    const loci = queryLoci([{
                        entity_id: point.entityId,
                        struct_asym_id: point.labelAsymId,
                        auth_asym_id: point.authAsymId,
                        residue_number: point.labelSeqId,
                        auth_seq_id: point.authSeqId,
                        auth_ins_code_id: point.insertionCode,
                        atoms: point.labelAtomId ? [point.labelAtomId] : undefined,
                        auth_atoms: point.authAtomId ? [point.authAtomId] : undefined,
                        alt_loc_id: point.altLoc,
                    }], structure);
                    const atomCount = StructureElement.Loci.size(loci);
                    if (atomCount !== 1) {
                        return viewerUnsupported(
                            `Measurement atom ${point.labelAtomId ?? point.authAtomId ?? '?'} resolved to ${atomCount} atoms; exactly one is required`,
                            'measurements',
                        );
                    }
                    locis.push(loci);
                }
                planned.push({ measurement, locis });
            }

            const stagedRefs: string[] = [];
            try {
                for (const { measurement, locis } of planned) {
                    const options = {
                        customText: measurement.label,
                        selectionTags: `bms-measurement:${measurement.measurementId}`,
                        reprTags: `bms-measurement:${measurement.measurementId}`,
                    };
                    const created = measurement.type === 'distance'
                        ? await plugin.managers.structure.measurement.addDistance(locis[0]!, locis[1]!, options)
                        : measurement.type === 'angle'
                            ? await plugin.managers.structure.measurement.addAngle(locis[0]!, locis[1]!, locis[2]!, options)
                            : await plugin.managers.structure.measurement.addDihedral(locis[0]!, locis[1]!, locis[2]!, locis[3]!, options);
                    if (!created) throw new Error(`Mol* could not stage measurement ${measurement.measurementId}`);
                    stagedRefs.push(created.selection.ref);
                }
            } catch (error) {
                for (const ref of stagedRefs) {
                    try { await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref }); } catch { /* preserve original failure */ }
                }
                return viewerError(error);
            }

            const previousRefs = this.measurementSelectionRefs;
            try {
                for (const ref of previousRefs) {
                    await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref });
                }
            } catch (error) {
                for (const ref of stagedRefs) {
                    try { await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref }); } catch { /* best-effort rollback */ }
                }
                return viewerError(error);
            }
            this.measurementSelectionRefs = stagedRefs;
            return viewerOk(undefined);
        });
        this.measurementQueue = task.then(() => undefined, () => undefined);
        return task.catch((error) => viewerError(error));
    }

    private representationEntries() {
        const plugin = this.requirePlugin();
        return plugin.managers.structure.hierarchy.current.structures.flatMap((structureRef) => {
            const structure = structureRef.cell.obj?.data;
            const documentId = structure ? this.documentStructures.get(structure) : undefined;
            if (!documentId) return [];
            return structureRef.components.flatMap((component, componentIndex) => component.representations.flatMap((representation, representationIndex) => {
                const params = representation.cell.transform.params as { type?: { name?: string; params?: { alpha?: number } } } | undefined;
                const kind = representationKind(params?.type?.name ?? '');
                if (!kind) throw new Error(`Mol* representation kind ${params?.type?.name ?? '<missing>'} is not supported by the saved-review contract`);
                return [{
                    representation,
                    state: {
                        representationId: `${documentId}:${component.key ?? componentIndex}:${kind}:${representationIndex}`,
                        documentId,
                        kind,
                        visible: !representation.cell.state.isHidden,
                        opacity: params?.type?.params?.alpha ?? 1,
                    } satisfies StructureRepresentationState,
                }];
            }));
        });
    }

    capturePresentation(): ViewerResult<StructureScenePresentation> {
        const canvas = this.requirePlugin().canvas3d;
        if (!canvas) return viewerUnsupported('Mol* canvas is unavailable for presentation capture', 'camera');
        const state = canvas.camera.state;
        const vector = (value: ArrayLike<number>): [number, number, number] => [value[0]!, value[1]!, value[2]!];
        return viewerOk({
            camera: {
                mode: canvas.props.camera.mode,
                target: vector(state.target),
                position: vector(state.position),
                up: vector(state.up),
                radius: state.radius,
            },
            representations: this.representationEntries().map((entry) => entry.state),
        });
    }

    private async applyRepresentations(states: readonly StructureRepresentationState[]): Promise<void> {
        const plugin = this.requirePlugin();
        const entries = new Map(this.representationEntries().map((entry) => [entry.state.representationId, entry]));
        if (states.length !== entries.size) throw new Error('Saved representation set does not match the loaded Mol* hierarchy');
        const update = plugin.state.data.build();
        for (const state of states) {
            const entry = entries.get(state.representationId);
            if (!entry || entry.state.documentId !== state.documentId || entry.state.kind !== state.kind) {
                throw new Error(`Saved representation ${state.representationId} does not match the loaded Mol* hierarchy`);
            }
            plugin.managers.structure.hierarchy.toggleVisibility([entry.representation], state.visible ? 'show' : 'hide');
            update.to(entry.representation.cell).update((params: { type?: { params?: { alpha?: number } } }) => {
                if (!params.type?.params) throw new Error(`Mol* representation ${state.representationId} has no opacity parameters`);
                params.type.params.alpha = state.opacity;
            });
        }
        await update.commit({ revertOnError: true });
    }

    applyPresentation(presentation: MolstarDirectPresentation): Promise<void> {
        const generation = ++this.presentationGeneration;
        const task = this.presentationQueue.then(async () => {
            if (!this.isPresentationCurrent(generation)) return;
            const plugin = this.requirePlugin();
            const colors = presentation.colorSelections ?? [];
            const tooltips = presentation.tooltipSelections ?? [];
            const hidden = presentation.hiddenSelections ?? [];
            if (presentation.representations) await this.applyRepresentations(presentation.representations);

            if (colors.length > 0 || hidden.length > 0) {
                await this.applyColorSelections(plugin, colors, presentation.nonSelectedColor);
                await this.applyHiddenSelections(plugin, hidden);
                this.hasSelection = true;
            } else if (this.hasSelection) {
                await this.clearColorSelections(plugin);
                this.hasSelection = false;
            }
            if (!this.isPresentationCurrent(generation)) return;

            if (tooltips.length > 0) {
                await this.applyTooltips(plugin, tooltips);
                this.hasTooltips = true;
            } else if (this.hasTooltips) {
                await this.applyTooltips(plugin, []);
                this.hasTooltips = false;
            }
        }).catch((error) => {
            if (!this.isPresentationCurrent(generation)) return;
            throw error;
        });

        this.presentationQueue = task.catch(() => undefined);
        return task;
    }

    applyCamera(camera: StructureCameraState): ViewerResult<void> {
        const canvas = this.requirePlugin().canvas3d;
        if (!canvas) return viewerUnsupported('Mol* canvas is unavailable for camera reconciliation', 'camera');
        canvas.setProps({ camera: { ...canvas.props.camera, mode: camera.mode } });
        canvas.camera.setState({
            ...(camera.target ? { target: Vec3.create(...camera.target) } : {}),
            ...(camera.position ? { position: Vec3.create(...camera.position) } : {}),
            ...(camera.up ? { up: Vec3.create(...camera.up) } : {}),
            ...(camera.radius !== undefined ? { radius: camera.radius } : {}),
        });
        return viewerOk(undefined);
    }

    resetCamera(durationMs = 250): ViewerResult<void> {
        const canvas = this.requirePlugin().canvas3d;
        if (!canvas) return viewerUnsupported('Mol* canvas is unavailable for camera reset', 'camera');
        canvas.requestCameraReset({ durationMs });
        return viewerOk(undefined);
    }

    async loadVolume(descriptor: SpatialVolumeDescriptorV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
        const plugin = this.requirePlugin();
        const resolveUrl = this.resolveViewerArtifactUrl;
        if (!resolveUrl) return viewerUnsupported('No authorized viewer-artifact resolver is configured', 'volume-ccp4-v1');
        if (this.volumes.has(descriptor.volumeId)) {
            const removed = await this.removeVolume(descriptor.volumeId, signal);
            if (removed.status !== 'ok') return removed;
        }
        let dataRef: string | undefined;
        try {
            const bytes = await fetchVerifiedArtifactBytes(
                resolveUrl(descriptor.artifactId), descriptor.byteLength, descriptor.artifactSha256, signal,
            );
            if (signal.aborted) throw new MolstarDirectAdapterCancelledError();

            const data = await plugin.builders.data.rawData({
                data: bytes,
                label: `${descriptor.semanticKind} (${descriptor.volumeId})`,
            }, { state: { isGhost: true } });
            dataRef = data.ref;
            const format = plugin.build().to(data).apply(StateTransforms.Data.ParseCcp4, {}, { state: { isGhost: true } });
            const parsedVolume = format.apply(StateTransforms.Volume.VolumeFromCcp4, { entryId: descriptor.volumeId });
            await format.commit({ revertOnError: true });
            const parsed = parsedVolume.selector.data;
            const parsedFormat = format.selector.data as { header?: { MAPC: number; MAPR: number; MAPS: number } } | undefined;
            if (!parsed || !parsedFormat?.header) throw new Error('Mol* CCP4 parser returned incomplete volume state');
            const actualDimensions = [...parsed.grid.cells.space.dimensions];
            if (actualDimensions.some((value, index) => value !== descriptor.dimensions[index])) throw new Error('CCP4 grid dimensions do not match the governed descriptor');
            const actualAxisOrder = [parsedFormat.header.MAPC - 1, parsedFormat.header.MAPR - 1, parsedFormat.header.MAPS - 1];
            if (actualAxisOrder.some((value, index) => value !== descriptor.axisOrder[index])) throw new Error('CCP4 axis order does not match the governed descriptor');
            const actualTransform = [...Grid.getGridToCartesianTransform(parsed.grid)];
            const actualTransformRowMajor = [
                actualTransform[0], actualTransform[4], actualTransform[8], actualTransform[12],
                actualTransform[1], actualTransform[5], actualTransform[9], actualTransform[13],
                actualTransform[2], actualTransform[6], actualTransform[10], actualTransform[14],
                actualTransform[3], actualTransform[7], actualTransform[11], actualTransform[15],
            ];
            if (actualTransformRowMajor.some((value, index) => Math.abs(value! - descriptor.gridToWorldRowMajor4x4[index]!) > 1e-5)) {
                throw new Error('CCP4 grid-to-world transform does not match the governed descriptor');
            }
            this.volumes.set(descriptor.volumeId, {
                descriptor,
                sourceRef: parsedVolume.ref,
                activeRef: parsedVolume.ref,
            });
            return viewerOk(undefined);
        } catch (error) {
            if (dataRef) {
                try {
                    await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref: dataRef, removeParentGhosts: true });
                } catch { /* best-effort rollback */ }
            }
            return error instanceof MolstarDirectAdapterCancelledError || signal.aborted
                ? viewerCancelled('Mol* operation was cancelled')
                : viewerError(error);
        }
    }

    async applyVolumeSegmentation(segmentation: VolumeSegmentationV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
        const plugin = this.requirePlugin();
        const entry = this.volumes.get(segmentation.volumeId);
        if (!entry || entry.descriptor.semanticKind !== 'segmentation') {
            return viewerUnsupported(`Supplied segmentation volume ${segmentation.volumeId} is not loaded`, 'volume-segmentation-v1');
        }
        const volume = plugin.state.data.cells.get(entry.sourceRef)?.obj?.data as Volume | undefined;
        if (!volume) return viewerUnsupported('Loaded segmentation volume state is unavailable', 'volume-segmentation-v1');
        const [x, y, z] = volume.grid.cells.space.dimensions;
        const segments = new Map<number, Set<number>>();
        const sets = new Map<number, Set<number>>();
        const bounds: Record<number, Box3D> = {};
        const labels: Record<number, string> = {};
        const extents = new Map<number, [number, number, number, number, number, number]>();
        for (const label of segmentation.labels) {
            segments.set(label.segmentId, new Set([label.segmentId]));
            sets.set(label.segmentId, new Set([label.segmentId]));
            extents.set(label.segmentId, [x, y, z, -1, -1, -1]);
            if (label.label !== null) labels[label.segmentId] = label.label;
        }
        const coordinates: [number, number, number] = [0, 0, 0];
        const values = volume.grid.cells.data;
        for (let index = 0; index < values.length; index += 1) {
            if ((index & 0xFFFFF) === 0) {
                if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
                await Promise.resolve();
            }
            const segmentId = values[index]!;
            if (segmentId === 0) continue;
            if (!Number.isInteger(segmentId) || !extents.has(segmentId)) {
                return viewerUnsupported(`Segmentation voxel value ${segmentId} has no exact supplied label`, 'volume-segmentation-v1');
            }
            volume.grid.cells.space.getCoords(index, coordinates);
            const extent = extents.get(segmentId)!;
            extent[0] = Math.min(extent[0], coordinates[0]); extent[1] = Math.min(extent[1], coordinates[1]); extent[2] = Math.min(extent[2], coordinates[2]);
            extent[3] = Math.max(extent[3], coordinates[0]); extent[4] = Math.max(extent[4], coordinates[1]); extent[5] = Math.max(extent[5], coordinates[2]);
        }
        for (const [segmentId, extent] of extents) {
            if (extent[3] < 0) return viewerUnsupported(`Supplied segment ${segmentId} has no voxels`, 'volume-segmentation-v1');
            bounds[segmentId] = Box3D.create(Vec3.create(extent[0], extent[1], extent[2]), Vec3.create(extent[3], extent[4], extent[5]));
        }
        const segmentationData = { segments, sets, bounds, labels };
        Volume.PickingGranularity.set(volume, 'object');
        Volume.Segmentation.set(volume, segmentationData);
        const activeVolume = plugin.state.data.cells.get(entry.activeRef)?.obj?.data as Volume | undefined;
        if (activeVolume && activeVolume !== volume) {
            Volume.PickingGranularity.set(activeVolume, 'object');
            Volume.Segmentation.set(activeVolume, segmentationData);
        }
        entry.segmentation = segmentation;
        return viewerOk(undefined);
    }

    async setVolumePresentation(state: VolumePresentationStateV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
        const plugin = this.requirePlugin();
        const entry = this.volumes.get(state.volumeId);
        if (!entry) return viewerUnsupported(`Volume ${state.volumeId} is not loaded`, 'volume-ccp4-v1');
        try {
            if (entry.representationRef) {
                await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref: entry.representationRef });
                entry.representationRef = undefined;
            }
            if (!state.visible) return viewerOk(undefined);
            if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
            const volume = plugin.state.data.cells.get(entry.activeRef)?.obj?.data;
            if (!volume) return viewerUnsupported('Loaded volume state is unavailable', 'volume-ccp4-v1');
            const isSegmentation = state.visibleSegmentIds.length > 0;
            if (isSegmentation && !entry.segmentation) return viewerUnsupported('Supplied segmentation metadata has not been applied', 'volume-segmentation-v1');
            const isoValue = Volume.IsoValue.absolute(absoluteContourValue(state, entry.descriptor));
            const typeParams = state.representation === 'slice'
                ? {
                    alpha: state.opacity,
                    isoValue,
                    dimension: {
                        name: state.slice!.axis === 0 ? 'x' as const : state.slice!.axis === 1 ? 'y' as const : 'z' as const,
                        params: state.slice!.index,
                    },
                }
                : { alpha: state.opacity, isoValue };
            const params = isSegmentation
                ? createVolumeRepresentationParams(plugin, volume, {
                    type: 'segment', typeParams: { segments: [...state.visibleSegmentIds] }, color: 'volume-segment',
                })
                : createVolumeRepresentationParams(plugin, volume, {
                    type: state.representation, typeParams, color: 'uniform', colorParams: { value: normalizeColor(state.color) },
                });
            const representation = await plugin.build()
                .to(entry.activeRef)
                .apply(StateTransforms.Representation.VolumeRepresentation3D, params)
                .commit();
            entry.representationRef = representation.ref;
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerOk(undefined);
        } catch (error) {
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerError(error);
        }
    }

    async removeVolume(volumeId: string, signal: AbortSignal): Promise<ViewerResult<void>> {
        const plugin = this.requirePlugin();
        const entry = this.volumes.get(volumeId);
        if (!entry) return viewerOk(undefined);
        try {
            await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref: entry.sourceRef, removeParentGhosts: true });
            this.volumes.delete(volumeId);
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerOk(undefined);
        } catch (error) {
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerError(error);
        }
    }

    async applyVolumeRegistration(registration: VolumeRegistrationV1, signal: AbortSignal): Promise<ViewerResult<void>> {
        if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
        const plugin = this.requirePlugin();
        const entry = this.volumes.get(registration.volumeId);
        if (!entry) return viewerUnsupported(`Volume ${registration.volumeId} is not loaded`, 'volume-segmentation-v1');
        try {
            if (entry.representationRef) {
                await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref: entry.representationRef });
                entry.representationRef = undefined;
            }
            if (entry.transformRef) {
                await PluginCommands.State.RemoveObject(plugin, { state: plugin.state.data, ref: entry.transformRef });
                entry.transformRef = undefined;
                entry.activeRef = entry.sourceRef;
            }
            const supplied = registration.transformRowMajor4x4;
            const matrix = Mat4.ofRows([
                [supplied[0]!, supplied[1]!, supplied[2]!, supplied[3]!],
                [supplied[4]!, supplied[5]!, supplied[6]!, supplied[7]!],
                [supplied[8]!, supplied[9]!, supplied[10]!, supplied[11]!],
                [supplied[12]!, supplied[13]!, supplied[14]!, supplied[15]!],
            ]);
            const transformed = await plugin.build().to(entry.sourceRef).apply(
                StateTransforms.Volume.VolumeTransform,
                { transform: { name: 'matrix', params: { data: matrix, transpose: false } } },
            ).commit();
            entry.transformRef = transformed.ref;
            entry.activeRef = transformed.ref;
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerOk(undefined);
        } catch (error) {
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerError(error);
        }
    }

    async capturePng(signal: AbortSignal): Promise<ViewerResult<Blob>> {
        if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
        const plugin = this.requirePlugin();
        const viewportScreenshot = plugin.helpers.viewportScreenshot;
        if (!viewportScreenshot) return viewerUnsupported('Mol* screenshot helper is unavailable', 'export-png-v1');
        try {
            const dataUri = await viewportScreenshot.getImageDataUri();
            if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
            const response = await fetch(dataUri);
            return viewerOk(await response.blob());
        } catch (error) {
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerError(error);
        }
    }

    async exportSelectionMmcif(signal: AbortSignal): Promise<ViewerResult<Blob>> {
        if (signal.aborted) return viewerCancelled('Mol* operation was cancelled');
        const plugin = this.requirePlugin();
        const root = plugin.managers.structure.hierarchy.current.structures[0]?.cell.obj?.data;
        if (!root) return viewerUnsupported('No structure is loaded for mmCIF export', 'export-mmcif-v1');
        try {
            const selected = plugin.managers.structure.selection.getStructure(root) ?? root;
            const encoded = to_mmCIF('BMS_SELECTION', selected, false, { copyAllCategories: false });
            if (typeof encoded !== 'string') throw new Error('Mol* returned binary mmCIF for a text export request');
            return viewerOk(new Blob([encoded], { type: 'chemical/x-mmcif' }));
        } catch (error) {
            return signal.aborted ? viewerCancelled('Mol* operation was cancelled') : viewerError(error);
        }
    }

    getCanvasElement(): ViewerResult<HTMLCanvasElement> {
        const canvas = this.plugin?.canvas3dContext?.canvas;
        return canvas instanceof HTMLCanvasElement
            ? viewerOk(canvas)
            : viewerUnsupported('Mol* canvas element is unavailable', 'export-webm-v1');
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        this.sceneGeneration += 1;
        this.presentationGeneration += 1;
        this.measurementGeneration += 1;
        this.disposedPlugin = this.plugin;
        this.clickSubscription?.unsubscribe();
        this.clickSubscription = undefined;
        if (this.plugin && this.tooltipProvider) this.plugin.managers.lociLabels.removeProvider(this.tooltipProvider);
        this.tooltipProvider = undefined;
        this.residueClickHandler = undefined;
        this.volumes.clear();
        this.owner.dispose();
        if (this.target) adapterRegistry.delete(this.target);
        this.plugin = undefined;
        this.target = undefined;
    }

    get diagnostics(): MolstarDirectAdapterDiagnostics {
        const disposedPlugin = this.plugin ?? this.disposedPlugin;
        return {
            disposed: this.disposed,
            pluginDisposed: isPluginDisposed(disposedPlugin),
            structureCount: this.plugin?.managers.structure.hierarchy.current.structures.length ?? 0,
            hasCanvas3d: Boolean(this.plugin?.canvas3d),
            completedSceneGeneration: this.completedSceneGeneration,
            measurementCount: this.plugin
                ? this.plugin.managers.structure.measurement.state.distances.length
                    + this.plugin.managers.structure.measurement.state.angles.length
                    + this.plugin.managers.structure.measurement.state.dihedrals.length
                : 0,
        };
    }

    private async setBackground(backgroundColor: string): Promise<void> {
        const plugin = this.requirePlugin();
        await PluginCommands.Canvas3D.SetSettings(plugin, {
            settings: (props) => {
                props.renderer.backgroundColor = Color.fromHexStyle(backgroundColor);
            },
        });
    }

    private async clearColorSelections(plugin: PluginUIContext): Promise<void> {
        for (const structureRef of plugin.managers.structure.hierarchy.current.structures) {
            if (this.hasOverpaint) await clearStructureOverpaint(plugin, structureRef.components);
            if (this.hasTransparency) await clearStructureTransparency(plugin, structureRef.components);
        }
        this.hasOverpaint = false;
        this.hasTransparency = false;
    }

    private async applyColorSelections(
        plugin: PluginUIContext,
        selections: readonly MolstarDirectQuery[],
        nonSelectedColor: MolstarDirectPresentation['nonSelectedColor'],
    ): Promise<void> {
        await this.clearColorSelections(plugin);
        const focusLoci: StructureElement.Loci[] = [];
        for (const structureRef of plugin.managers.structure.hierarchy.current.structures) {
            const structure = structureRef.cell.obj?.data;
            if (!structure) continue;
            const documentId = this.documentStructures.get(structure);
            const documentSelections = selections.filter((selection) => !selection.document_id || selection.document_id === documentId);
            if (documentSelections.length === 0) continue;
            if (nonSelectedColor !== undefined) {
                await setStructureOverpaint(
                    plugin,
                    structureRef.components,
                    normalizeColor(nonSelectedColor),
                    async (root) => queryLoci([{}], root),
                );
                this.hasOverpaint = true;
            }
            for (const selection of documentSelections) {
                if (selection.color !== null) {
                    await setStructureOverpaint(
                        plugin,
                        structureRef.components,
                        normalizeColor(selection.color),
                        async (root) => queryLoci([selection], root),
                    );
                    this.hasOverpaint = true;
                }
                if (selection.opacity !== undefined && selection.opacity < 1) {
                    await setStructureTransparency(
                        plugin,
                        structureRef.components,
                        Math.max(0, Math.min(1, 1 - selection.opacity)),
                        async (root) => queryLoci([selection], root),
                    );
                    this.hasTransparency = true;
                }
                if (selection.focus) focusLoci.push(queryLoci([selection], structure));
            }
        }
        if (focusLoci.length > 0) plugin.managers.camera.focusLoci(focusLoci);
    }

    private async applyHiddenSelections(
        plugin: PluginUIContext,
        selections: readonly MolstarDirectQuery[],
    ): Promise<void> {
        if (selections.length === 0) return;
        for (const structureRef of plugin.managers.structure.hierarchy.current.structures) {
            const structure = structureRef.cell.obj?.data;
            if (!structure) continue;
            const documentId = this.documentStructures.get(structure);
            const documentSelections = selections.filter((selection) => !selection.document_id || selection.document_id === documentId);
            for (const selection of documentSelections) {
                await setStructureTransparency(
                    plugin,
                    structureRef.components,
                    1,
                    async (root) => queryLoci([selection], root),
                );
                this.hasTransparency = true;
            }
        }
    }

    private async applyTooltips(
        plugin: PluginUIContext,
        selections: readonly MolstarDirectQuery[],
    ): Promise<void> {
        if (this.tooltipProvider) plugin.managers.lociLabels.removeProvider(this.tooltipProvider);
        this.tooltipProvider = undefined;

        const entries: Array<{ text: string; loci: StructureElement.Loci }> = [];
        for (const structureRef of plugin.managers.structure.hierarchy.current.structures) {
            const structure = structureRef.cell.obj?.data;
            if (!structure) continue;
            const documentId = this.documentStructures.get(structure);
            const documentSelections = selections.filter((selection) => !selection.document_id || selection.document_id === documentId);
            for (const selection of documentSelections) {
                if (!selection.tooltip) continue;
                entries.push({ text: selection.tooltip, loci: queryLoci([selection], structure) });
            }
        }
        if (entries.length === 0) return;

        const escapeLabel = (value: string) => value
            .replaceAll('&', '&amp;')
            .replaceAll('<', '&lt;')
            .replaceAll('>', '&gt;')
            .replaceAll('"', '&quot;')
            .replaceAll("'", '&#39;');
        const provider: LociLabelProvider = {
            priority: 100,
            label: (loci: Loci) => {
                if (!StructureElement.Loci.is(loci)) return undefined;
                const labels = entries
                    .filter((entry) => StructureElement.Loci.areIntersecting(entry.loci, loci))
                    .map((entry) => escapeLabel(entry.text));
                return labels.length > 0 ? labels.join('<br/>') : undefined;
            },
        };
        plugin.managers.lociLabels.addProvider(provider);
        this.tooltipProvider = provider;
    }

    private requirePlugin(): PluginUIContext {
        if (this.disposed || !this.plugin) throw new MolstarDirectAdapterCancelledError();
        return this.plugin;
    }


    private isSceneCurrent(generation: number): boolean {
        return !this.disposed && generation === this.sceneGeneration;
    }

    private assertSceneCurrent(generation: number): void {
        if (!this.isSceneCurrent(generation)) throw new MolstarDirectAdapterCancelledError();
    }

    private isPresentationCurrent(generation: number): boolean {
        return !this.disposed && generation === this.presentationGeneration;
    }
}
