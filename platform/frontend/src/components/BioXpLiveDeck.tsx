import React, { useRef, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { api } from '../lib/api';
import { bioXpErrorText, useBioXpOperatorUpdates, useBioXpTipTrays, type BioXpDeckDestinationV1, type BioXpOperatorDashboardV2 } from '../lib/bioxpClient';
import { readCalibrationSettings } from '../lib/bioxpCalibration';
import { methodsGet } from '../lib/bioxpMethods';
import { deckResources, deckSize, deckStations, projectDeckPoint, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import { activeDeckResources, alignmentReference, gunCenters, poseFromAxisObservations, retainAxisObservations } from '../lib/bioxpLiveDeck';
import { routeLetter, type DeckContents, type LiveRouteStop, type PlateGeometry } from '../lib/bioxpLiveRoute';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import { BioXpLiveDeckPipette } from './BioXpLiveDeckPipette';
import './BioXpMethodsWorkspace.css';
import './BioXpRobotControls.css';
import './BioXpLiveDeck.css';

export type BioXpLiveDeckProps = {
    generation: number;
    connected: boolean;
    visible: boolean;
    dashboard: BioXpOperatorDashboardV2 | null | undefined;
    stale: boolean;
    selection: BioXpDeckSelection;
    selectedDestination?: BioXpDeckDestinationV1 | null;
    /** Current robot destination catalog; holder rows/wells with native XY are drawn on the map. */
    destinations?: BioXpDeckDestinationV1[];
    cameraControls?: React.ReactNode;
    onMoveToStation: (target: string) => void;
    onMoveToWell: (locationId: number, well: string) => void;
    onBlockedMove?: (intent: { station: string } | { locationId: number; well: string }) => void;
    preparation?: React.ReactNode;
    stationDisabledReason: (target: string) => string | null;
    wellDisabledReason: string | null;
    doorControls?: React.ReactNode;
    /** Chillers and thermal cycler card (includes the door controls when given). */
    thermalControls?: React.ReactNode;
    movementControls: React.ReactNode;
    commandDetails: React.ReactNode;
    commandDetailsOpen?: boolean;
    onCommandDetailsToggle?: (open: boolean) => void;
    transferControls: React.ReactNode | ((visible: boolean) => React.ReactNode);
    /** Deck record panel; record-only. */
    deckRecord?: React.ReactNode;
    /** Recorded tip occupancy by tray 1-4; shades tip racks only. */
    tipOccupancy?: Record<number, boolean[]>;
};

type PlateProfiles = { plates?: Record<string, { name?: string }>; stations?: Record<string, string>; object_locations?: Record<string, string> };
type LibraryPlate = { id: string; name?: string; short_name?: string; vessel_geometry?: { height_to_volume?: number[][] | null; calibrated_low_gap_mm?: number | null } };
type Popup = { kind: 'module'; station: string; left: number; top: number } | { kind: 'well'; station: string; locationId: number; well: string; left: number; top: number };
const CONTENTS_URL = '/api/bioxp/operator-controls/deck/contents';
const FIT = { x: 85, y: 30, width: 1075, height: 1070 };
// Plate (gripper) station of each pipetting station, as the robot names them.
const GRIP_STATION: Record<string, string> = { LOC_P_MS: 'LOC_MS', LOC_P_TC: 'LOC_TC', LOC_P_OC: 'LOC_OC', LOC_P_RC: 'LOC_RC' };
const PLATE_STATIONS = deckResources.filter(resource => resource.kind === 'plate');
const stationLabel = (id: string) => deckStations.find(station => station.id === id)?.label ?? id;

export function BioXpLiveDeck(props: BioXpLiveDeckProps) {
    const { generation, connected, visible, dashboard, selection } = props;
    const client = useQueryClient();
    const updates = useBioXpOperatorUpdates(generation, connected && visible);
    const revision = dashboard?.deck?.position_table_revision ?? null;
    const geometry = useQuery({
        queryKey: ['bioxp-live-deck-geometry', generation, revision],
        queryFn: async () => ({ generation, settings: await readCalibrationSettings(generation) }),
        enabled: visible && connected, staleTime: Infinity, retry: false,
        refetchOnWindowFocus: false, refetchOnReconnect: false,
        placeholderData: previous => previous?.generation === generation ? previous : undefined,
    });
    // Plate type per station lives in the robot's plate record; liquid amounts are a saved BMS record.
    const profilesKey = ['bioxp', 'plate-profiles', generation];
    const profiles = useQuery({ queryKey: profilesKey, enabled: connected && visible && generation > 0, retry: false, refetchOnWindowFocus: false,
        queryFn: async () => (await api.get<PlateProfiles>('/api/bioxp/plate-profiles', { params: { expected_connection_generation: generation } })).data });
    const library = useQuery({ queryKey: ['bioxp', 'methods', 'plates'], queryFn: () => methodsGet<{ profiles?: LibraryPlate[] }>('plates'), enabled: visible, retry: false, staleTime: Infinity });
    const contentsQuery = useQuery({ queryKey: ['bioxp', 'deck-contents'], enabled: visible, retry: false, refetchOnWindowFocus: false,
        queryFn: async () => (await api.get<{ wells?: DeckContents }>(CONTENTS_URL)).data });
    const tipTrays = useBioXpTipTrays(generation, connected && visible);
    const trays = Array.isArray(tipTrays.data?.trays) ? tipTrays.data : undefined;
    const contents: DeckContents = contentsQuery.data?.wells && typeof contentsQuery.data.wells === 'object' ? contentsQuery.data.wells : {};
    const plateAt: Record<string, string> = profiles.data?.stations && typeof profiles.data.stations === 'object' ? profiles.data.stations : {};
    const plates = Array.isArray(library.data?.profiles) ? library.data!.profiles! : [];
    const plateName = (id: string) => plates.find(plate => plate.id === id)?.short_name ?? plates.find(plate => plate.id === id)?.name ?? profiles.data?.plates?.[id]?.name ?? id;
    const plateGeometry: Record<string, PlateGeometry> = Object.fromEntries(plates.map(plate => [plate.id,
        { table: plate.vessel_geometry?.height_to_volume ?? null, gap: plate.vessel_geometry?.calibrated_low_gap_mm ?? null }]));

    const [mode, setMode] = useState<'move' | 'pipette'>('move');
    const [route, setRoute] = useState<LiveRouteStop[]>([]);
    const [popup, setPopup] = useState<Popup | null>(null);
    const [draft, setDraft] = useState('');
    const [saving, setSaving] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    const [confirmClear, setConfirmClear] = useState(false);
    const pane = useRef<HTMLDivElement>(null);
    const anchor = (selector: string) => {
        const host = pane.current, target = host?.querySelector(selector);
        if (!host || !target) return { left: 16, top: 60 };
        const box = host.getBoundingClientRect(), at = target.getBoundingClientRect();
        return { left: Math.max(8, Math.min(at.left - box.left - 40, box.width - 290)), top: Math.max(8, at.bottom - box.top + 8) };
    };
    const save = async (work: () => Promise<unknown>, done?: () => void) => {
        if (saving) return;
        setSaving(true); setNotice(null);
        try { await work(); done?.(); } catch (cause) { setNotice(bioXpErrorText(cause)); } finally { setSaving(false); }
    };
    const refreshRecords = () => Promise.all([client.invalidateQueries({ queryKey: ['bioxp', 'deck-contents'] }), client.invalidateQueries({ queryKey: profilesKey })]);
    const putStation = (location: string, plate_id: string | null) => api.put('/api/bioxp/plate-profiles/station', { expected_connection_generation: generation, location, plate_id });
    const savePlate = (station: string, plateId: string) => save(async () => {
        await putStation(station, plateId || null);
        // The output plate carries its type with it: record it as loaded so the type follows when the robot moves the plate.
        const outputAt = profiles.data?.object_locations?.OUTPUT_PLATE;
        if (plateId && outputAt && GRIP_STATION[outputAt] === station)
            await api.put('/api/bioxp/plate-profiles/loaded', { expected_connection_generation: generation, movable_object: 'OUTPUT_PLATE', plate_id: plateId });
        await refreshRecords();
    }, () => setPopup(null));
    const saveWell = (station: string, well: string, text: string) => save(async () => {
        const value = text.trim() === '' ? null : Number(text);
        if (value !== null && !(value >= 0)) throw new Error('Enter the amount in µL.');
        await api.put(CONTENTS_URL, { station, wells: { [well]: value } });
        await refreshRecords();
    });
    const clearModule = (station: string) => save(async () => {
        await api.delete(CONTENTS_URL, { params: { station } });
        if (connected && plateAt[station]) await putStation(station, null);
        setRoute(current => current.filter(stop => stop.station !== station));
        await refreshRecords();
    }, () => setPopup(null));
    const clearDeck = () => save(async () => {
        await api.delete(CONTENTS_URL);
        if (connected) for (const station of PLATE_STATIONS) if (plateAt[station.id]) await putStation(station.id, null);
        setRoute([]);
        await refreshRecords();
    }, () => { setConfirmClear(false); setPopup(null); });
    const refreshDeck = () => { void refreshRecords(); void tipTrays.refetch(); void geometry.refetch(); };
    const openWell = (locationId: number, well: string) => {
        const resource = deckResources.find(item => item.locationId === locationId);
        if (!resource || resource.kind === 'rack') {
            if (!props.wellDisabledReason) props.onMoveToWell(locationId, well); else props.onBlockedMove?.({ locationId, well });
            return;
        }
        const known = contents[resource.id]?.[well];
        setDraft(known === undefined ? '' : String(known)); setNotice(null);
        setPopup({ kind: 'well', station: resource.id, locationId, well, ...anchor(`[data-station="${resource.id}"][data-well="${well}"]`) });
    };
    const openModule = (station: string) => {
        setDraft(plateAt[station] ?? ''); setNotice(null);
        setPopup({ kind: 'module', station, ...anchor(`g[data-resource="${station}"] rect.bwd-body`) });
    };
    const addStop = (action: LiveRouteStop['action']) => {
        if (popup?.kind !== 'well') return;
        setRoute(current => [...current, { station: popup.station, locationId: popup.locationId, well: popup.well, action, volume: '' }]);
        setPopup(null);
    };
    const routeFinished = (changed: Record<string, Record<string, number>>) => {
        const stations = Object.entries(changed);
        if (!stations.length) return;
        void Promise.all(stations.map(([station, wells]) => api.put(CONTENTS_URL, { station, wells }))).then(refreshRecords, cause => setNotice(bioXpErrorText(cause)));
    };

    const settings = geometry.data?.generation === generation ? geometry.data.settings : undefined;
    const { resources, calibrated } = activeDeckResources(settings);
    const destination = props.selectedDestination;
    // Selection is display-only; preserve the exact native target for submission.
    const holderTargets = (props.destinations ?? []).filter(option => option.xy != null);
    const selectedStation = destination && destination.xy == null ? deckStations.find(station => station.id === destination.target
        || destination.aliases.includes(station.id) || station.locationId === destination.location_id) : undefined;
    const selectedCoordinates = destination?.xy ?? (!selectedStation && destination
        ? settings?.active_motion_positions?.find(row => row.location_id === destination.target)?.base_coordinates : undefined);
    const selectedPoint = selectedCoordinates && Number.isFinite(selectedCoordinates.x) && Number.isFinite(selectedCoordinates.y)
        ? projectDeckPoint(selectedCoordinates.x, selectedCoordinates.y) : null;
    const [observation, setObservation] = useState(() => ({ generation, update: updates.data,
        axes: retainAxisObservations(null, updates.data, generation) }));
    let axes = observation.axes;
    if (observation.generation !== generation || observation.update !== updates.data) {
        axes = retainAxisObservations(axes, updates.data, generation);
        setObservation({ generation, update: updates.data, axes });
    }
    const pose = poseFromAxisObservations(axes);
    // Readbacks are intermittent, not a continuous physical-position guarantee.
    const lastKnown = true;
    const alignment = dashboard?.deck?.head_alignment;
    const frame = settings?.active_motion_positions?.find(row => row.location_id === dashboard?.deck?.current_location);
    const reference = pose && dashboard?.ownership_generation === pose.ownershipGeneration
        ? alignmentReference(pose.x, pose.y, alignment?.tip_location, frame) : null;
    const point = pose ? projectDeckPoint(pose.x, pose.y) : null;
    const guns = pose ? gunCenters(pose.x, pose.y) : [];
    const outside = point && (point[0] < 0 || point[0] > deckSize.width || point[1] < 0 || point[1] > deckSize.height);
    const geometryLabel = !settings ? 'Reference layout' : geometry.isError || geometry.isPlaceholderData ? 'Last known active geometry' : calibrated.length === deckResources.length ? 'Active motion geometry' : 'Active geometry · partial reference layout';
    const tipLabel = alignment?.tip_location == null ? 'Unavailable' : alignment.tip_location === -1 ? '−1 · no compensation' : alignment.tip_location >= 0 && alignment.tip_location <= 3 ? `Channel ${alignment.tip_location + 1} (source ${alignment.tip_location})` : `Unsupported source reference ${alignment.tip_location}`;
    const time = pose?.observedAt != null ? new Date(pose.observedAt * 1000).toLocaleString() : 'Observation time unavailable';
    const axisSteps = (name: string) => axes?.axes.find(axis => axis.axis === name)?.position_steps ?? 'Unavailable';
    const wellPoint = (stop: LiveRouteStop) => resources.find(resource => resource.id === stop.station)?.points.find(item => item.well === stop.well);
    const routePoints = route.map(wellPoint);
    const contentLabels = Object.fromEntries(Object.entries(contents).map(([station, wells]) => [station,
        Object.fromEntries(Object.entries(wells).map(([well, volume]) => [well, `${volume} µL recorded`]))]));
    const stopLabel = (stop: LiveRouteStop) => `${stationLabel(stop.station)} ${stop.well}`;
    const pipetting = mode === 'pipette';
    return <section className="bioxp-live-deck" data-testid="oem-deck-movement" aria-label="Live deck">
        <header className="bld-heading"><div><h2>Live deck</h2><p>{pipetting ? 'Click a well to record its liquid or add it to the route. Click a plate to set its type.' : 'Click a well or a Move button to move the head. Click a plate to set its type.'}</p></div><span className="bld-status">{connected ? 'Connected' : 'Disconnected'} · {geometryLabel}</span></header>
        <div className="bld-workspace">
            <div className="bld-map-pane bioxp-method-map" ref={pane}>
                <BioXpWorkflowDeck canvas fit={FIT} selection={selection} onChange={() => { /* Authoring selection is not a live intent. */ }}
                    resources={resources} tipOccupancy={props.tipOccupancy} holderTargets={holderTargets} contents={contentLabels}
                    live={{ onStation: props.onMoveToStation, onWell: pipetting ? openWell : props.onMoveToWell, selectedStation: selectedStation?.id,
                        stationDisabledReason: props.stationDisabledReason, wellDisabledReason: pipetting ? null : props.wellDisabledReason, onBlocked: props.onBlockedMove, onModule: openModule }}
                    navigationLead={<span className="bld-click-mode" role="group" aria-label="Deck click action">Click does
                        <button type="button" aria-pressed={!pipetting} onClick={() => { setMode('move'); setPopup(null); }}>Move head</button>
                        <button type="button" aria-pressed={pipetting} onClick={() => { setMode('pipette'); setPopup(null); }}>Pipette</button>
                        <button type="button" onClick={refreshDeck} title="Re-read plate types, liquid amounts, tips and geometry.">Refresh deck</button>
                        {confirmClear ? <><span>Clear all plate types and liquid amounts?</span>
                            <button type="button" className="bld-danger" disabled={saving} onClick={() => void clearDeck()}>Yes, clear</button>
                            <button type="button" onClick={() => setConfirmClear(false)}>Cancel</button></>
                            : <button type="button" onClick={() => setConfirmClear(true)} title="Clears every recorded plate type and liquid amount. Nothing moves.">Clear deck</button>}
                    </span>}
                    overlay={<>
                        <g className="bld-plate-types" aria-label="Plate types">{PLATE_STATIONS.map(station => <text key={station.id} data-plate-type={station.id}
                            className={plateAt[station.id] ? 'is-set' : undefined} x={station.bounds.x - 19} y={station.bounds.y + station.bounds.height + 40}>
                            {plateAt[station.id] ? `Plate: ${plateName(plateAt[station.id])}` : 'No plate type · click to set'}</text>)}</g>
                        {route.length > 0 && <g className="bld-route-overlay" aria-label="Pipetting route">
                            <polyline points={routePoints.filter(Boolean).map(item => `${item!.x},${item!.y}`).join(' ')} />
                            {routePoints.map((item, index) => item && <g key={index}><circle cx={item.x} cy={item.y} r="8.5" /><text x={item.x} y={item.y + 4} textAnchor="middle">{routeLetter(index)}</text></g>)}
                        </g>}
                        {selectedPoint && <g className="bld-chosen-point" data-selected-destination={destination?.target}
                            aria-label={`Selected destination: ${destination?.label}`}>
                            <circle cx={selectedPoint[0]} cy={selectedPoint[1]} r="14" />
                            <title>{destination?.label} · selected native destination anchor</title>
                        </g>}{pose && point ? <g className={`bld-pose${lastKnown ? ' is-last-known' : ''}`} data-testid="reported-gantry" aria-label={lastKnown ? 'Last known gantry position' : 'Reported gantry position'}>
                            <title>Reported gantry; four schematic heads with the beam behind them, not measured outlines or tip-presence observations</title>
                            {/* The beam runs behind the heads: on the far side from the deck front. */}
                            <rect data-testid="vertical-deck-arm" className="bld-rail" x={point[0] + 40} y="40" width="18" height={deckSize.height - 60} rx="4" />
                            <rect className="bld-carriage" x={point[0] + 34} y={guns[0].point[1] - 24} width="30" height={guns[3].point[1] - guns[0].point[1] + 48} rx="5" />
                            {guns.map(({ channel, point: [x, y] }) => <g key={channel} data-gun-module={channel} transform={`translate(${x} ${y})`}>
                                <rect className="bld-gun-bracket" x="14" y="-5" width="22" height="10" />
                                <rect className="bld-gun-housing" x="-15" y="-12" width="30" height="24" rx="5" />
                                <rect className="bld-gun-motor" x="36" y="-7" width="8" height="14" rx="2" />
                                <circle className="bld-gun-axis" cx="0" cy="0" r="5" />
                                <text className="bld-gun-number" x="49" y="4" textAnchor="middle">{channel + 1}</text>
                            </g>)}
                            <path className="bld-gantry-anchor" d={`M ${point[0] - 7} ${point[1]} H ${point[0] + 7} M ${point[0]} ${point[1] - 7} V ${point[1] + 7}`} />
                            {reference && <g data-testid="alignment-reference"><circle className="bld-alignment" cx={reference[0]} cy={reference[1]} r="9" /><title>Published alignment-reference projection, not measured tip contact</title></g>}
                        </g> : null}</>} />
                {popup?.kind === 'module' && <div role="dialog" aria-label={`${stationLabel(popup.station)} plate type`} className="bx-ui bld-popup" style={{ left: popup.left, top: popup.top }}>
                    <button type="button" className="bld-popup-close" aria-label="Close" onClick={() => setPopup(null)}>×</button>
                    <b>{stationLabel(popup.station)} · plate</b>
                    <div className="bld-popup-row"><select aria-label="Plate type" value={draft} onChange={event => setDraft(event.target.value)}>
                        <option value="">No plate type</option>
                        {draft && !plates.some(plate => plate.id === draft) && <option value={draft}>{plateName(draft)}</option>}
                        {plates.map(plate => <option key={plate.id} value={plate.id}>{plate.short_name ?? plate.name ?? plate.id}</option>)}</select>
                        <button type="button" className="bx-go" disabled={!connected || saving} title={connected ? undefined : 'Connect to the robot first.'} onClick={() => void savePlate(popup.station, draft)}>Save</button></div>
                    <small>Set once per location. Follows the plate when the robot moves it.</small>
                    <div className="bld-popup-row"><button type="button" disabled={saving} title="Clears this module's plate type and liquid amounts. Nothing moves." onClick={() => void clearModule(popup.station)}>Clear module</button></div>
                    {notice && <p role="alert">{notice}</p>}
                </div>}
                {popup?.kind === 'well' && <div role="dialog" aria-label={`${stationLabel(popup.station)} ${popup.well}`} className="bx-ui bld-popup" style={{ left: popup.left, top: popup.top }}>
                    <button type="button" className="bld-popup-close" aria-label="Close" onClick={() => setPopup(null)}>×</button>
                    <b>{stationLabel(popup.station)} · {popup.well}</b>
                    <div className="bld-popup-row"><label>Liquid in well <input type="number" min={0} step="any" aria-label="Liquid in well (µL)" placeholder="µL" value={draft} onChange={event => setDraft(event.target.value)} /> µL</label>
                        <button type="button" className="bx-go" disabled={saving} onClick={() => void saveWell(popup.station, popup.well, draft)}>Save</button></div>
                    <small>Your estimate; saved until you clear it. Checked by pressure detection when pipetting.</small>
                    <div className="bld-popup-row"><button type="button" onClick={() => addStop('aspirate')}>Aspirate from</button><button type="button" onClick={() => addStop('dispense')}>Dispense to</button>
                        <button type="button" disabled={!!props.wellDisabledReason && !props.onBlockedMove} title={props.wellDisabledReason ?? undefined}
                            onClick={() => { const { locationId, well } = popup; setPopup(null); if (!props.wellDisabledReason) props.onMoveToWell(locationId, well); else props.onBlockedMove?.({ locationId, well }); }}>Move head</button></div>
                    <div className="bld-popup-row"><button type="button" disabled={saving} title="Clears this module's plate type and liquid amounts. Nothing moves." onClick={() => void clearModule(popup.station)}>Clear module</button></div>
                    {notice && <p role="alert">{notice}</p>}
                </div>}
                {!popup && notice && <p role="alert">{notice}</p>}
                <div className="bld-legend"><span>Solid cross: reported gantry</span><span>Ring: alignment reference</span><span>Outlined station / well: requested destination</span><span className="bld-selection-key">Amber outline: dropdown selection</span><span>Filled well: liquid recorded</span>{holderTargets.length > 0 && <span>Bulk reagent holder: M1/M2 four-tube rows · P1/P2 eight-well PCR rows</span>}</div>
                <p className="bld-note">Intermittent controller observations. Heads and deck outlines are schematic; tips and occupancy are not inferred.</p>
                {geometry.isError && <p role="status">Geometry read unavailable. Showing {settings ? 'last known geometry' : 'the reference layout'}; the robot evaluates movement requests.</p>}
            </div>
            <aside className="bld-side" aria-label="Live movement controls">
                {props.preparation}
                {props.cameraControls}
                <BioXpLiveDeckPipette generation={generation} connected={connected} visible={visible} route={route} onRoute={setRoute} stopLabel={stopLabel}
                    tipTrays={trays} plateAt={plateAt} contents={contents} geometry={plateGeometry} onFinished={routeFinished} />
                <section className="bld-card bld-move-card" aria-label="Move head"><h3>Move head</h3>{props.movementControls}</section>
            </aside>
        </div>
        <div className="bld-band">
            {props.thermalControls ?? (props.doorControls ? <section className="bld-card">{props.doorControls}</section> : null)}
            <section className="bld-transfer-card" aria-label="Move plate / cover"><h3>Move plate / cover</h3>
                {typeof props.transferControls === 'function' ? props.transferControls(visible) : props.transferControls}
            </section>
            {props.deckRecord}
        </div>
        <section className="bld-pose-card" aria-label="Reported pose"><h3>{pose ? lastKnown ? 'Last known position' : 'Reported gantry position' : 'Position unavailable'}</h3>
            <dl className="bld-coordinates"><div><dt>X steps</dt><dd>{axisSteps('x')}</dd></div><div><dt>Y steps</dt><dd>{axisSteps('y')}</dd></div><div><dt>Z steps</dt><dd>{axisSteps('z')}</dd></div></dl>
            {updates.isError && <p role="status">Position updates unavailable. Retaining last observed values.</p>}
            <details className="bld-pose-more"><summary>Alignment, recorded location and destination</summary>
                <p>{time}</p><dl aria-label="Axis observation times">{axes?.axes.map(axis => <div key={axis.axis}>
                    <dt>{axis.axis.toUpperCase()} observed</dt><dd data-axis-observed={axis.axis} data-observed-at={axis.observed_at}>{new Date(axis.observed_at * 1000).toLocaleString()}</dd>
                </div>)}</dl>{axes && <p>Ownership generation {axes.ownershipGeneration}</p>}{dashboard?.telemetry?.snapshot?.clock_skew_detected && <p>Source clock skew reported.</p>}{pose && <p>{pose.reference}</p>}{outside && <p>Reported gantry is outside the reference deck view.</p>}
                <dl><dt>Published alignment</dt><dd>{tipLabel}</dd><dt>Alignment projection</dt><dd>{reference ? 'Source-addressing reference; not a physical measurement' : 'Unavailable'}</dd>
                    <dt>Recorded location</dt><dd>{dashboard?.deck?.current_location ?? 'Unavailable'}{dashboard?.deck?.current_well != null ? ` · well ID ${dashboard.deck.current_well}` : ''}</dd>
                    <dt>Selected destination</dt><dd>{destination?.label ?? 'None'}</dd>
                    <dt>Requested destination</dt><dd title={selection.station || undefined}>{deckStations.find(s => s.id === selection.station)?.label ?? props.destinations?.find(d => d.target === selection.station)?.label ?? (selection.station || 'None')}{selection.wells.length ? ` · ${selection.wells.join(', ')}` : ''}</dd></dl>
                <p className="bld-note">Recorded location and requested destination do not establish arrival.</p>
            </details>
        </section>
        <details className="bld-details" open={props.commandDetailsOpen} onToggle={event => props.onCommandDetailsToggle?.(event.currentTarget.open)}><summary>Command details</summary>{props.commandDetails}</details>
    </section>;
}
