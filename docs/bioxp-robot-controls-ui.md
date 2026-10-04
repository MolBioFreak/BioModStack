# BioXP Robot controls presentation

Approved layout: compact desktop axis rows and a single-axis phone picker.
This is a presentation-only change to the Robot controls landing page and shared
header. Pipettes, Workflows and Live deck movement retain their existing editors.

## Layout

- Keep the four existing tabs, keyboard navigation and their existing lazy mounts.
- Keep addressed X/Y/Z/gripper Stops and one Software Abort in the shared header.
  Software Abort cancels waiters; it is not a motor Stop or physical emergency stop.
- Keep connection, controller Enable/Recover and service restart accessible on all
  four tabs. The shared strip reports connection, controller state, references,
  enclosure state and observation time without repeating uncertainty banners.
- Show X, Y, Z, gripper and thermal-door controls in aligned desktop rows. Keep
  steps and the 1,000/5,000/10,000/25,000 presets, existing numeric bounds and defaults.
  Z Clear and switch-search recovery home remain explicit distinct actions.
- Below 768px, select one axis at a time without duplicating controls or resetting
  its draft. Combined XY controls remain accessible in a compact disclosure.
  The phone layout also covers narrow tablets so row Stops cannot be clipped.
- Keep ordinary evidence in row details, history/reports and tools/catalog in
  collapsed drawers. Pending commands, uncertainty, errors and interrupt outcomes
  must remain visible outside closed disclosures and nonselected mobile controls.
- Use inherited BMS theme tokens. Do not add a local theme palette or theme state.
  Use a darkened theme error color with white text for the shared motor Stops;
  selected tabs use a tinted theme accent, primary text and an accent border.

## Preserved behavior

Keep every existing mutation, endpoint, payload, idempotency key, authority field,
disabled predicate, input limit, default, polling owner and cadence. Move the
existing controls rather than reimplementing their operations. Short visible
labels retain descriptive accessible names and disabled reasons.

Observation age and missing readbacks remain presentation evidence, not new
admission rules, reference claims or success conditions. No implicit homing,
recovery, retries, service restart or robot mutation occurs on navigation.

History, reports and the command catalog keep their existing explicit-demand
states. Removing the duplicate dashboard must not create another query owner or
remove the telemetry used by surviving controls. Service restart retains its
existing mutation/availability reader and independent submission behavior.

## Qualification

Use inert transport to verify exact requests for every retained action and rapid
submission/Stop behavior. Keep existing behavioral assertions; update selectors
and retired-copy assertions only where presentation ownership moved. Exercise
mobile draft retention, shared controls on all four tabs, error/pending visibility
and lazy history/report/catalog demand.

Build the actual frontend. Measure the normal served route at 1440×900 and
400×900, including the BMS shell's inner scrolling container. The ordinary
collapsed layout should fit within one viewport; errors or explicitly opened
details may increase height. Check global theme changes, text contrast and
horizontal overflow. Compare a matched passive request window with the baseline.
No physical robot command is required for this UI qualification.
