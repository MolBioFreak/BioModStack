import { QualityAssessmentProvider } from 'molstar/lib/extensions/model-archive/quality-assessment/prop';
import { PLDDTConfidenceColorThemeProvider } from 'molstar/lib/extensions/model-archive/quality-assessment/color/plddt';
import { OrderedSet } from 'molstar/lib/mol-data/int';
import { Unit } from 'molstar/lib/mol-model/structure';
import { PluginBehavior } from 'molstar/lib/mol-plugin/behavior/behavior';
import type { LociLabelProvider } from 'molstar/lib/mol-plugin-state/manager/loci-label';
import { ParamDefinition as PD } from 'molstar/lib/mol-util/param-definition';

interface BmsPLDDTQualityAssessmentParams {
    autoAttach: boolean;
    showTooltip: boolean;
}

/**
 * Plugin-local pLDDT support.
 *
 * Molstar's MAQualityAssessment behavior also mutates the process-global
 * DefaultQueryRuntimeTable. That table is not reference-counted, so concurrent
 * plugins warn on registration and one plugin can remove another plugin's
 * symbols during disposal. BMS only needs the model property, color theme, and
 * tooltip here; selection-query symbols remain deliberately unregistered.
 */
export const BmsPLDDTQualityAssessment = PluginBehavior.create<BmsPLDDTQualityAssessmentParams>({
    name: 'bms-plddt-quality-assessment',
    category: 'custom-props',
    display: {
        name: 'BMS pLDDT Quality Assessment',
        description: 'Plugin-local pLDDT coloring and residue tooltips.',
    },
    ctor: class extends PluginBehavior.Handler<BmsPLDDTQualityAssessmentParams> {
        private readonly labelProvider: LociLabelProvider = {
            label: (loci) => {
                if (!this.params.showTooltip || loci.kind !== 'element-loci' || loci.elements.length === 0) {
                    return undefined;
                }

                const seen = new Set<string>();
                let scoreTotal = 0;
                let scoreCount = 0;
                for (const { indices, unit } of loci.elements) {
                    if (!Unit.isAtomic(unit)) continue;
                    const scores = QualityAssessmentProvider.get(unit.model).value?.pLDDT;
                    if (!scores) continue;
                    const residueIndex = unit.model.atomicHierarchy.residueAtomSegments.index;
                    OrderedSet.forEach(indices, (index) => {
                        const residue = residueIndex[unit.elements[index]];
                        const key = `${unit.id}:${residue}`;
                        if (seen.has(key)) return;
                        seen.add(key);
                        const score = scores.get(residue);
                        if (typeof score === 'number' && Number.isFinite(score)) {
                            scoreTotal += score;
                            scoreCount += 1;
                        }
                    });
                }

                if (scoreCount === 0) return undefined;
                const average = scoreTotal / scoreCount;
                const countLabel = scoreCount === 1 ? 'Residue' : `${scoreCount} Residues avg.`;
                return `pLDDT Score <small>(${countLabel})</small>: ${average.toFixed(2)}`;
            },
        };

        register(): void {
            this.ctx.customModelProperties.register(QualityAssessmentProvider, this.params.autoAttach);
            this.ctx.managers.lociLabels.addProvider(this.labelProvider);
            this.ctx.representation.structure.themes.colorThemeRegistry.add(PLDDTConfidenceColorThemeProvider);
        }

        update(params: BmsPLDDTQualityAssessmentParams): boolean {
            const changed = this.params.autoAttach !== params.autoAttach
                || this.params.showTooltip !== params.showTooltip;
            this.params = params;
            this.ctx.customModelProperties.setDefaultAutoAttach(
                QualityAssessmentProvider.descriptor.name,
                params.autoAttach,
            );
            return changed;
        }

        unregister(): void {
            this.ctx.customModelProperties.unregister(QualityAssessmentProvider.descriptor.name);
            this.ctx.managers.lociLabels.removeProvider(this.labelProvider);
            this.ctx.representation.structure.themes.colorThemeRegistry.remove(PLDDTConfidenceColorThemeProvider);
        }
    },
    params: () => ({
        autoAttach: PD.Boolean(true),
        showTooltip: PD.Boolean(true),
    }),
});
