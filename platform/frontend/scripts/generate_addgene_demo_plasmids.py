#!/usr/bin/env python3

from __future__ import annotations

import html
import json
import re
from collections import OrderedDict
from pathlib import Path
from typing import Any

import requests


ROOT = Path(__file__).resolve().parents[1]
OUTPUT_PATH = ROOT / "src/components/MolBioToolkit/demoConstructs.generated.ts"

PLASMIDS: list[dict[str, str]] = [
    {"plasmid_id": "52961", "name": "lentiCRISPR v2"},
    {"plasmid_id": "12260", "name": "psPAX2"},
    {"plasmid_id": "12259", "name": "pMD2.G"},
    {"plasmid_id": "85492", "name": "pET28a-sfGFP"},
    {"plasmid_id": "26094", "name": "pET28a-LIC"},
    {"plasmid_id": "128034", "name": "pcDNA3.1-HA"},
    {"plasmid_id": "86470", "name": "pYES2-HTH"},
    {"plasmid_id": "186478", "name": "pCambia-PUP-IT"},
    {"plasmid_id": "100047", "name": "pAAV.CAG.LSL.EGFP"},
    {"plasmid_id": "98927", "name": "pENN.AAV.CAG.Flex.GFPsm_myc.WPRE.SV40"},
    {"plasmid_id": "113194", "name": "PX458-AAVS1"},
    {"plasmid_id": "110403", "name": "pX330.puro"},
    {"plasmid_id": "203312", "name": "PiggyBac PlayBack"},
    {"plasmid_id": "39196", "name": "pHIV-Luc-ZsGreen"},
    {"plasmid_id": "231890", "name": "mCherry-NLS-mCherry"},
    {"plasmid_id": "165422", "name": "pGoldenGreenGate-M (pGGG-M)"},
    {"plasmid_id": "109218", "name": "MoClo adapted pUC57-Kan Level 2"},
    {"plasmid_id": "109221", "name": "MoClo adapted pUC57-Amp Level 1 position 2"},
    {"plasmid_id": "194220", "name": "MTK Lentivirus Single Cassette Destination Vector (pAN2414)"},
    {"plasmid_id": "194221", "name": "MTK Lentivirus Multi Cassette Destination Vector (pAN2424)"},
]

SESSION = requests.Session()
SESSION.headers.update({
    "User-Agent": "BioModStack Demo Importer/1.0",
})

SEQUENCE_ID_PATTERN = re.compile(r'href="/browse/sequence/(\d+)/"')
QUERY_PATTERN = re.compile(r'name="QUERY" value="([ACGT]+)"')
ASSET_PATTERNS = {
    "features": re.compile(r'features:\s*"([^"]+features\.json)"'),
    "primers": re.compile(r'primers:\s*"([^"]+primers\.json)"'),
    "orfs": re.compile(r'orfs:\s*"([^"]+orfs\.json)"'),
}


def dedupe_preserve_order(values: list[str]) -> list[str]:
    return list(OrderedDict.fromkeys(values))


def fetch_text(url: str) -> str:
    response = SESSION.get(url, timeout=60)
    response.raise_for_status()
    return response.text


def fetch_json(url: str) -> Any:
    response = SESSION.get(url, timeout=60)
    response.raise_for_status()
    return response.json()


def clean_asset_url(raw_url: str) -> str:
    return html.unescape(raw_url).replace("\\u002D", "-")


def prune_none(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: prune_none(item)
            for key, item in value.items()
            if item is not None
        }
    if isinstance(value, list):
        return [prune_none(item) for item in value]
    return value


def strip_html(value: str) -> str:
    no_tags = re.sub(r"<[^>]+>", "", value or "")
    no_space = html.unescape(no_tags).replace("\xa0", " ")
    return re.sub(r"\s+", " ", no_space).strip()


def first_meaningful_attribute(attributes: dict[str, Any]) -> str | None:
    preferred_keys = [
        "label",
        "gene",
        "product",
        "note",
        "function",
        "source",
        "translation",
    ]
    for key in preferred_keys:
        value = attributes.get(key)
        if isinstance(value, list) and value:
            return value[0]
        if isinstance(value, str) and value:
            return value
    return None


