#!/usr/bin/env python3
"""Read-only SAM modified-tag admission shared by workflow and completion.

Sources: pysam v0.23.3/htslib sam_mods.c (bam_parse_basemod2,
bam_next_basemod); modkit 0.6.4 cd85862 mod_bam.rs:1419-1519.
SAM permits empty lists, absent ML, legacy Mm/Ml, skip flags and ChEBI
codes. Missing evidence is not malformed evidence. Modkit separately owns
probability inference, implicit canonical calls, filtering and site usability.
"""
from __future__ import annotations

from array import array
import argparse
import copy
import re
import sys

import pysam

# Lexical envelope only. Coordinates/orientation are decoded by pinned htslib.
_GROUP = re.compile(r"([ACGTUN])([+-])([A-Za-z]+|[0-9]+)([.?]?)((?:,[0-9]+)*);")


class ModifiedBaseAdmissionError(ValueError):
    pass


def require_parser():
    from pysam import version
    if version.__htslib_version__ != "1.21":
        raise ModifiedBaseAdmissionError("modified-tag parser requires pysam 0.23.3 / htslib 1.21")
    if version.__version__ == "0.23.3":
        return
    if version.__version__ == "0.23.3+bms1":
        # The API uses the same scientific parser with the reviewed transport
        # patch. Admit that exact build through its existing source/hash gate,
        # not an unverified local-version suffix or a second identity policy.
        from pathlib import Path
        api_root = str(Path(__file__).resolve().parents[1] / "platform" / "api")
        if api_root not in sys.path:
            sys.path.insert(0, api_root)
        try:
            from services.verified_native_reads import require_runtime
            require_runtime()
        except Exception as exc:
            raise ModifiedBaseAdmissionError("modified-tag parser native build is unverified") from exc
        return
    raise ModifiedBaseAdmissionError("modified-tag parser requires pysam 0.23.3 / htslib 1.21")


