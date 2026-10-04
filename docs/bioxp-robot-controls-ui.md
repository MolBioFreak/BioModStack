# BioXP Robot controls presentation

Approved layout: all axes in rows on desktop and phone, with sliders and exact entry.
This is a presentation-only change to the Robot controls landing page and shared
header. Pipettes, Workflows and Live deck movement retain their existing editors.

## Layout

- Keep the four existing tabs, keyboard navigation and their existing lazy mounts.
- Keep addressed X/Y/Z/gripper Stops and one Software Abort in the shared header.
  Software Abort cancels waiters; it is not a motor Stop or physical emergency stop.
- Keep connection, controller Enable/Recover and service restart accessible on all
  four tabs. The shared strip reports connection, controller state, references,
  enclosure state and observation time without repeating uncertainty banners.
- Show X, Y, Z, gripper and thermal-door controls together in aligned rows at every
  width. Narrow rows wrap their controls; there is no axis picker.
- Replace step presets with relative-step and absolute-target sliders alongside
  small exact numeric entries. Use published per-axis bounds, not the generic
  signed-integer transport limit. If bounds are absent, retain exact entry and
  existing action availability without inventing a range.
- Sliders edit the existing drafts only. Relative −/+ and absolute Go remain the
  explicit movement actions. Preserve numeric input bounds/defaults and native
  payloads; a cleared or out-of-slider-range numeric draft is never rewritten by
  rendering, polling or bounds changes.
- Z Clear and switch-search recovery home remain explicit distinct actions.
  Combined XY controls remain accessible in a compact phone disclosure.
- Keep ordinary evidence in row details, history/reports and tools/catalog in
  collapsed drawers. Pending commands, uncertainty, errors and interrupt outcomes
  must remain visible outside closed disclosures.
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
400×900, including the BMS shell's inner scrolling container. Every axis row is
visible without selection; vertical scrolling on narrow screens is acceptable.
No control may be clipped or require page-wide horizontal scrolling. Check global theme changes, text contrast and
horizontal overflow. Compare a matched passive request window with the baseline.
No physical robot command is required for this UI qualification.