def normalize_notes(raw_attributes: dict[str, Any]) -> dict[str, Any] | None:
    notes: dict[str, Any] = {}
    for key, value in raw_attributes.items():
        if not value:
            continue
        if isinstance(value, list):
            cleaned = [strip_html(str(item)) for item in value if strip_html(str(item))]
            if cleaned:
                notes[key] = cleaned[:4]
        else:
            cleaned = strip_html(str(value))
            if cleaned:
                notes[key] = cleaned
    return notes or None


def feature_color(feature: dict[str, Any]) -> str | None:
    for segment in feature.get("segments") or []:
        color = segment.get("color")
        if color:
            return color
    return None


def map_feature(plasmid_id: str, feature: dict[str, Any]) -> dict[str, Any]:
    attributes = normalize_notes(feature.get("attributes") or {}) or {}
    start = max(0, int(feature.get("rangeBegin", 1)) - 1)
    end = int(feature.get("rangeEnd", start))
    strand = -1 if "reverse" in (feature.get("direction") or "").lower() else 1
    name = strip_html(feature.get("name") or feature.get("type") or "feature")
    mapped = {
        "id": f"addgene-{plasmid_id}-feature-{feature.get('id', name)}",
        "name": name,
        "type": feature.get("type") or "misc_feature",
        "start": start,
        "end": end,
        "strand": strand,
        "color": feature_color(feature),
        "description": first_meaningful_attribute(attributes),
        "notes": attributes or None,
    }
    return mapped


def normalize_feature_label(value: str | None) -> str:
    return re.sub(r"\s+", " ", (value or "").strip()).lower()


