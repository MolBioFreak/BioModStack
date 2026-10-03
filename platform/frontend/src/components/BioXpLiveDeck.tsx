import React, { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useBioXpOperatorUpdates, type BioXpDeckDestinationV1, type BioXpOperatorDashboardV2 } from '../lib/bioxpClient';
import { readCalibrationSettings } from '../lib/bioxpCalibration';
import { deckResources, deckSize, deckStations, projectDeckPoint, type BioXpDeckSelection } from '../lib/bioxpWorkflowDeck';
import { activeDeckResources, alignmentReference, gunCenters, poseFromAxisObservations, retainAxisObservations } from '../lib/bioxpLiveDeck';
import { BioXpWorkflowDeck } from './BioXpWorkflowDeck';
import './BioXpLiveDeck.css';

export type BioXpLiveDeckProps = {
    generation: number;
    connected: boolean;
    visible: boolean;
    dashboard: BioXpOperatorDashboardV2 | null | undefined;
    stale: boolean;
    selection: BioXpDeckSelection;
    selectedDestination?: BioXpDeckDestinationV1 | null;
    cameraControls?: React.ReactNode;
    onMoveToStation: (target: string) => void;
    onMoveToWell: (locationId: number, well: string) => void;
    stationDisabledReason: (target: string) => string | null;
    wellDisabledReason: string | null;
    doorControls: React.ReactNode;
    movementControls: React.ReactNode;
    commandDetails: React.ReactNode;
    onCommandDetailsToggle?: (open: boolean) => void;
    transferControls: React.ReactNode | ((visible: boolean) => React.ReactNode);
};

