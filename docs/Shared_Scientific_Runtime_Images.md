# Shared scientific runtime images

New Protenix conformational-mapping preflights and NGS alignment commands use one verified, immutable local SIF object per SHA-256, rather than task-local or source-parent-specific copies.

## Storage and references

- Local default: `${BMS_CONTAINER_DIR}/.image-store`; override with `BMS_RUNTIME_IMAGE_STORE`.
- Object: `objects/sha256/<digest>/runtime.sif`, file mode 0400, object directory 0500, one inode/link.
- Objects are copied once at publication, verified against the expected digest, atomically published and reused. Mutable original sources are not hardlinked into the store. Corrupt existing objects fail; they are not silently overwritten.
- Development API and workflow-adapter managed units load optional `references/development.env` under their default store. Its exact digest paths select Dorado, Confornets and Protenix. Production references may be prepared but require an explicitly authorized production service configuration/promotion; Development deployment does not reconfigure production.
- Publish a complete lane reference set with `python scripts/publish_runtime_images.py --store-root PATH --lane development --manifest FILE`. JSON maps `BMS_NGS_RUNTIME_SIF`, `BMS_CM_CONFORNETS_CONTAINER_PATH`, and/or `BMS_PROTENIX_CONTAINER_PATH` to objects containing `source` and `sha256`. The tool never deletes originals or restarts services. Reference updates take effect after the managed lane restarts.

## Execution

Protenix preflight stages a small receipt/reference, not a SIF. Execution selects the verified shared object; image identity is checked at the execution boundary. NGS samtools retains its no-follow descriptor and inode checks, but all source parents resolve to the same shared object. Changing a source file cannot mutate a published object.

Remote command transport resolves the image store to the worker's shared `cache/runtime-images` directory, not an attempt directory or a controller path. Existing remote input/runtime transfer behavior is otherwise unchanged; remote materialization copies are not removed by this change.

## Retirement

A source path is not disposable merely because publication succeeded. First update its consumers/references and stop or drain affected commands; check pinned open descriptors and retained runtime generations. Preserve required distinct digests, not redundant physical copies. Do not replace live pinned objects or introduce writable hardlink aliases. There is intentionally no unattended deletion policy in this change. Operator retirement must verify the exact digest/inode and exclude current reference objects.

The object modes protect cooperating BMS processes, not against deliberate mutation by the filesystem owner/root. Publication, consumers and maintenance must preserve that ownership boundary.
