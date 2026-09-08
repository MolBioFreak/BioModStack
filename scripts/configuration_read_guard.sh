#!/usr/bin/env bash
# Guard both presence and immutable generation identity across export reads.
_BMS_CONFIG_GUARD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
bms_configuration_check() {
    PYTHONPATH="$_BMS_CONFIG_GUARD_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -B -c \
        'import sys; sys.path.insert(0, sys.argv[1]); from biomodstack_configuration import assert_configuration_readable, configuration_identity; before=configuration_identity(); assert_configuration_readable(); assert before == configuration_identity(), "Configuration changed; retry"; print(before or "absent")' "$_BMS_CONFIG_GUARD_ROOT"
}
_BMS_CONFIG_BEFORE="$(bms_configuration_check)" || exit 78
bms_configuration_read_finish() {
    local after
    after="$(bms_configuration_check)" || exit 78
    if [[ "$_BMS_CONFIG_BEFORE" != "$after" ]]; then
        printf '%s\n' 'BioModStack configuration changed during read; retry after recover.' >&2
        exit 78
    fi
}
