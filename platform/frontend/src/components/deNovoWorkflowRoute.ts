// Navigation metadata only. Native model owners still compile scientific requests.
export const DE_NOVO_TEMPLATE = 'protein_modification_experimental';
const DE_NOVO_MODELS = new Set([DE_NOVO_TEMPLATE, 'protein_local_redesign', 'protein_cad_experimental']);
export const DE_NOVO_ROUTE_KEYS = ['modification_mode', 'generator', 'design_task'] as const;
export interface DeNovoNavigationState {
    modification_mode: string;
    generator?: string;
    design_task?: string;
}
export const isDeNovoModel = (value: unknown): boolean => typeof value === 'string' && DE_NOVO_MODELS.has(value);

export function deNovoSavedValues(
    model: unknown,
    mode: unknown,
    values: Record<string, unknown> = {},
): Record<string, unknown> {
    // Preserve the validated-pipeline identity; it is not interchangeable with
    // the native iteration route even though both share a visual workspace.
    const modificationMode = values.modification_mode ?? (
        mode === 'shape_blueprint' ? 'shape_blueprint'
            : mode === 'region_redesign' ? 'region_redesign'
                : model === 'protein_local_redesign' || values.template_model_id === 'protein_local_redesign'
                    ? 'rfd3_local_redesign' : 'de_novo_design'
    );
    return {
        ...values,
        modification_mode: modificationMode,
        ...(values.generator === undefined && typeof values.backend === 'string' ? { generator: values.backend } : {}),
    };
}

export function deNovoRouteValues(params: URLSearchParams): Record<string, unknown> {
    const model = params.get('model') ?? params.get('template');
    const values: Record<string, unknown> = {};
    for (const key of DE_NOVO_ROUTE_KEYS) {
        if (params.has(key)) values[key] = params.get(key);
    }
    return deNovoSavedValues(model, params.get('mode'), values);
}

export function deNovoNavigation(values: Record<string, unknown>): DeNovoNavigationState {
    return {
        modification_mode: typeof values.modification_mode === 'string' ? values.modification_mode : 'de_novo_design',
        ...(typeof values.generator === 'string' ? { generator: values.generator }
            : typeof values.backend === 'string' ? { generator: values.backend } : {}),
        ...(typeof values.design_task === 'string' ? { design_task: values.design_task } : {}),
    };
}

export function deNovoSearch(params: URLSearchParams, state: DeNovoNavigationState): URLSearchParams {
    const next = new URLSearchParams(params);
    next.set('template', DE_NOVO_TEMPLATE);
    // Retain destination/context keys, but retire the conflicting manual editor route.
    next.delete('model'); next.delete('mode'); next.delete('engine');
    for (const key of DE_NOVO_ROUTE_KEYS) {
        const value = state[key];
        if (value !== undefined) next.set(key, value);
        else next.delete(key);
    }
    return next;
}
