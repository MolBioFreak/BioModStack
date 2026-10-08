/** Transport-only adapter for existing full-catalog test fixtures.
 * Native split/projection equivalence is tested against the actual capture in
 * test_debloat_bms_catalog_split; this helper makes no bandwidth claims.
 */
export function catalogWireFixture(full: any, view: unknown) {
    if (view !== 'metadata' && view !== 'assessment') throw new Error(`Unexpected catalog fixture view: ${view}`);
    const project = (catalog: any, revision: string) => {
        const { actions = [], canonical: _canonical, ...live } = catalog;
        const body = view === 'metadata'
            ? { catalog_view: view, metadata_revision: revision, actions: actions.map((action: any) => ({ action_id: action.action_id })) }
            : { ...live, catalog_view: view, metadata_revision: revision,
                action_states: actions, action_state_indices: actions.map((_: any, index: number) => index) };
        // Semantic fixtures send a complete initial/resync envelope, not native
        // recurring byte evidence. Preserve every fixture field and mutation.
        return view === 'metadata' ? body : {
            catalog_view: view, assessment_revision: revision,
            assessment_base: body, assessment_changes: [],
        };
    };
    return { ...project(full, 'fixture-v1'), canonical: project(full.canonical ?? { actions: [] }, 'fixture-v2') };
}