def inspect_record(read):
    """Validate SAM syntax/cardinality/positions without a Dorado model whitelist.

    pysam 0.23.3's modified_bases allocates mods[5] but iterates the native
    returned count. Never feed it an arbitrary external multi-code record:
    project each MM group onto ONE placeholder code and its first ML column.
    Codes share the group's deltas. Native htslib still decodes all positions,
    reverse orientation, skip flags and sequence bounds; no source is mutated.
    Lexical/cardinality checks close native strtol's permissive numeric parsing.
    """
    tags = {}
    for key, value, kind in read.get_tags(with_value_type=True):
        if key not in {"MM", "Mm", "ML", "Ml", "MN"}:
            continue
        if key in tags:
            raise ModifiedBaseAdmissionError("duplicate modified-base auxiliary tag")
        tags[key] = (value, kind)
    mm = tags.get("MM", tags.get("Mm"))
    ml = tags.get("ML", tags.get("Ml"))
    mn = tags.get("MN")
    if mn is not None and (mn[1] not in "cCsSiI" or type(mn[0]) is not int or mn[0] != read.query_length):
        raise ModifiedBaseAdmissionError("MN must be an integer matching SEQ length")
    if ml is not None and (ml[1] != "B" or not isinstance(ml[0], array) or ml[0].typecode != "B"):
        raise ModifiedBaseAdmissionError("ML must be unsigned-byte B:C")
    if mm is None:
        if ml is not None:
            raise ModifiedBaseAdmissionError("ML has no corresponding MM tag")
        return {"paired": False, "explicit_calls": 0, "informative": False, "available": {}}
    if mm[1] != "Z" or not isinstance(mm[0], str):
        raise ModifiedBaseAdmissionError("MM must be a Z string")
    groups = []
    offset = cardinality = 0
    available = {}
    for match in _GROUP.finditer(mm[0]):
        if match.start() != offset:
            raise ModifiedBaseAdmissionError("malformed MM group")
        offset = match.end()
        base, strand, raw_codes, skip, deltas = match.groups()
        codes = [str(int(raw_codes))] if raw_codes.isdigit() else list(raw_codes)
        if raw_codes.isdigit() and int(raw_codes) > 4294967295:
            raise ModifiedBaseAdmissionError("MM ChEBI code exceeds modkit's unsigned 32-bit representation")
        positions = deltas.count(",")
        if any(int(value) > 4294967295 for value in deltas.split(",")[1:]):
            raise ModifiedBaseAdmissionError("MM delta exceeds unsigned 32-bit representation")
        for canonical in ("ACGT" if base == "N" else "T" if base == "U" else base):
            available.setdefault(canonical, set()).update(codes)
        groups.append((base, strand, skip, deltas, positions, len(codes), cardinality))
        cardinality += positions * len(codes)
    if offset != len(mm[0]):
        raise ModifiedBaseAdmissionError("malformed or unterminated MM group")
    if ml is not None and len(ml[0]) != cardinality:
        raise ModifiedBaseAdmissionError("MM/ML cardinality mismatch")
    sequence = read.get_forward_sequence() or ""
    informative = False
    for base, strand, skip, deltas, positions, stride, start in groups:
        # No sequence is a source-valid unusable record in modkit, rather than
        # proof of a coordinate assignment. Validate lexical tags, then skip it.
        if not sequence:
            continue
        probe = copy.copy(read)
        for tag in ("MM", "Mm", "ML", "Ml"):
            if probe.has_tag(tag):
                probe.set_tag(tag, None)
        probe.set_tag("MM", f"{base}{strand}m{skip}{deltas};", value_type="Z")
        if ml is not None:
            probe.set_tag("ML", array("B", ml[0][start:start + positions * stride:stride]))
        decoded = probe.modified_bases
        if decoded is None or sum(len(values) for values in decoded.values()) != positions:
            raise ModifiedBaseAdmissionError("MM coordinates are invalid for SEQ")
        canonical_present = bool(sequence) if base == "N" else ("T" if base == "U" else base) in sequence.upper()
        # This is potential source evidence, NOT a pileup-site count. Empty
        # implicit groups may carry canonical evidence; '?' does not imply it.
        informative |= positions > 0 or (skip != "?" and canonical_present)
    eligible = bool(sequence) and (not (read.is_secondary or read.is_supplementary) or mn is not None)
    return {"paired": ml is not None, "explicit_calls": cardinality,
            "informative": bool(informative and eligible and ml is not None), "available": available}


def inspect_bam(bam):
    require_parser()
    total = mapped = paired = explicit = informative = 0
    available = {}
    for ordinal, read in enumerate(bam.fetch(until_eof=True), 1):
        try:
            record = inspect_record(read)
        except (ValueError, TypeError, OverflowError) as exc:
            raise ModifiedBaseAdmissionError(f"malformed_modified_base_tags: record {ordinal}: {exc}") from exc
        total += 1
        mapped += not read.is_unmapped
        paired += record["paired"]
        explicit += record["explicit_calls"]
        informative += record["informative"]
        for base, codes in record["available"].items():
            available.setdefault(base, set()).update(codes)
    receipt = {
        "tag_validation_schema": "bms.ngs.modified-base-admission.v1",
        "tag_parser": "pysam-0.23.3_htslib-1.21",
        "total_records": str(total), "mapped_records": str(mapped),
        "modified_base_tagged_records": str(paired), "explicit_modification_calls": str(explicit),
        "informative_tagged_records": str(informative),
        "tag_evidence_state": "valid_informative_tags" if informative else "valid_no_informative_tags",
    }
    return receipt, available


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bam")
    args = parser.parse_args()
    try:
        with pysam.AlignmentFile(args.bam, "rb", check_sq=False) as bam:
            if not bam.is_bam:
                raise ModifiedBaseAdmissionError("input is not BAM")
            receipt, _ = inspect_bam(bam)
        for key, value in receipt.items():
            print(f"{key}={value}")
        # Missing/empty evidence is deliberately not an admission error. Native
        # modkit determines whether this valid source produces usable sites.
        return 0
    except (OSError, ValueError, TypeError) as exc:
        print(f"ERROR: modified-base semantic admission failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
