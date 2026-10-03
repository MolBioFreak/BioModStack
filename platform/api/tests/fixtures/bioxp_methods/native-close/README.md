# Native-close BMS receiving fixtures

These 51 gzip files are byte-for-byte copies of `testdata/native_method_close/bms/`
from native evidence commit `afd6349` (producer implementation
`ae006a9a854c2959ab313aac994b396217ac12f6`). `producer-manifest.json` is the unchanged
native manifest; only its `bms/` entries are bundled here. Paths elsewhere in that
manifest refer to the native repository, not additional BMS fixture dependencies.

The collection covers 49 distinct compiled documents, 51 final jobs, and retained
held readbacks. The API test replays every final job and the timer error-held job.
It extracts original JSON value byte slices from the decompressed wrapper, checks
both compressed and decompressed hashes, and sends those bytes through
BioXpRobotClient's HTTP transport. No IDs or metadata are changed.

The original documents contain `metadata.bms_method`, **not**
`metadata.bms_method_run`. Raw method, bindings, dependencies, compiler-recorded
initial state, liquid resolution, digest and action provenance are present.
A full facade run envelope and the original submitter's omitted-vs-null assumption
choice are not present. Receiving reports this limitation rather than injecting
new metadata or recompiling with a newer compiler.

Native source execution used physical-leaf replacements. This is offline software
qualification, not liquid accuracy, hardware state, or scientific success. These
wrappers do not include a post-pickup failure or exact control request/receipt
capture; held and aborted job snapshots are not substitutes for such captures.
