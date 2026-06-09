import assert from 'node:assert/strict';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';

const layoutSource = readFileSync(resolve('src/components/Layout.tsx'), 'utf8');
const clientSource = readFileSync(resolve('src/lib/bioxpClient.ts'), 'utf8');
const cockpitSource = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
const panelPath = resolve('src/components/BioXpInterlinkControlPanel.tsx');
const panelSource = existsSync(panelPath) ? readFileSync(panelPath, 'utf8') : '';
const interlinkStatusSource = readFileSync(resolve('src/components/bioxpInterlinkStatus.ts'), 'utf8');

test('BioXP topbar menu exists but is gated by the install feature flag', () => {
    assert.ok(existsSync(panelPath), 'BioXpInterlinkControlPanel.tsx must exist');
    assert.match(layoutSource, /BioXpInterlinkMenu/);
    assert.match(layoutSource, /bmsFeatures\.bioxp\s*&&\s*<BioXpInterlinkMenu \/>/);
    assert.match(panelSource, /data-bms-bioxp-interlink-menu="true"/);
    assert.match(panelSource, /BioXP/);
    assert.doesNotMatch(panelSource, /BIOXP LINK/);
    assert.doesNotMatch(panelSource, /BioXP robot interlink/);
});

test('interlink panel exposes governed connection, diagnostics, logs, and button-only runtime controls', () => {
    for (const marker of [
        'Status:',
        'Endpoint:',
        'deriveBioXpInterlinkMenuStatus',
        'useBioXpInterlinkState(true, isOpen ? 5000 : 30000)',
        'probeFailed: state.isError',
        'probeStale: state.data?.probe_stale === true',
        'maskEndpointForDisplay',
        'xxx.xxx',
        'Profile settings',
        'Connect',
        'Disconnect',
        'Forget saved profile',
        'Diagnostics',
        'Robot logs',
        'Advanced controls',
        'Restart runtime',
        'RESET BIOXP RUNTIME',
        'Reboot host',
        'REBOOT ROBOT',
    ]) {
        assert.match(panelSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    for (const removed of [
        'Profile is inert until Connect',
        'Status/control only — no motion',
        'Endpoint is masked outside edit mode',
        'active URL:',
        'recommended:',
        'state:',
        'reachable:',
        'saved endpoint hidden',
        'add endpoint',
        'Fetch robot logs',
        'Recent robot API logs (last 120 lines)',
        'Advanced controls (restart/reboot)',
        'Some deployments do not support these actions',
        'BMS proxy route only',
        'typed confirmation required',
        'type RESET BIOXP RUNTIME',
        'type REBOOT ROBOT',
        'runtimeAck',
        'rebootAck',
        'Saved profile is inactive',
        'optional sudo password',
        'Lifecycle actions unavailable here',
        'reset/reboot controls are intentionally not shown',
        'Robot service logs',
        'Fetches the last 120 robot-local API service lines',
        'Last 120 robot-local API lines',
        'Runtime controls',
        'Robot API runtime/container controls',
        'governed BMS proxy endpoints only',
        'BMS proxy only; no raw-port access or motion',
        'raw container-internal FastAPI port',
        'Typed ack required',
        'supported=false',
        'Advanced robot host reboot',
        'never home, arm, recover motion, or move axes',
    ]) {
        assert.doesNotMatch(panelSource, new RegExp(removed.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('interlink status helper fails closed on unknown, stale, or timed-out robot probes', () => {
    for (const marker of [
        'BIOXP_INTERLINK_FRESH_PROBE_WINDOW_MS',
        "unreachable: 'UNREACHABLE'",
        "unverified: 'UNVERIFIED'",
        "stale: 'STALE'",
        "linked: 'LINKED'",
        'Robot API unreachable',
        'Active, not yet verified',
        'Last robot probe is stale',
        'hardware state unknown',
        "state === 'linked'",
    ]) {
        assert.match(interlinkStatusSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }

    assert.doesNotMatch(panelSource, /reachable === false\s*\?\s*'DEGRADED'\s*:\s*'LINKED'/);
    assert.doesNotMatch(panelSource, /Connected, not reachable/);
    assert.match(cockpitSource, /useBioXpInterlinkState\(true, activeTab === 'connection' \? 5000 : false\)/);
});

test('frontend client exposes interlink hooks and only the BMS proxy route family', () => {
    for (const marker of [
        'export interface BioXpInterlinkState',
        'export interface BioXpInterlinkSettings',
        'export interface BioXpInterlinkActionRequest',
        'export const useBioXpInterlinkState',
        'export const useSaveBioXpInterlinkSettings',
        'export const useForgetBioXpInterlinkSettings',
        'export const useBioXpInterlinkConnect',
        'export const useBioXpInterlinkDisconnect',
        'export const useBioXpInterlinkDiagnostics',
        'export const useBioXpRuntimeReset',
        'export const useBioXpRobotReboot',
        'export const useBioXpInterlinkLogs',
        'export interface MotionInterlockOverridePayload',
        'export const useMotionInterlockOverrideStatus',
        'export const useSetMotionInterlockOverride',
        'export type UsbSniffProfile',
        'export interface UsbSniffStatusResponse',
        'export interface UsbSniffCapturePayload',
        'export const useUsbSniffStatus',
        'export const useUsbSniffRuns',
        'export const useStartUsbSniffCapture',
        'export const useStopUsbSniffCapture',
        'export const useExportUsbSniffCapture',
        "operator_ack: 'USB_SNIFF'",
        'export interface MotionEvidence',
        'motion_evidence?: MotionEvidence | null',
        'controller_motion_evidence?: boolean',
        'physical_motion_confirmed?: boolean',
        '/api/bioxp/interlink/state',
        '/api/bioxp/interlink/settings',
        '/api/bioxp/interlink/connect',
        '/api/bioxp/interlink/disconnect',
        '/api/bioxp/interlink/diagnostics',
        '/api/bioxp/interlink/runtime-reset',
        '/api/bioxp/interlink/robot-reboot',
        '/api/bioxp/interlink/logs',
        '/api/bioxp/motion/interlock/override',
        '/api/bioxp/diagnostics/usb-sniff/status',
        '/api/bioxp/diagnostics/usb-sniff/runs',
        '/api/bioxp/diagnostics/usb-sniff/start',
        '/api/bioxp/diagnostics/usb-sniff/stop',
        '/api/bioxp/diagnostics/usb-sniff/export',
    ]) {
        assert.match(clientSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
    assert.doesNotMatch(panelSource, /8123\/status/);
});

test('BioXP cockpit surfaces OEM-style motion evidence without claiming physical proof', () => {
    for (const marker of [
        'motionEvidenceClassification',
        'CTRL EVIDENCE:',
        'PHYSICAL PROOF:',
        'STALL EVENT:',
        'nonzero speed:',
        "physical_motion_confirmed === true ? 'YES' : 'NO'",
    ]) {
        assert.match(cockpitSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('BioXP cockpit exposes latch/24V interlock override as explicit commissioning-only control', () => {
    for (const marker of [
        'Commissioning latch + 24V override',
        'Default off; strict-startup only. Operator observation required.',
        'Enable Latch+24V Override',
        'Disable Override',
        "operator_ack: 'INTERLOCK_OVERRIDE'",
        'override_latch: true',
        'override_24v: true',
        'Reason required before enable',
    ]) {
        assert.match(cockpitSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('BioXP cockpit exposes full USB capture controls as capture-only diagnostics', () => {
    for (const marker of [
        'USB Capture',
        'Full USB Packet Capture',
        'Priority 1 observability: usbmon pcap + driver TX/RX ledger',
        'Capture-only diagnostics',
        'No homing, no arming, no recovery, no axis movement',
        'USB SNIFF:',
        'Start USB Capture',
        'Stop Capture',
        'Export Bundle',
        "operator_ack: 'USB_SNIFF'",
        'useUsbSniffStatus',
        'useStartUsbSniffCapture',
        'Capture Completeness Contract',
        'packet counts and unmatched-frame accounting',
    ]) {
        assert.match(cockpitSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('BioXP cockpit fails closed when robot API/control-plane evidence is absent', () => {
    for (const marker of [
        'robotApiReachable',
        'robotApiExplicitlyUnreachable',
        'interlinkProbeFresh',
        'interlinkReportsRobotApiReachable',
        'runtimeReportsRobotApiReachable',
        'operationCapabilitiesReportRobotApiReachable',
        'statusFetchedAfterMount',
        'runtimeFetchedAfterMount',
        'statusDataUpdatedAt',
        'runtimeDataUpdatedAt',
        'operationCapabilities.dataUpdatedAt',
        'isFreshPositiveBioXpSignal',
        'interlinkExplicitFailure',
        'ROBOT UNREACHABLE',
        'BIOXP ROBOT UNREACHABLE',
        'Robot probes failed; hardware state unknown.',
        'CHECKING...',
    ]) {
        assert.match(cockpitSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }

    assert.doesNotMatch(cockpitSource, /robot_openapi_reachable\s*!==\s*false/);
    assert.doesNotMatch(cockpitSource, /bioXpInterlink\.data\?\.reachable === true \|\|/);
    assert.doesNotMatch(cockpitSource, /runtimeStatus\?\.linked_runtime_reachable === true \|\|/);
    assert.doesNotMatch(cockpitSource, /operationCapabilities\.data\?\.robot_openapi_reachable === true \|\|/);
    assert.doesNotMatch(cockpitSource, /interlinkReportsHardwareConnected = bioXpInterlink\.data\?\.hardware_connected === true/);
    assert.doesNotMatch(cockpitSource, /interlinkActive\s*&&\s*\(statusIsError\s*\|\|\s*runtimeStatusIsError/);
    assert.doesNotMatch(cockpitSource, /PINGING\.\.\./);
    assert.doesNotMatch(cockpitSource, /hasRecentHardwareContact/);
});

test('BioXP cockpit treats fresh positive robot evidence as reachable even after a stale failed status probe', () => {
    for (const marker of [
        'interlinkReportsRobotApiReachable',
        'runtimeReportsRobotApiReachable',
        'operationCapabilitiesReportRobotApiReachable',
        'statusReportsHardwareConnected',
        'runtimeReportsHardwareConnected',
        'interlinkReportsHardwareConnected',
        '!robotApiReachable &&',
        "? 'API ONLY'",
    ]) {
        assert.match(cockpitSource, new RegExp(marker.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
    }
});

test('BioXP cockpit is gated by active interlink state and does not own reset lifecycle', () => {
    assert.match(cockpitSource, /useBioXpInterlinkState/);
    assert.match(cockpitSource, /Activate BIOXP LINK first\. No auto-motion on load\./);
    assert.match(cockpitSource, /interlinkActive/);
    assert.doesNotMatch(cockpitSource, /useMotionHardReset/);
    assert.doesNotMatch(cockpitSource, /motionHardReset/);
    assert.doesNotMatch(cockpitSource, /Reconnect USB Runtime/);
    assert.doesNotMatch(cockpitSource, />\s*Hard Reset\s*</);
    assert.match(cockpitSource, /Reset thermal controller profile/);
    assert.match(cockpitSource, /Reset chiller profile/);
});

test('runtime reset and robot reboot are exposed as button-only interlink menu actions but not cockpit actions', () => {
    assert.match(panelSource, /Restart runtime/);
    assert.match(panelSource, /operator_ack: 'RESET BIOXP RUNTIME'/);
    assert.match(panelSource, /Reboot host/);
    assert.match(panelSource, /operator_ack: 'REBOOT ROBOT'/);
    assert.doesNotMatch(panelSource, /placeholder="type RESET BIOXP RUNTIME"/);
    assert.doesNotMatch(panelSource, /placeholder="type REBOOT ROBOT"/);
    assert.doesNotMatch(cockpitSource, /Reset robot local runtime/);
    assert.doesNotMatch(cockpitSource, /Restart robot OS/);
    assert.doesNotMatch(cockpitSource, /Reboot robot host/);
});
