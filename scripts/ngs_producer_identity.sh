# Source this in an NGS task; append the verified receipt to its existing log.
# Only implementation files and resolved executables are read here. Scientific
# inputs, models and runtime images retain their existing locked receipt owners.
bms_producer_capture() {
    local relative executable digest
    for relative in scripts/ngs_producer_identity.sh "${bms_producer_sources[@]}"; do
        [[ -f "${bms_producer_root}/${relative}" && ! -L "${bms_producer_root}/${relative}" ]] || return 1
        digest=$(sha256sum "${bms_producer_root}/${relative}") || return 1
        printf 'bms_producer_source:%s=%s\n' "${relative}" "${digest%% *}"
    done
    for executable in "${bms_producer_tools[@]}"; do
        local resolved label command_name
        label=${executable%%=*}
        command_name=${executable#*=}
        resolved=$(command -v "${command_name}") || return 1
        [[ -f "${resolved}" ]] || return 1
        digest=$(sha256sum "${resolved}") || return 1
        printf 'bms_producer_tool:%s=%s\n' "${label}" "${digest%% *}"
    done
    digest=$(sha256sum .command.sh) || return 1
    printf 'bms_producer_command=%s\n' "${digest%% *}"
}

bms_producer_begin() {
    bms_producer_root=$1; shift
    bms_producer_sources=()
    while [[ $# -gt 0 && $1 != -- ]]; do
        bms_producer_sources+=("$1"); shift
    done
    [[ $# -gt 0 ]] || return 1
    shift
    bms_producer_tools=("$@")
    bms_producer_capture > .bms-producer.before || return 1
}

bms_producer_finish() {
    bms_producer_capture > .bms-producer.after || return 1
    cmp -s .bms-producer.before .bms-producer.after || {
        printf 'NGS producer implementation changed during execution\n' >&2
        return 1
    }
    cat .bms-producer.after
}
