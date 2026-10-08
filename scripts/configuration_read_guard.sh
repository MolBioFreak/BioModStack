#!/usr/bin/env bash
# Source before reading profile exports; finish immediately after reading them.
# First-install is immutable after activation. A transition from legacy/absent
# state during a read is rejected rather than mixing defaults and a generation.
_BMS_CONFIG_GUARD_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
_BMS_CONFIG_TRANSACTION="${XDG_CONFIG_HOME:-$HOME/.config}/biomodstack/configuration-v1"
_BMS_CONFIG_BEFORE=absent
[[ ! -d "$_BMS_CONFIG_TRANSACTION" ]] || _BMS_CONFIG_BEFORE=managed
bms_configuration_check() {
    PYTHONPATH="$_BMS_CONFIG_GUARD_ROOT${PYTHONPATH:+:$PYTHONPATH}" python3 -B -c \
        'from biomodstack_configuration import assert_configuration_readable; assert_configuration_readable()' || exit 78
}
bms_configuration_read_finish() {
    bms_configuration_check
    if [[ "$_BMS_CONFIG_BEFORE" == absent && -d "$_BMS_CONFIG_TRANSACTION" ]]; then
        printf '%s\n' 'BioModStack configuration changed during read; retry after recover.' >&2
        exit 78
    fi
}
bms_configuration_check
