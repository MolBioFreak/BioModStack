import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
const cockpit = readFileSync(resolve('src/components/BioXpCockpit.tsx'), 'utf8');
test('retired command families have no cockpit producer', () => {
  for (const command of ['initialize_oem_environment', 'initialize_motors', 'run_oem_motor_stage', 'record_oem_motor_stage_observation', 'collect_hardware_snapshot', 'start_job', 'pause_job', 'resume_job', 'stop_job', 'recover_runtime']) assert.doesNotMatch(cockpit, new RegExp(command));
});
test('cockpit produces only compact active command families', () => {
  for (const command of ['activate_usb_for_service', 'recover_motion_non_homing', 'run_axis_diagnostic', 'stop_axis_diagnostic']) assert.match(cockpit, new RegExp(command));
});
