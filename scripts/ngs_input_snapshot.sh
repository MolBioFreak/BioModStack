#!/usr/bin/env bash
# NGS task naming only: retained authority is supplied by the existing receipt
# consumer, not inferred from a read-only upload. Keep legacy snapshot behavior.
ngs_input_snapshot() {
    local source="$1" target="$2" retained="$3" concrete mode
    concrete="$(readlink -f -- "$source")"
    mode="$(stat -c %a -- "$concrete")"
    if [[ "$retained" == true ]] && (( (8#$mode & 8#222) == 0 )); then
        if [[ "$(stat -c %d -- "$concrete")" == "$(stat -c %d -- "$(dirname -- "$target")")" ]]; then
            ln -- "$concrete" "$target"
        else
            # A task-local name may reference an already retained version on
            # another filesystem; this is not an independently portable export.
            ln -s -- "$concrete" "$target"
        fi
    else
        cp --reflink=auto -- "$source" "$target"
        chmod 0444 "$target"
    fi
}
