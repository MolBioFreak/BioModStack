import { CustomTooltipsProvider } from 'molstar/lib/extensions/mvs/components/custom-tooltips-prop';
import {
    Queries,
    StructureElement,
    StructureProperties,
    StructureSelection,
} from 'molstar/lib/mol-model/structure';
import type { Structure } from 'molstar/lib/mol-model/structure';
import { StructureQuery } from 'molstar/lib/mol-model/structure/query/query';
import {
    clearStructureOverpaint,
    setStructureOverpaint,
} from 'molstar/lib/mol-plugin-state/helpers/structure-overpaint';
import { clearStructureTransparency, setStructureTransparency } from 'molstar/lib/mol-plugin-state/helpers/structure-transparency';
import { StateTransforms } from 'molstar/lib/mol-plugin-state/transforms';
import { Asset } from 'molstar/lib/mol-util/assets';
import { Color } from 'molstar/lib/mol-util/color/color';
import { Vec3 } from 'molstar/lib/mol-math/linear-algebra';
import { PluginCommands } from 'molstar/lib/mol-plugin/commands';
import type { PluginUIContext } from 'molstar/lib/mol-plugin-ui/context';
import { StateSelection } from 'molstar/lib/mol-state';

import { createDirectMolstarEngineOwner } from '../runtime/createDirectMolstarEngineOwner';
import type { MolstarEngineOwner } from '../runtime/MolstarEngineOwner';
import { assessMeasurement, type ViewerMeasurement } from '../contracts/measurements';
import type { StructureCameraState } from '../contracts/scenePresentation';
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
    readonly color?: string | number | { r: number; g: number; b: number };
    readonly focus?: boolean;
    readonly tooltip?: string;
    readonly opacity?: number;
}

