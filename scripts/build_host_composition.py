#!/usr/bin/env python3
"""Share of the whole read population that is host genomic DNA.

Reads are counted once each. A read that aligns to both the plasmid and the
host goes to whichever holds more of its bases; a tie is reported as ambiguous
and a read that aligns to neither as unclassified. Nothing is dropped: the
categories add up to the total, and every share states its denominator.

The molar and mass shares are estimates from aligned bases divided by molecule
length. They are labelled as estimates and have not been checked against a
mixture of known composition.

Inputs are SAM text (samtools view -h). Standard library only.
"""
from __future__ import annotations

import argparse
import gzip
import json
import re
from pathlib import Path

SCHEMA = "bms.ngs.host-composition.v1"
CATEGORIES = ("plasmid", "host", "ambiguous", "unclassified")
_CIGAR = re.compile(r"(\d+)([MIDNSHP=X])")


def population(path: Path) -> dict[str, int]:
    """Read name to length for every read in the run's FASTQ."""
    opener = gzip.open if path.name.endswith(".gz") else open
    reads: dict[str, int] = {}
    with opener(path, "rt", encoding="utf-8", errors="replace") as handle:
        name = ""
        for index, line in enumerate(handle):
            if index % 4 == 0:
                name = line[1:].split()[0] if len(line) > 1 else ""
            elif index % 4 == 1:
                reads[name] = len(line.rstrip("\r\n"))
    return reads


def aligned_bases(path: Path) -> tuple[dict[str, int], dict[str, int]]:
    """Per read, the query bases placed by primary and supplementary records; and the reference lengths."""
    bases: dict[str, int] = {}
    references: dict[str, int] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            if line.startswith("@"):
                if line.startswith("@SQ"):
                    fields = dict(item.split(":", 1) for item in line.rstrip("\n").split("\t")[1:] if ":" in item)
                    if "SN" in fields and fields.get("LN", "").isdigit():
                        references[fields["SN"]] = int(fields["LN"])
                continue
            cells = line.split("\t", 6)
            if len(cells) < 6 or not cells[1].isdigit():
                continue
            flag = int(cells[1])
            if flag & 0x4 or flag & 0x100 or cells[5] == "*":
                continue
            placed = sum(int(count) for count, operation in _CIGAR.findall(cells[5]) if operation in "M=XI")
            bases[cells[0]] = bases.get(cells[0], 0) + placed
    return bases, references


def classify(reads: dict[str, int], plasmid: dict[str, int], host: dict[str, int]) -> dict:
    counts = {name: {"reads": 0, "bases": 0} for name in CATEGORIES}
    aligned = {"plasmid": 0, "host": 0}
    both = 0
    for name, length in reads.items():
        # Overlapping supplementary records cannot place more bases than the read has.
        on_plasmid = min(length, plasmid.get(name, 0))
        on_host = min(length, host.get(name, 0))
        if on_plasmid and on_host:
            both += 1
        if on_plasmid > on_host:
            category = "plasmid"
            aligned["plasmid"] += on_plasmid
        elif on_host > on_plasmid:
            category = "host"
            aligned["host"] += on_host
        elif on_plasmid:
            category = "ambiguous"
        else:
            category = "unclassified"
        counts[category]["reads"] += 1
        counts[category]["bases"] += length
    return {"counts": counts, "aligned_bases": aligned, "reads_aligning_to_both": both}


def build(args: argparse.Namespace) -> dict:
    reads = population(args.reads_fastq)
    plasmid, plasmid_references = aligned_bases(args.plasmid_sam)
    host, host_references = aligned_bases(args.host_sam)
    result = classify(reads, plasmid, host)
    total_reads = len(reads)
    total_bases = sum(reads.values())
    share = lambda value, total: value / total if total else None
    categories = {
        name: {**result["counts"][name],
               "read_fraction": share(result["counts"][name]["reads"], total_reads),
               "base_fraction": share(result["counts"][name]["bases"], total_bases)}
        for name in CATEGORIES
    }
    plasmid_length = args.plasmid_length or sum(plasmid_references.values())
    host_length = sum(host_references.values())
    plasmid_aligned, host_aligned = result["aligned_bases"]["plasmid"], result["aligned_bases"]["host"]
    estimate = None
    if plasmid_length and host_length and plasmid_aligned + host_aligned:
        plasmid_molecules = plasmid_aligned / plasmid_length
        host_molecules = host_aligned / host_length
        estimate = {
            "host_mass_fraction": host_aligned / (plasmid_aligned + host_aligned),
            "host_molar_fraction": host_molecules / (plasmid_molecules + host_molecules),
            "plasmid_length_bp": plasmid_length,
            "host_length_bp": host_length,
            "method": "aligned bases divided by molecule length",
            "assumptions": [
                "Every base of plasmid and host DNA is equally likely to be sequenced.",
                "Mass is proportional to aligned bases; the number of molecules is aligned bases divided by molecule length.",
                "Ambiguous and unclassified reads are left out of the estimate.",
            ],
            "checked_against_known_mixture": False,
        }
    return {
        "schema": SCHEMA,
        "sample": args.sample,
        "host_reference": {"records": len(host_references), "length_bp": host_length,
                           "names": sorted(host_references)[:8]},
        "denominator": {"total_reads": total_reads, "total_bases": total_bases, "source": args.reads_fastq.name},
        "categories": categories,
        "reads_aligning_to_both": result["reads_aligning_to_both"],
        "rule": "A read goes to the plasmid or the host, whichever holds more of its aligned bases. Equal is ambiguous; neither is unclassified.",
        "estimate": estimate,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sample", required=True)
    parser.add_argument("--reads-fastq", required=True, type=Path)
    parser.add_argument("--plasmid-sam", required=True, type=Path)
    parser.add_argument("--host-sam", required=True, type=Path)
    parser.add_argument("--plasmid-length", type=int, default=0)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    document = build(args)
    args.out.write_text(json.dumps(document, sort_keys=True) + "\n", encoding="utf-8")
    host = document["categories"]["host"]
    print(f"Host genomic DNA: {host['reads']} of {document['denominator']['total_reads']} reads")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
