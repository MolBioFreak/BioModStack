#!/usr/bin/env bash
# Source inside a task to reuse its completed help output; direct invocation
# retains the three-argument capability probe interface.
_dorado_help_command=''
_dorado_help_subcommand=''
_dorado_help_status=1
_dorado_help_output=''

dorado_supports_option() {
    if [[ $# -ne 3 ]]; then
        echo "usage: dorado_supports_option.sh <dorado-command> <subcommand> <option>" >&2
        return 2
    fi
    local dorado_command=$1 subcommand=$2 option=$3
    if [[ -z "$dorado_command" || -z "$subcommand" || "$option" != --* ]]; then
        echo "invalid Dorado capability probe arguments" >&2
        return 2
    fi
    if [[ "${_dorado_help_command-}" != "$dorado_command" || "${_dorado_help_subcommand-}" != "$subcommand" ]]; then
        _dorado_help_command=$dorado_command
        _dorado_help_subcommand=$subcommand
        _dorado_help_status=0
        _dorado_help_output=$("$dorado_command" "$subcommand" --help 2>&1) || _dorado_help_status=$?
    fi
    [[ "${_dorado_help_status}" -eq 0 ]] || return 1
    # Match completed output, never a live help | grep -q pipeline (SIGPIPE).
    [[ "${_dorado_help_output}" == *"$option"* ]]
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    set -euo pipefail
    dorado_supports_option "$@"
fi
