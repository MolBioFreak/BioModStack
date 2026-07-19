#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 ]]; then
    echo "usage: dorado_supports_option.sh <dorado-command> <subcommand> <option>" >&2
    exit 2
fi

dorado_command=$1
subcommand=$2
option=$3

if [[ -z "$dorado_command" || -z "$subcommand" || "$option" != --* ]]; then
    echo "invalid Dorado capability probe arguments" >&2
    exit 2
fi

help_file=$(mktemp)
trap 'rm -f "$help_file"' EXIT

if ! "$dorado_command" "$subcommand" --help >"$help_file" 2>&1; then
    exit 1
fi

# Search a completed help file rather than a live `help | grep -q` pipeline.
# Under `set -o pipefail`, grep's early exit can SIGPIPE Dorado and turn a real
# capability match into status 141.
if grep -F -q -- "$option" "$help_file"; then
    exit 0
fi
exit 1
