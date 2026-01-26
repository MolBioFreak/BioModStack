#!/usr/bin/env python3
"""
Convert anchor JSON into PPIFlow fixed_positions string.
"""
import argparse
import json
import re
from pathlib import Path


def group_consecutive(numbers):
    if not numbers:
        return []
    numbers = sorted(set(numbers))
    ranges = []
    start = numbers[0]
    prev = numbers[0]
    for num in numbers[1:]:
        if num != prev + 1:
            ranges.append((start, prev))
            start = num
        prev = num
    ranges.append((start, prev))
    return ranges


def build_positions(anchors):
    by_chain = {}
    icode_positions = []
    for anchor in anchors:
        chain = anchor.get("chain")
        resnum = anchor.get("resnum")
        icode = (anchor.get("icode") or "").strip()
        if not chain or resnum is None:
            continue
        if icode:
            icode_positions.append(f"{chain}{resnum}{icode}")
            continue
        by_chain.setdefault(chain, []).append(int(resnum))

    positions = []
    for chain, nums in sorted(by_chain.items()):
        for start, end in group_consecutive(nums):
            if start == end:
                positions.append(f"{chain}{start}")
            else:
                positions.append(f"{chain}{start}-{end}")

    positions.extend(sorted(set(icode_positions)))
    return ",".join(positions)


def main():
    parser = argparse.ArgumentParser(description="Generate PPIFlow fixed_positions from anchors JSON")
    parser.add_argument("--anchors_json", required=True, help="Path to anchors.json")
    parser.add_argument("--output", required=True, help="Path to output positions string")
    args = parser.parse_args()

    with open(args.anchors_json, "r") as f:
        data = json.load(f)

    anchors = data.get("anchors", [])
    positions = build_positions(anchors)
    Path(args.output).write_text(positions + "\n")


if __name__ == "__main__":
    main()