export function BioXpLiveDeck(props: BioXpLiveDeckProps) {
    const { generation, connected, visible, dashboard, selection } = props;
    const updates = useBioXpOperatorUpdates(generation, connected && visible);
    const [transferOpen, setTransferOpen] = useState(false);
    const revision = dashboard?.deck?.position_table_revision ?? null;
    const geometry = useQuery({
        queryKey: ['bioxp-live-deck-geometry', generation, revision],
        queryFn: async () => ({ generation, settings: await readCalibrationSettings(generation) }),
        enabled: visible && connected, staleTime: Infinity, retry: false,
        refetchOnWindowFocus: false, refetchOnReconnect: false,
        placeholderData: previous => previous?.generation === generation ? previous : undefined,
    });
    const settings = geometry.data?.generation === generation ? geometry.data.settings : undefined;
    const { resources, calibrated } = activeDeckResources(settings);
    const destination = props.selectedDestination;
    // Selection is display-only; preserve the exact native target for submission.
    const selectedStation = destination ? deckStations.find(station => station.id === destination.target
        || destination.aliases.includes(station.id) || station.locationId === destination.location_id) : undefined;
    const selectedCoordinates = !selectedStation && destination
        ? settings?.active_motion_positions?.find(row => row.location_id === destination.target)?.base_coordinates : undefined;
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
    return <section className="bioxp-live-deck" data-testid="oem-deck-movement" aria-label="Live deck movement">
        <header className="bld-heading"><div><h2>Live deck movement</h2><p>Click a destination or well to move.</p></div><span className="bld-status">{connected ? 'Connected' : 'Disconnected'} · {geometryLabel}</span></header>
        <div className="bld-workspace">
            <div className="bld-map-pane">
                <BioXpWorkflowDeck selection={selection} onChange={() => { /* Authoring selection is not a live intent. */ }}
                    resources={resources} live={{ onStation: props.onMoveToStation, onWell: props.onMoveToWell, selectedStation: selectedStation?.id,
                        stationDisabledReason: props.stationDisabledReason, wellDisabledReason: props.wellDisabledReason }}
                    overlay={<>{selectedPoint && <g className="bld-chosen-point" data-selected-destination={destination?.target}
                        aria-label={`Selected destination: ${destination?.label}`}>
                        <circle cx={selectedPoint[0]} cy={selectedPoint[1]} r="14" />
                        <title>{destination?.label} · selected native destination anchor</title>
                    </g>}{pose && point ? <g className={`bld-pose${lastKnown ? ' is-last-known' : ''}`} data-testid="reported-gantry" aria-label={lastKnown ? 'Last known gantry position' : 'Reported gantry position'}>
                        <title>Reported gantry; four schematic gun housings, not measured outlines or tip-presence observations</title>
                        <rect data-testid="vertical-deck-arm" className="bld-rail" x={point[0] - 45} y="25" width="13" height={deckSize.height - 50} rx="5" />
                        <path className="bld-carriage" d={`M ${point[0] - 39} ${guns[0].point[1]} L ${point[0] - 39} ${guns[3].point[1]}`} />
                        {guns.map(({ channel, point: [x, y] }) => <g key={channel} data-gun-module={channel} transform={`translate(${x} ${y})`}>
                            <path className="bld-gun-bracket" d="M -39 -9 H -22 V 9 H -39 Z" />
                            <rect className="bld-gun-housing" x="-24" y="-13" width="44" height="26" rx="4" />
                            <rect className="bld-gun-motor" x="-19" y="-9" width="17" height="18" rx="2" />
                            <path className="bld-gun-seam" d="M 5 -10 V 10 M 11 -8 V 8" />
                            <circle className="bld-gun-axis" cx="0" cy="0" r="3.4" />
                            <text className="bld-gun-number" x="25" y="4">{channel + 1}</text>
                        </g>)}
                        <path className="bld-gantry-anchor" d={`M ${point[0] - 7} ${point[1]} H ${point[0] + 7} M ${point[0]} ${point[1] - 7} V ${point[1] + 7}`} />
                        {reference && <g data-testid="alignment-reference"><circle className="bld-alignment" cx={reference[0]} cy={reference[1]} r="9" /><title>Published alignment-reference projection, not measured tip contact</title></g>}
                    </g> : null}</>} />
                <div className="bld-legend"><span>Solid cross: reported gantry</span><span>Ring: alignment reference</span><span>Outlined station / well: requested destination</span><span className="bld-selection-key">Amber outline: dropdown selection</span></div>
                <p className="bld-note">Intermittent controller observations. Gun housings and deck outlines are schematic; tips and occupancy are not inferred.</p>
                {geometry.isError && <p role="status">Geometry read unavailable. Showing {settings ? 'last known geometry' : 'the reference layout'}; the robot evaluates movement requests.</p>}
            </div>
            <aside className="bld-controls" aria-label="Live movement controls">
                {props.doorControls}
                {props.movementControls}
                {props.cameraControls}
                <section className="bld-pose-card" aria-label="Reported pose"><h3>{pose ? lastKnown ? 'Last known position' : 'Reported gantry position' : 'Position unavailable'}</h3>
                    <dl className="bld-coordinates"><div><dt>X steps</dt><dd>{axes?.axes.find(axis => axis.axis === 'x')?.position_steps ?? 'Unavailable'}</dd></div><div><dt>Y steps</dt><dd>{axes?.axes.find(axis => axis.axis === 'y')?.position_steps ?? 'Unavailable'}</dd></div><div><dt>Z steps</dt><dd>{axes?.axes.find(axis => axis.axis === 'z')?.position_steps ?? 'Unavailable'}</dd></div></dl>
                    {updates.isError && <p role="status">Position updates unavailable. Retaining last observed values.</p>}
                    <p>{time}</p><dl aria-label="Axis observation times">{axes?.axes.map(axis => <div key={axis.axis}>
                        <dt>{axis.axis.toUpperCase()} observed</dt><dd data-axis-observed={axis.axis} data-observed-at={axis.observed_at}>{new Date(axis.observed_at * 1000).toLocaleString()}</dd>
                    </div>)}</dl>{axes && <p>Ownership generation {axes.ownershipGeneration}</p>}{dashboard?.telemetry?.snapshot?.clock_skew_detected && <p>Source clock skew reported.</p>}{pose && <p>{pose.reference}</p>}{outside && <p>Reported gantry is outside the reference deck view.</p>}
                    <dl><dt>Published alignment</dt><dd>{tipLabel}</dd><dt>Alignment projection</dt><dd>{reference ? 'Source-addressing reference; not a physical measurement' : 'Unavailable'}</dd>
                        <dt>Recorded location</dt><dd>{dashboard?.deck?.current_location ?? 'Unavailable'}{dashboard?.deck?.current_well != null ? ` · well ID ${dashboard.deck.current_well}` : ''}</dd>
                        <dt>Selected destination</dt><dd>{destination?.label ?? 'None'}</dd>
                        <dt>Requested destination</dt><dd title={selection.station || undefined}>{deckStations.find(s => s.id === selection.station)?.label ?? (selection.station || 'None')}{selection.wells.length ? ` · ${selection.wells.join(', ')}` : ''}</dd></dl>
                    <p className="bld-note">Recorded location and requested destination do not establish arrival.</p>
                </section>
            </aside>
        </div>
        <details className="bld-details" onToggle={event => props.onCommandDetailsToggle?.(event.currentTarget.open)}><summary>Command details</summary>{props.commandDetails}</details>
        <details className="bld-details" onToggle={event => setTransferOpen(event.currentTarget.open)}><summary>Plate and cover handling</summary>
            {typeof props.transferControls === 'function' ? props.transferControls(visible && transferOpen) : props.transferControls}
        </details>
    </section>;
}
