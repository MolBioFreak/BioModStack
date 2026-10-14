/** Producer-declared, document-scoped roles only. Never infer from chain letters,
 * lengths, filenames, the Design's other conformation or a first selected row. */
export interface DeclaredDocumentRoles { binder_chains?: string | string[] | null; target_chains?: string | string[] | null }
const chains = (value: unknown): string[] | undefined => typeof value === 'string'
    ? value.split(',').map(chain => chain.trim()).filter(Boolean)
    : Array.isArray(value) && value.every(chain => typeof chain === 'string') ? value : undefined;
export function declaredBinderRoleDefaults(documents: Array<DeclaredDocumentRoles | undefined>, fields: Array<{ name: string; type?: string }>): Record<string, string | string[]> {
    const result: Record<string, string | string[]> = {};
    if (!documents.length) return result;
    for (const [role, keys] of [['binder_chains', ['binder_chains', 'design_chain']], ['target_chains', ['target_chains', 'target_chain']]] as const) {
        const values = documents.map(document => chains(document?.[role]));
        const first = values[0];
        if (!first || values.some(value => value === undefined || JSON.stringify(value) !== JSON.stringify(first))) continue;
        for (const field of fields) if ((keys as readonly string[]).includes(field.name)) result[field.name] = field.type === 'array' ? [...first] : first.join(',');
    }
    return result;
}