def normalize_note_values(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        merged: list[str] = []
        for item in value:
            merged.extend(normalize_note_values(item))
        return merged
    normalized = str(value).strip()
    return [normalized] if normalized else []


def merge_note_values(existing: Any, incoming: Any) -> Any:
    merged = dedupe_preserve_order([
        *normalize_note_values(existing),
        *normalize_note_values(incoming),
    ])
    if not merged:
        return None
    if len(merged) == 1:
        return merged[0]
    return merged


def merge_feature_notes(
    existing_notes: dict[str, Any] | None,
    incoming_notes: dict[str, Any] | None,
) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    for key, value in (existing_notes or {}).items():
        next_value = merge_note_values(None, value)
        if next_value is not None:
            merged[key] = next_value
    for key, value in (incoming_notes or {}).items():
        next_value = merge_note_values(merged.get(key), value)
        if next_value is not None:
            merged[key] = next_value
    return merged or None


def feature_identity_key(feature: dict[str, Any]) -> tuple[str, str, int, int, int]:
    return (
        normalize_feature_label(feature.get("name")),
        normalize_feature_label(feature.get("type")),
        int(feature.get("start", 0)),
        int(feature.get("end", 0)),
        int(feature.get("strand", 1)),
    )


def merge_feature_records(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    existing_description = (existing.get("description") or "").strip()
    incoming_description = (incoming.get("description") or "").strip()
    return {
        **existing,
        "id": existing.get("id") or incoming.get("id"),
        "name": existing.get("name") or incoming.get("name"),
        "type": existing.get("type") or incoming.get("type"),
        "color": existing.get("color") or incoming.get("color"),
        "description": incoming_description
        if len(incoming_description) > len(existing_description)
        else (existing_description or None),
        "notes": merge_feature_notes(existing.get("notes"), incoming.get("notes")),
    }


def dedupe_features(features: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged_by_key: OrderedDict[tuple[str, str, int, int, int], dict[str, Any]] = OrderedDict()
    for feature in features:
        key = feature_identity_key(feature)
        existing = merged_by_key.get(key)
        if existing is None:
            merged_by_key[key] = feature
            continue
        merged_by_key[key] = merge_feature_records(existing, feature)
    return list(merged_by_key.values())


def parse_percent_gc(raw_percent: str | None) -> float | None:
    if not raw_percent:
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)?)", raw_percent)
    return float(match.group(1)) if match else None


def map_primers(plasmid_id: str, primers_payload: dict[str, Any]) -> list[dict[str, Any]]:
    mapped: list[dict[str, Any]] = []
    for primer in primers_payload.get("primers") or []:
        sites = primer.get("sites") or []
        if not sites:
            continue
        primary_site = sites[0]
        mapped.append({
            "id": f"addgene-{plasmid_id}-primer-{primer.get('id', len(mapped))}",
            "name": primer.get("name") or f"Primer {len(mapped) + 1}",
            "sequence": (primer.get("sequence") or "").upper(),
            "sequenceType": "dna",
            "start": max(0, int(primary_site.get("bindingSiteStart", 1)) - 1),
            "end": int(primary_site.get("bindingSiteEnd", 0)),
            "strand": 1 if (primary_site.get("strand") or "").lower() == "top" else -1,
            "tm": primary_site.get("meltingTemperature"),
            "gc_percent": parse_percent_gc(primer.get("percentGC")),
        })
    return mapped


def first_full_sequence_id(plasmid_id: str) -> str:
    page = fetch_text(f"https://www.addgene.org/{plasmid_id}/sequences/")
    full_section_match = re.search(r'<section id="addgene-full".*?</section>', page, re.S)
    search_space = full_section_match.group(0) if full_section_match else page
    sequence_ids = dedupe_preserve_order(SEQUENCE_ID_PATTERN.findall(search_space))
    if not sequence_ids:
        raise RuntimeError(f"No public browse sequence found for Addgene plasmid {plasmid_id}")
    return sequence_ids[0]


def browse_sequence_payload(plasmid_id: str, sequence_id: str, label: str) -> dict[str, Any]:
    page = fetch_text(f"https://www.addgene.org/browse/sequence/{sequence_id}/")
    query_match = QUERY_PATTERN.search(page)
    if not query_match:
        raise RuntimeError(f"No public sequence payload found for Addgene plasmid {plasmid_id} sequence {sequence_id}")

    urls: dict[str, str] = {}
    for key, pattern in ASSET_PATTERNS.items():
        match = pattern.search(page)
        if not match:
            raise RuntimeError(f"Missing {key} asset URL for Addgene plasmid {plasmid_id} sequence {sequence_id}")
        urls[key] = clean_asset_url(match.group(1))

    features_payload = fetch_json(urls["features"])
    primers_payload = fetch_json(urls["primers"])

    mapped_features = dedupe_features([
        map_feature(plasmid_id, feature) for feature in features_payload.get("features") or []
    ])
    mapped_primers = map_primers(plasmid_id, primers_payload)

    return {
        "name": label,
        "description": f"Real Addgene plasmid #{plasmid_id}, sequence #{sequence_id}, imported from the public Addgene browse sequence page.",
        "sequence": query_match.group(1),
        "circular": True,
        "sequenceType": "dna",
        "features": mapped_features,
        "primers": mapped_primers,
        "translations": [],
    }


def generate_module(plasmids: list[dict[str, Any]]) -> str:
    payload = json.dumps(prune_none(plasmids), indent=2, ensure_ascii=True)
    return (
        "/*\n"
        " * Generated from public Addgene browse sequence pages.\n"
        " * Run `python3 platform/frontend/scripts/generate_addgene_demo_plasmids.py` to refresh.\n"
        " */\n\n"
        "import type { SequenceData } from './types';\n\n"
        f"export const DEMO_PLASMIDS: SequenceData[] = {payload};\n"
    )


def main() -> None:
    generated: list[dict[str, Any]] = []
    for plasmid in PLASMIDS:
        plasmid_id = plasmid["plasmid_id"]
        sequence_id = first_full_sequence_id(plasmid_id)
        generated.append(browse_sequence_payload(plasmid_id, sequence_id, plasmid["name"]))

    OUTPUT_PATH.write_text(generate_module(generated))
    print(f"Wrote {len(generated)} demo plasmids to {OUTPUT_PATH}")


if __name__ == "__main__":
    main()
