import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { bioXpErrorText, type BioXpOperatorJsonSchema } from '../lib/bioxpClient';
import { methodsGet, type MethodCatalog, type MethodValue } from '../lib/bioxpMethods';
import { MethodFields, object } from './BioXpMethodFields';

const families = ['cavro_application', 'pipette_settings', 'fluid_search', 'pressure_stream', 'tip_pickup', 'tip_eject', 'profile_pickup', 'calibration'];
/** Native discovery is the schema authority. Never imply unadvertised firmware support. */
export function BioXpPipetteNativeTools({ visible, enabled, run }: {
    visible: boolean; enabled: boolean; run: (action: string, inputs: MethodValue) => void;
}) {
    const [action, setAction] = useState('');
    const [drafts, setDrafts] = useState<Record<string, MethodValue>>({});
    const catalog = useQuery({ queryKey: ['bioxp', 'methods', 'catalog'], queryFn: () => methodsGet<MethodCatalog>('catalog'), enabled: visible, retry: false, staleTime: Infinity });
    const actions = catalog.data?.actions?.filter(row => families.includes(String(row.action ?? row.id))) ?? [];
    const selected = actions.find(row => (row.action ?? row.id) === action);
    const source = selected?.input_schema ?? selected?.inputs;
    const schema = source ? { ...source, $defs: { ...object(catalog.data?.native_definitions) as Record<string, BioXpOperatorJsonSchema>, ...source.$defs } } : undefined;
    return <section hidden={!visible} aria-label="Native device and calibration tools" className="space-y-3 min-w-0">
        <h4>Device, service, geometry & calibration</h4>
        <p>One explicit native action through the existing protocol owner. Device programs are device-buffer operations, not a second workflow editor. Shared head motion affects all mounted tips. Reports and command completion are not measured delivery.</p>
        <label>Native tool<select aria-label="Native tool" value={action} onChange={event => setAction(event.target.value)}><option value="">Choose a published tool</option>{actions.map(row => <option key={String(row.action ?? row.id)} value={String(row.action ?? row.id)}>{String(row.label ?? row.action ?? row.id).replaceAll('_', ' ')}</option>)}</select></label>
        {catalog.isLoading && <p>Reading native tool definitions…</p>}
        {catalog.error && <p role="alert">{bioXpErrorText(catalog.error)}</p>}
        {selected && schema && <>
            <MethodFields label={action} schema={schema} value={drafts[action] ?? {}} onChange={value => setDrafts(previous => ({ ...previous, [action]: object(value) }))} />
            <button type="button" disabled={!enabled} onClick={() => run(action, structuredClone(drafts[action] ?? {}))}>Run selected native action</button>
        </>}
        {selected && !schema && <p>The published tool has no typed input schema; its fields cannot be rendered here.</p>}
    </section>;
}
