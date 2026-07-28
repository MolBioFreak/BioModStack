import assert from 'node:assert/strict'
import fs from 'node:fs'
import path from 'node:path'
import test from 'node:test'

const root = path.resolve(process.cwd())
const clientSource = fs.readFileSync(path.join(root, 'src/lib/bioxpClient.ts'), 'utf8')
const cockpitSource = fs.readFileSync(path.join(root, 'src/components/BioXpCockpit.tsx'), 'utf8')

test('BioXP projects typed maintenance state and exposes only the typed recovery command', () => {
  assert.match(clientSource, /export interface BioXpMaintenanceState/)
  assert.match(clientSource, /maintenance_state: BioXpMaintenanceState \| null/)
  assert.match(clientSource, /\| 'recover_motion_non_homing'/)
  assert.match(clientSource, /command: 'recover_motion_non_homing'/)
  assert.match(clientSource, /operator_ack: 'RECOVER_MOTION'/)
  assert.match(clientSource, /reason: string/)
})

test('BioXP shows maintenance state and a deliberate non-homing recovery action', () => {
  assert.match(cockpitSource, /Maintenance motion state/)
  assert.match(cockpitSource, /maintenanceState\?\.block_reason/)
  assert.match(cockpitSource, /Recover Motion Without Homing/)
  assert.match(cockpitSource, /window\.confirm\(/)
  assert.match(cockpitSource, /window\.prompt\(/)
  assert.match(cockpitSource, /command: 'recover_motion_non_homing'/)
  assert.match(cockpitSource, /operator_ack: 'RECOVER_MOTION'/)
})

test('BioXP uses API command availability for movement and visibly blocks lifecycle planning', () => {
  assert.match(cockpitSource, /availableCommands\.has\('run_axis_diagnostic'\)/)
  assert.match(cockpitSource, /availableCommands\.has\('run_oem_motor_stage'\)/)
  assert.match(cockpitSource, /maintenanceMotionBlocked/)
  assert.match(cockpitSource, /disabled=\{[^}]*maintenanceMotionBlocked/)
  assert.match(cockpitSource, /Lifecycle planning is blocked while maintenance motion is blocked/)
})
