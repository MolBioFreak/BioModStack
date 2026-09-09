# BioXP responsiveness candidate

This is a paired change with the robot operator-responsiveness candidate. It is
not authorization to deploy either side or to run motion.

- Last-known same-generation telemetry and receipt rows remain visible during a
  failed/stale refresh. They are labeled stale/unavailable, not current authority.
- Existing freshness, coherence, generation, pending-operation, and backend safety
  checks continue to gate mutation. Retained display data is not substituted for
  fresh admission data. Connection-generation changes still separate query data.
- A status-read error does not erase the last-known generation used to identify
  display/cache data. It does disable connected/fresh-authority admission.
- Query `enabled` is not part of the v2 catalog identity: temporary disabling must
  not create a different empty cache for the same connection generation.
- Robot action requests receive durable admission rather than waiting for OEM
  completion. Existing receipt polling/recovery follows the returned ID. No new
  motion retries or inflated transport timeouts are introduced.
- Automatic snapshot requests send `automatic: true`. A robot response of
  `published: false, reason: operator_action_pending` is a foreground-priority
  deferral, not a failed controller query. A subsequent observation poll may retry
  after 0.5 seconds; ordinary transport failures retain their configured backoff.
  This retries only status observation, never a motion command.

The mounted cockpit regressions cover stale-display/fresh-admission separation
and preservation of retained receipt rows during a read failure. Robot-client
transport tests use mock transports and verify the automatic-collection flag and
short observation deferral. Isolated frontend build and focused regression results
belong in the accompanying evidence bundle, not a claim of live responsiveness.

Both candidates are needed for the complete behavior. The underlying Y preliminary
move stall remains a live hardware investigation; neither UI smoothing nor durable
admission proves that the motor moved.