export interface MolstarDirectPresentation {
    readonly colorSelections?: readonly MolstarDirectQuery[];
    readonly nonSelectedColor?: string | number | { r: number; g: number; b: number };
    readonly tooltipSelections?: readonly MolstarDirectQuery[];
}

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
            ...((param.atoms || param.auth_atoms || param.atom_id || param.alt_loc_id !== undefined) ? {
                atomTest: (location) => (
                    (!param.atoms
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

export interface MolstarDirectProbe { readonly diagnostics: MolstarDirectAdapterDiagnostics; }
export const getMolstarDirectProbeForElement = (element: HTMLElement): MolstarDirectProbe | undefined => {
    const adapter = adapterRegistry.get(element);
    return adapter ? { get diagnostics() { return adapter.diagnostics; } } : undefined;
};

export interface MolstarDirectAdapterOptions {
    readonly hideControls?: boolean;
    readonly alphafoldView?: boolean;
    readonly backgroundColor?: string;
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
    private plugin: PluginUIContext | undefined;
    private disposedPlugin: PluginUIContext | undefined;
    private target: HTMLElement | undefined;
    private sceneGeneration = 0;
    private completedSceneGeneration = 0;
    private presentationGeneration = 0;
    private disposed = false;
    private hasSelection = false;
    private hasTooltips = false;
    private residueClickHandler: ((residue: MolstarDirectResidueClick) => void) | undefined;
    private clickSubscription: { unsubscribe(): void } | undefined;
    private documentStructures = new WeakMap<Structure, string>();
    private measurementSelectionRefs: string[] = [];
    private measurementGeneration = 0;
    private sceneQueue: Promise<void> = Promise.resolve();
    private presentationQueue: Promise<void> = Promise.resolve();
    private measurementQueue: Promise<void> = Promise.resolve();

    constructor({
        hideControls = true,
        alphafoldView = false,
        backgroundColor = '#0f172a',
    }: MolstarDirectAdapterOptions = {}) {
        this.backgroundColor = backgroundColor;
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

    loadScene(documents: readonly MolstarDirectDocument[]): Promise<void> {
        this.requirePlugin();
        const generation = ++this.sceneGeneration;
        this.presentationGeneration += 1;
        this.measurementGeneration += 1;
        this.hasSelection = false;
        this.hasTooltips = false;

        const task = this.sceneQueue.then(async () => {
            if (!this.isSceneCurrent(generation)) throw new MolstarDirectAdapterCancelledError();
            const plugin = this.requirePlugin();
            try {
                await plugin.clear();
                this.assertSceneCurrent(generation);
                this.documentStructures = new WeakMap<Structure, string>();
                this.measurementSelectionRefs = [];

                for (const document of documents) {
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
                        representationPreset: 'auto',
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

    applyPresentation(presentation: MolstarDirectPresentation): Promise<void> {
        const generation = ++this.presentationGeneration;
        const task = this.presentationQueue.then(async () => {
            if (!this.isPresentationCurrent(generation)) return;
            const plugin = this.requirePlugin();
            const colors = presentation.colorSelections ?? [];
            const tooltips = presentation.tooltipSelections ?? [];

            if (colors.length > 0) {
                await this.applyColorSelections(plugin, colors, presentation.nonSelectedColor);
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

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        this.sceneGeneration += 1;
        this.presentationGeneration += 1;
        this.measurementGeneration += 1;
        this.disposedPlugin = this.plugin;
        this.clickSubscription?.unsubscribe();
        this.clickSubscription = undefined;
        this.residueClickHandler = undefined;
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
            await clearStructureOverpaint(plugin, structureRef.components);
            await clearStructureTransparency(plugin, structureRef.components);
        }
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
            }
            for (const selection of documentSelections) {
                if (selection.color !== null) {
                    await setStructureOverpaint(
                        plugin,
                        structureRef.components,
                        normalizeColor(selection.color),
                        async (root) => queryLoci([selection], root),
                    );
                }
                if (selection.opacity !== undefined && selection.opacity < 1) {
                    await setStructureTransparency(
                        plugin,
                        structureRef.components,
                        Math.max(0, Math.min(1, 1 - selection.opacity)),
                        async (root) => queryLoci([selection], root),
                    );
                }
                if (selection.focus) focusLoci.push(queryLoci([selection], structure));
            }
        }
        if (focusLoci.length > 0) plugin.managers.camera.focusLoci(focusLoci);
    }

    private async applyTooltips(
        plugin: PluginUIContext,
        selections: readonly MolstarDirectQuery[],
    ): Promise<void> {
        for (const structureRef of plugin.managers.structure.hierarchy.current.structures) {
            const structure = structureRef.cell.obj?.data;
            if (!structure) continue;
            const documentId = this.documentStructures.get(structure);
            const documentSelections = selections.filter((selection) => !selection.document_id || selection.document_id === documentId);
            const customTooltipProps = {
                tooltips: documentSelections.map((selection) => ({
                    text: selection.tooltip ?? '',
                    selector: {
                        name: 'bundle' as const,
                        params: StructureElement.Bundle.fromLoci(queryLoci([selection], structure)),
                    },
                })),
            };
            const structureTransformRef = structureRef.cell.transform.ref;
            let propertyCells = plugin.state.data.select(
                StateSelection.Generators.ofTransformer(
                    StateTransforms.Model.CustomStructureProperties,
                    structureTransformRef,
                ),
            );
            if (propertyCells.length === 0) {
                await plugin.build()
                    .to(structureTransformRef)
                    .apply(StateTransforms.Model.CustomStructureProperties)
                    .commit();
                propertyCells = plugin.state.data.select(
                    StateSelection.Generators.ofTransformer(
                        StateTransforms.Model.CustomStructureProperties,
                        structureTransformRef,
                    ),
                );
            }
            const propertyCell = propertyCells[0];
            if (!propertyCell) continue;
            await plugin.build().to(propertyCell).update((old) => ({
                properties: {
                    ...old.properties,
                    [CustomTooltipsProvider.descriptor.name]: customTooltipProps,
                },
                autoAttach: old.autoAttach.includes(CustomTooltipsProvider.descriptor.name)
                    ? old.autoAttach
                    : [...old.autoAttach, CustomTooltipsProvider.descriptor.name],
            })).commit();
        }
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
