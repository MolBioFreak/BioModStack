#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 3 && $# -ne 4 ]]; then
    echo "usage: dorado_supports_option.sh <dorado-command> <subcommand> <option> [evidence-json]" >&2
    exit 2
fi

dorado_command=$1
subcommand=$2
option=$3

if [[ -z "$dorado_command" || -z "$subcommand" || ! "$option" =~ ^--[A-Za-z0-9-]+$ ]]; then
    echo "invalid Dorado capability probe arguments" >&2
    exit 2
fi

help_file=$(mktemp)
trap 'rm -f "$help_file"' EXIT

if ! "$dorado_command" "$subcommand" --help >"$help_file" 2>&1; then
    # A failed help invocation is not evidence that an option is unsupported.
    exit 2
fi

# Search completed help rather than a SIGPIPE-prone live pipeline.
supported=false
if grep -E -q -- "(^|[[:space:],])${option}([[:space:]=,]|$)" "$help_file"; then supported=true; fi
if [[ $# -eq 4 ]]; then
    help_sha256=$(sha256sum "$help_file" | cut -d' ' -f1)
    jq -n --argjson supported "$supported" --arg help_sha256 "$help_sha256" \
        '{supported:$supported,help_sha256:$help_sha256}' > "$4"
fi
if [[ "$supported" == true ]]; then exit 0; fi
exit 1
