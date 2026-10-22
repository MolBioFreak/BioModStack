#!/usr/bin/env python3
"""Run local ESMFold2 prediction and publish real BMS artifacts.

This wrapper deliberately does not use the Biohub remote API and defaults to
``local_files_only=True`` so a launch fails closed when the ESMFold2/ESMC-6B
cache is absent. It never creates demo structures.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence


AMINO_ACID_RE = re.compile(r"^[A-Z]+$")
DNA_RE = re.compile(r"^[ACGTUN]+$")
RNA_RE = re.compile(r"^[ACGTUN]+$")
MMCIF_DATA_BLOCK_SAFE_RE = re.compile(r"[^A-Za-z0-9_.-]+")
DEFAULT_FAST_MODEL = "biohub/ESMFold2-Fast"
DEFAULT_FULL_MODEL = "biohub/ESMFold2"
NATIVE_SCALAR_DIALECT = {
    'name': 'biohub_esmfold2_token_scalar_v1',
    'esm_commit': 'c94ed8d763bbd7088b296949e5b401e8ea12073a',
    'transformers_commit': '3a8956fb4d4ea16b0ec8e71deef2c2909b6a5cbf',
}

PROTEIN_3TO1 = {
    "ALA": "A",
    "ARG": "R",
    "ASN": "N",
    "ASP": "D",
    "CYS": "C",
    "GLN": "Q",
    "GLU": "E",
    "GLY": "G",
    "HIS": "H",
    "ILE": "I",
    "LEU": "L",
    "LYS": "K",
    "MET": "M",
    "MSE": "M",
    "PHE": "F",
    "PRO": "P",
    "SEC": "U",
    "SER": "S",
    "THR": "T",
    "TRP": "W",
    "TYR": "Y",
    "VAL": "V",
    "PYL": "O",
    "ASX": "B",
    "GLX": "Z",
    "UNK": "X",
}
DNA_3TO1 = {
    "DA": "A",
    "DC": "C",
    "DG": "G",
    "DT": "T",
    "DI": "N",
    "A": "A",
    "C": "C",
    "G": "G",
    "T": "T",
    "I": "N",
}
RNA_3TO1 = {
    "A": "A",
    "C": "C",
    "G": "G",
    "U": "U",
    "I": "N",
    "RA": "A",
    "RC": "C",
    "RG": "G",
    "RU": "U",
}


class ESMFold2InputError(ValueError):
    """Operator-facing ESMFold2 input error."""


def parse_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off"}:
        return False
    raise argparse.ArgumentTypeError(f"expected boolean value, got {value!r}")


def _strip_sequence(sequence: str) -> str:
    return re.sub(r"\s+", "", sequence or "").upper()


def normalize_sequence(sequence: str) -> str:
    """Normalize one protein chain sequence.

    Multi-chain separators are deliberately not accepted here; use
    complex_components_json/PDB/DNA/RNA/ligand fields for real complexes.
    """
    normalized = _strip_sequence(sequence)
    if not normalized:
        raise ESMFold2InputError("sequence is required")
    if any(separator in normalized for separator in (":", ",", ";", "/")):
        raise ESMFold2InputError(
            "protein sequence must be one chain; use Components JSON or PDB sequence source for complexes"
        )
    if not AMINO_ACID_RE.match(normalized):
        raise ESMFold2InputError("protein sequence must contain amino-acid letters only")
    return normalized


def normalize_component_sequence(sequence: str, molecule_type: str) -> str:
    mol_type = (molecule_type or "protein").strip().lower()
    normalized = _strip_sequence(sequence)
    if not normalized:
        raise ESMFold2InputError(f"{mol_type} sequence is required")
    if any(separator in normalized for separator in (":", ",", ";", "/")):
        raise ESMFold2InputError(f"{mol_type} sequence must be one chain")
    if mol_type in {"protein", "peptide"}:
        if not AMINO_ACID_RE.match(normalized):
            raise ESMFold2InputError("protein sequence must contain amino-acid letters only")
        return normalized
    if mol_type == "dna":

        if not DNA_RE.match(normalized):
            raise ESMFold2InputError("DNA sequence must contain only A/C/G/T/N bases")
        return normalized
    if mol_type == "rna":

        if not RNA_RE.match(normalized):
            raise ESMFold2InputError("RNA sequence must contain only A/C/G/U/N bases")
        return normalized
    raise ESMFold2InputError(f"unsupported molecule type {molecule_type!r}")


def sanitize_mmcif_data_block_id(value: object, fallback: str = "esmfold2_prediction") -> str:
    """Return a single-token mmCIF data block id.

    Biohub ESMFold2 uses ``complex_id`` as the mmCIF ``data_`` block name.
    UI labels such as ``RCSB: 3KTQ`` are valid display names but invalid mmCIF
    block identifiers because whitespace creates an extra token. Mol*/PDBe then
    fails parsing before any model/structure reaches the viewer.
    """
    text = str(value or "").strip() or fallback
    safe = MMCIF_DATA_BLOCK_SAFE_RE.sub("_", text).strip("._-")
    if not safe:
        safe = fallback
    if not re.match(r"^[A-Za-z0-9]", safe):
        safe = f"{fallback}_{safe}"
    return safe[:120]


def ensure_safe_mmcif_data_block(mmcif_text: str, data_block_id: object) -> str:
    """Replace the leading mmCIF data block line with a parser-safe token."""
    safe_id = sanitize_mmcif_data_block_id(data_block_id)
    lines = mmcif_text.splitlines()
    if lines and lines[0].startswith("data_"):
        lines[0] = f"data_{safe_id}"
        suffix = "\n" if mmcif_text.endswith("\n") else ""
        return "\n".join(lines) + suffix
    return f"data_{safe_id}\n{mmcif_text}"


def default_model_for_variant(variant: str) -> str:
    normalized = (variant or "fast").strip().lower()
    if normalized == "full":
        return DEFAULT_FULL_MODEL
    if normalized == "fast":
        return DEFAULT_FAST_MODEL
    raise ESMFold2InputError("model_variant must be one of: fast, full")


def parse_chain_ids(value: object) -> str | list[str]:
    if isinstance(value, list):
        ids = [str(item).strip() for item in value if str(item).strip()]
        if not ids:
            raise ESMFold2InputError("component id list cannot be empty")
        return ids
    text = str(value or "").strip()
    if not text:
        raise ESMFold2InputError("component chain id is required")
    tokens = [token.strip() for token in re.split(r"[,\s]+", text) if token.strip()]
    if not tokens:
        raise ESMFold2InputError("component chain id is required")
    return tokens if len(tokens) > 1 else tokens[0]


def iter_chain_ids(value: str | list[str]) -> Iterable[str]:
    if isinstance(value, list):
        yield from (str(item).strip() for item in value if str(item).strip())
    else:
        text = str(value).strip()
        if text:
            yield text


def parse_ccd_list(value: object) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, list):
        ccd = [str(item).strip().upper() for item in value if str(item).strip()]
    else:
        ccd = [token.strip().upper() for token in re.split(r"[,\s]+", str(value)) if token.strip()]
    return ccd or None


def parse_components_json(value: str | None) -> list[dict[str, Any]]:
    if not value or not str(value).strip():
        return []
    try:
        decoded = json.loads(str(value))
    except json.JSONDecodeError as exc:
        raise ESMFold2InputError(f"complex_components_json is not valid JSON: {exc}") from exc
    if isinstance(decoded, dict):
        decoded = decoded.get("components", decoded.get("sequences"))
    if not isinstance(decoded, list):
        raise ESMFold2InputError("complex_components_json must be a JSON array or object with components[]")
    components: list[dict[str, Any]] = []
    for index, component in enumerate(decoded, start=1):
        if not isinstance(component, dict):
            raise ESMFold2InputError(f"component {index} must be an object")
        normalized = dict(component)
        mol_type = str(normalized.get("type") or normalized.get("molecule_type") or "protein").strip().lower()
        if mol_type == "peptide":
            mol_type = "protein"
        if mol_type in {"small_molecule", "smiles", "ccd"}:
            mol_type = "ligand"
        if mol_type not in {"protein", "dna", "rna", "ligand"}:
            raise ESMFold2InputError(
                f"component {index} has unsupported type {normalized.get('type')!r}; use protein/dna/rna/ligand"
            )
        normalized["type"] = mol_type
        components.append(normalized)
    return components


def load_components_file(path: str | None) -> list[dict[str, Any]]:
    if not path or not str(path).strip():
        return []
    component_path = Path(str(path)).expanduser()
    if not component_path.exists():
        raise ESMFold2InputError(f"complex components file not found: {component_path}")
    return parse_components_json(component_path.read_text(encoding="utf-8"))


def _classify_pdb_chain(residue_names: Sequence[str]) -> str | None:
    names = [name.upper().strip() for name in residue_names if name.strip()]
    protein_hits = sum(1 for name in names if protein_residue_letter(name))
    dna_hits = sum(1 for name in names if name in DNA_3TO1 and (name.startswith("D") or name == "T"))
    rna_hits = sum(1 for name in names if name in RNA_3TO1 and (name.startswith("R") or name == "U"))
    nucleotide_hits = sum(1 for name in names if name in DNA_3TO1 or name in RNA_3TO1)
    if protein_hits and protein_hits >= nucleotide_hits:
        return "protein"
    if dna_hits:
        return "dna"
    if rna_hits:
        return "rna"
    if nucleotide_hits:
        # Ambiguous A/C/G-only nucleotide chains are rare in PDB; RNA is the safer
        # no-thymine fallback because unprefixed DNA usually carries DT/DA/DC/DG.
        return "rna"
    return None


def protein_residue_letter(name: str) -> str | None:
    # Biopython's CCD-derived parent table, not a hand-invented substitution.
    from Bio.Data.PDBData import protein_letters_3to1_extended
    return PROTEIN_3TO1.get(name) or protein_letters_3to1_extended.get(name)


def _map_residue_to_letter(residue_name: str, molecule_type: str) -> str | None:
    name = residue_name.upper().strip()
    if molecule_type == "protein":
        return protein_residue_letter(name)
    if molecule_type == "dna":
        return DNA_3TO1.get(name)
    if molecule_type == "rna":
        return RNA_3TO1.get(name)
    return None


def parse_pdb_polymer_components(
    path: str | Path,
    *,
    chain_ids: str | Sequence[str] | None = None,
    include_dna_rna: bool = True,
    include_ligands: bool = False,
    _author_residue_rows: dict | None = None,
) -> list[dict[str, Any]]:
    """Extract protein/DNA/RNA sequence components from a local PDB file.

    This is a sequence-source bridge for the launcher; it does not claim to use
    PDB coordinates as templates. Coordinates stay out of the ESMFold2 SPI unless
    upstream exposes a supported template-conditioning contract.
    """
    pdb_path = Path(path).expanduser()
    if not pdb_path.exists():
        raise ESMFold2InputError(f"PDB sequence source not found: {pdb_path}")
    selected = None
    if chain_ids:
        if isinstance(chain_ids, str):
            selected = {token.strip() for token in re.split(r"[,\s]+", chain_ids) if token.strip()}
        else:
            selected = {str(token).strip() for token in chain_ids if str(token).strip()}

    chains: dict[str, list[tuple[str, str]]] = {}
    seen_residues: set[tuple[str, str, str]] = set()
    polymer_records = set()
    # The round author's former second read was strict UTF-8. Keep that input
    # behavior for the opt-in projection, without changing the default bridge.
    errors = "strict" if _author_residue_rows is not None else "replace"
    for line in pdb_path.read_text(encoding="utf-8", errors=errors).splitlines():
        if not (line.startswith("ATOM  ") or line.startswith("HETATM")):
            continue
        chain_id = (line[21:22].strip() or "_") if len(line) >= 22 else "_"
        if selected is not None and chain_id not in selected:
            continue
        residue_name = line[17:20].strip().upper() if len(line) >= 20 else ""
        # Optional observation for the source owner; keep its historical author
        # deduplication separate from sequence classification and fallback IDs.
        # Convert author numbers only after that owner's chain validation.
        if (_author_residue_rows is not None and chain_id in _author_residue_rows
                and _map_residue_to_letter(residue_name, "protein")):
            _author_residue_rows[chain_id].setdefault(
                (line[22:27].strip(), residue_name),
                {'chain_id': chain_id, 'residue_number': line[22:26],
                 'insertion_code': line[26:27].strip()})
        residue_id = (line[22:27].strip() or str(len(chains.get(chain_id, [])) + 1)) if len(line) >= 27 else str(len(chains.get(chain_id, [])) + 1)
        key = (chain_id, residue_id, residue_name)
        if line.startswith('ATOM  ') or protein_residue_letter(residue_name) or residue_name in DNA_3TO1 or residue_name in RNA_3TO1:
            polymer_records.add(key)
        if key in seen_residues:
            continue
        seen_residues.add(key)
        chains.setdefault(chain_id, []).append((residue_id, residue_name))

    components: list[dict[str, Any]] = []
    reserved = set(chains)
    for chain_id in chains:
        residue_names = [name for resid, name in chains[chain_id] if (chain_id, resid, name) in polymer_records]
        mol_type = _classify_pdb_chain(residue_names)
        if include_ligands:
            for resid, name in chains[chain_id]:
                if (chain_id, resid, name) in polymer_records or name in {'HOH', 'WAT', 'DOD'}:
                    continue
                ligand_id = chain_id if not residue_names and not any(c['id'] == chain_id for c in components) else _next_chain_id(reserved, '')
                reserved.add(ligand_id)
                components.append({'type': 'ligand', 'id': ligand_id, 'ccd': [name],
                    'source': str(pdb_path), 'source_chain_id': chain_id, 'source_residue_id': resid})
        if mol_type is None:
            continue
        if mol_type in {"dna", "rna"} and not include_dna_rna:
            continue
        letters = [_map_residue_to_letter(name, mol_type) or ('X' if mol_type == 'protein' else 'N') for name in residue_names]
        sequence = "".join(letters)
        standard = (set(PROTEIN_3TO1) - {'MSE', 'SEC', 'PYL'}) if mol_type == 'protein' else ({'DA', 'DC', 'DG', 'DT'} if mol_type == 'dna' else {'A', 'C', 'G', 'U'})
        modifications = [{'position': i, 'ccd': name} for i, name in enumerate(residue_names) if name not in standard]
        if not sequence:
            continue
        components.append(
            {
                "type": mol_type,
                "id": chain_id,
                "sequence": normalize_component_sequence(sequence, mol_type),
                "source": str(pdb_path),
                "modifications": modifications,
            }
        )
    if selected is not None and not components:
        raise ESMFold2InputError(f"no selected polymer chains found in {pdb_path}: {sorted(selected)}")
    return components


def native_scalar_metrics(sample: Any) -> dict:
    """Native token-mean fractions, with invalid distinct from absent/null.

    Version identity follows the two immutable provider pins in esmfold2.def.
    This declaration is source semantics, not deployed-image attestation.
    """
    import math
    import numpy as np
    result = {'scalar_dialect': dict(NATIVE_SCALAR_DIALECT), 'scalar_states': {}}
    for key, attr in (('plddt_mean', 'plddt'), ('ptm', 'ptm'), ('iptm', 'iptm')):
        native = getattr(sample, attr, None)
        value, state = None, 'unavailable'
        if native is not None:
            state = 'invalid'
            try:
                if hasattr(native, 'detach'):
                    native = native.detach().float().cpu().numpy() if str(native.dtype) != 'torch.bool' else True
                array = np.asarray(native)
                if array.dtype.kind not in 'fiu' or array.size == 0 or (key != 'plddt_mean' and array.size != 1):
                    raise ValueError('invalid native scalar type or shape')
                if not np.all(np.isfinite(array)) or not np.all((array >= 0) & (array <= 1)):
                    raise ValueError('invalid native scalar domain')
                value = float(array.mean()) if key == 'plddt_mean' else float(array.item())
                if not math.isfinite(value):
                    raise ValueError('invalid native reduction')
                state = 'ok'
            except (ValueError, TypeError, AttributeError, OverflowError):
                value = None
        result[key] = value
        result['scalar_states'][key] = state
    return result


def retain_native_confidence(sample: Any, output_dir: Path, sample_id: str) -> dict:
    """Retain optional decoded tensors without inventing a token→CIF mapping.

    The pinned decoder removes the diffusion-sample dimension. Preserve those
    arrays unchanged, including residue_index/entity_id (neither identifies a
    unique chain instance). Invalid/absent evidence is not a prediction failure.
    """
    import numpy as np
    arrays, states = {}, {}
    for key in ('plddt', 'pae', 'pair_chains_iptm', 'distogram', 'residue_index', 'entity_id'):
        value = getattr(sample, key, None)
        states[key] = 'unavailable' if value is None else 'invalid'
        if value is None:
            continue
        try:
            if hasattr(value, 'detach'):
                value = value.detach().cpu()
                # NumPy cannot represent bfloat16; conversion preserves values.
                if str(value.dtype) == 'torch.bfloat16':
                    value = value.float()
                value = value.numpy()
            array = np.asarray(value)
            if array.dtype.kind not in 'fiu' or not array.size:
                continue
            arrays[key] = array
            states[key] = 'retained'
        except (ValueError, TypeError, AttributeError, OverflowError):
            continue
    result: dict[str, Any] = {'confidence_dialect': {
        'name': 'biohub_esmfold2_native_confidence_v1',
        **{k: v for k, v in NATIVE_SCALAR_DIALECT.items() if k != 'name'}},
        'confidence_states': states}
    if arrays:
        name = f'{sample_id}.confidence.npz'
        np.savez_compressed(output_dir / name, sample_id=np.asarray(sample_id), **arrays)
        result['native_confidence'] = name
    return result


def tensor_mean(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value.detach().float().mean().cpu().item())
    except Exception:
        try:
            return float(value.mean())
        except Exception:
            return None


def scalar(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value.detach().cpu().item())
    except Exception:
        try:
            return float(value)
        except Exception:
            return None


def as_results_iter(result: Any) -> Iterable[Any]:
    if isinstance(result, list):
        return result
    return [result]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run local ESMFold2 inference for a protein or real complex input")
    parser.add_argument("--sequence", default="")
    parser.add_argument("--sequence-name", default="esmfold2_candidate")
    parser.add_argument("--chain-id", default="A")
    parser.add_argument("--complex-components-json", default="")
    parser.add_argument("--complex-components-file", default="")
    parser.add_argument("--pdb-sequence-path", default="")
    parser.add_argument("--pdb-chain-ids", default="")
    parser.add_argument("--pdb-include-dna-rna", type=parse_bool, default=True)
    parser.add_argument("--msa-path", default="")
    parser.add_argument("--msa-format", choices=["auto", "a3m", "stockholm"], default="auto")
    parser.add_argument("--msa-max-sequences", type=int, default=None)
    parser.add_argument("--msa-remove-insertions", type=parse_bool, default=True)
    parser.add_argument("--dna-sequence", default="")
    parser.add_argument("--dna-chain-id", default="C")
    parser.add_argument("--rna-sequence", default="")
    parser.add_argument("--rna-chain-id", default="D")
    parser.add_argument("--ligand-smiles", default="")
    parser.add_argument("--ligand-ccd", default="")
    parser.add_argument("--ligand-chain-id", default="L")
    parser.add_argument("--model-variant", choices=["fast", "full"], default="fast")
    parser.add_argument("--model-id-or-path", default="")
    parser.add_argument("--local-files-only", type=parse_bool, default=True)
    parser.add_argument("--num-loops", type=int, default=3)
    parser.add_argument("--num-sampling-steps", type=int, default=50)
    parser.add_argument("--num-diffusion-samples", type=int, default=1)
    parser.add_argument("--seed", type=int, default=None)
    parser.add_argument("--device", default="auto", choices=["auto", "cuda", "cpu"])
    parser.add_argument("--output-dir", required=True)
    return parser


def load_msa_for_sequence(
    msa_cls: Any,
    *,
    path: str | None,
    fmt: str = "auto",
    remove_insertions: bool = True,
    max_sequences: int | None = None,
    expected_sequence: str,
    expected_sha256: str | None = None,
    snapshot_dir: Path | None = None,
) -> tuple[Any | None, dict[str, Any] | None]:
    if not path or not str(path).strip():
        return None, None
    msa_path = Path(str(path)).expanduser()
    if not msa_path.exists():
        raise ESMFold2InputError(f"MSA file not found: {msa_path}")
    resolved_fmt = (fmt or "auto").strip().lower()
    if resolved_fmt == "auto":
        resolved_fmt = "stockholm" if msa_path.suffix.lower() in {".sto", ".stockholm"} else "a3m"
    if expected_sha256 is not None:
        # Hash and parse the same read, not a mutable pathname reopened later.
        import hashlib
        import tempfile
        data = msa_path.read_bytes()
        actual_hash = hashlib.sha256(data).hexdigest()
        if actual_hash != expected_sha256:
            raise ESMFold2InputError('MSA artifact hash disagrees with effective settings')
        with tempfile.TemporaryDirectory(prefix='msa-snapshot-', dir=snapshot_dir) as directory:
            snapshot = Path(directory) / ('input.' + resolved_fmt)
            snapshot.write_bytes(data)
            parsed, metadata = load_msa_for_sequence(msa_cls, path=str(snapshot), fmt=resolved_fmt,
                remove_insertions=remove_insertions, max_sequences=max_sequences, expected_sequence=expected_sequence)
        metadata.update(path=str(msa_path), sha256=actual_hash)
        return parsed, metadata
    if resolved_fmt == "stockholm":
        msa = msa_cls.from_stockholm(msa_path, remove_insertions=remove_insertions, max_sequences=max_sequences)
    elif resolved_fmt == "a3m":
        msa = msa_cls.from_a3m(msa_path, remove_insertions=remove_insertions, max_sequences=max_sequences)
    else:
        raise ESMFold2InputError("msa_format must be auto, a3m, or stockholm")
    query = str(msa.sequences[0]).replace("-", "").upper() if getattr(msa, "sequences", None) else ""
    expected = normalize_sequence(expected_sequence).replace("-", "")
    if query and query != expected:
        raise ESMFold2InputError(
            "MSA query sequence does not match the protein sequence after gap removal"
        )
    metadata = {
        "path": str(msa_path),
        "format": resolved_fmt,
        "depth": getattr(msa, "depth", len(getattr(msa, "sequences", []) or [])),
        "remove_insertions": remove_insertions,
        "max_sequences": max_sequences,
    }
    return msa, metadata


def _next_chain_id(used_ids: set[str], preferred: str) -> str:
    preferred = (preferred or "").strip()
    if preferred and preferred not in used_ids:
        return preferred
    for ordinal in range(ord("A"), ord("Z") + 1):
        candidate = chr(ordinal)
        if candidate not in used_ids:
            return candidate
    ordinal = 1
    while f'X{ordinal}' in used_ids:
        ordinal += 1
    return f'X{ordinal}'


def _record_ids(value: str | list[str], used_ids: set[str]) -> None:
    for chain_id in iter_chain_ids(value):
        if chain_id in used_ids:
            raise ESMFold2InputError(f"duplicate chain/component id: {chain_id}")
        used_ids.add(chain_id)


def build_structure_prediction_input(
    args: argparse.Namespace,
    *,
    ProteinInput: Any,
    DNAInput: Any,
    RNAInput: Any,
    LigandInput: Any,
    StructurePredictionInput: Any,
    MSA: Any,
) -> tuple[Any, list[dict[str, Any]]]:
    inputs: list[Any] = []
    manifest_components: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    protein_count = 0

    def native_modifications(raw):
        if raw is None:
            return None
        from esm.utils.structure.input_builder import Modification
        return [Modification(position=m['position'], ccd=m['ccd']) for m in raw]

    def add_protein(
        *,
        chain_id: object,
        sequence: str,
        source: str,
        msa_path: str | None = None,
        msa_format: str = "auto",
        msa_max_sequences: int | None = None,
        msa_remove_insertions: bool = True,
        modifications=None,
        inline_msa=None,
    ) -> None:
        nonlocal protein_count
        normalized_sequence = normalize_component_sequence(sequence, "protein")
        component_id = parse_chain_ids(chain_id)
        _record_ids(component_id, used_ids)
        msa, msa_metadata = load_msa_for_sequence(
            MSA,
            path=None if inline_msa is not None else msa_path,
            fmt=msa_format,
            remove_insertions=msa_remove_insertions,
            max_sequences=msa_max_sequences,
            expected_sequence=normalized_sequence,
            expected_sha256=getattr(args, '_msa_expected_hashes', {}).get(str(msa_path)),
            snapshot_dir=Path(args.output_dir) if getattr(args, '_msa_expected_hashes', None) else None,
        )
        if inline_msa is not None:
            msa = MSA.from_sequences(inline_msa['sequences'])
            msa_metadata = {'source': 'inline', 'depth': len(inline_msa['sequences'])}
        inputs.append(ProteinInput(id=component_id, sequence=normalized_sequence, msa=msa,
                                   modifications=native_modifications(modifications)))
        protein_count += 1
        manifest_components.append(
            {
                "type": "protein",
                "id": component_id,
                "sequence_length": len(normalized_sequence),
                "source": source,
                "msa": msa_metadata,
                "modifications": modifications,
            }
        )

    def add_polymer(*, molecule_type: str, chain_id: object, sequence: str, source: str, modifications=None) -> None:
        normalized_sequence = normalize_component_sequence(sequence, molecule_type)
        component_id = parse_chain_ids(chain_id)
        _record_ids(component_id, used_ids)
        if molecule_type == "dna":
            inputs.append(DNAInput(id=component_id, sequence=normalized_sequence, modifications=native_modifications(modifications)))
        elif molecule_type == "rna":
            inputs.append(RNAInput(id=component_id, sequence=normalized_sequence, modifications=native_modifications(modifications)))
        else:
            raise ESMFold2InputError(f"unsupported polymer type {molecule_type!r}")
        manifest_components.append(
            {
                "type": molecule_type,
                "id": component_id,
                "sequence_length": len(normalized_sequence),
                "source": source,
                "modifications": modifications,
            }
        )

    def add_ligand(*, chain_id: object, smiles: str | None, ccd: object, source: str) -> None:
        smiles_text = str(smiles or "").strip() or None
        ccd_list = parse_ccd_list(ccd)
        if not smiles_text and not ccd_list:
            raise ESMFold2InputError("ligand requires ligand_smiles or ligand_ccd")
        component_id = parse_chain_ids(chain_id)
        _record_ids(component_id, used_ids)
        inputs.append(LigandInput(id=component_id, smiles=smiles_text, ccd=ccd_list))
        manifest_components.append(
            {
                "type": "ligand",
                "id": component_id,
                "smiles_present": bool(smiles_text),
                "ccd": ccd_list,
                "source": source,
            }
        )

    if str(args.sequence or "").strip():
        add_protein(
            chain_id=args.chain_id,
            sequence=args.sequence,
            source="primary_sequence",
            msa_path=args.msa_path,
            msa_format=args.msa_format,
            msa_max_sequences=args.msa_max_sequences,
            msa_remove_insertions=args.msa_remove_insertions,
        )

    if str(args.pdb_sequence_path or "").strip():
        pdb_msa_query = None
        if args.msa_path:
            msa_format = args.msa_format
            if msa_format == 'auto':
                msa_format = 'stockholm' if Path(args.msa_path).suffix.lower() in {'.sto', '.stockholm'} else 'a3m'
            reader = MSA.from_stockholm if msa_format == 'stockholm' else MSA.from_a3m
            source_msa = reader(Path(args.msa_path), remove_insertions=args.msa_remove_insertions,
                                max_sequences=args.msa_max_sequences)
            if source_msa.sequences:
                pdb_msa_query = source_msa.sequences[0].replace('-', '').upper()
        for component in parse_pdb_polymer_components(
            args.pdb_sequence_path,
            chain_ids=args.pdb_chain_ids,
            include_dna_rna=args.pdb_include_dna_rna,
            include_ligands=True,
        ):
            if component["type"] == "protein":
                add_protein(
                    chain_id=component["id"],
                    sequence=component["sequence"],
                    source=component.get("source") or "pdb_sequence_path",
                    modifications=component.get('modifications'),
                    msa_path=args.msa_path if component['sequence'] == pdb_msa_query else None,
                    msa_format=args.msa_format,
                    msa_max_sequences=args.msa_max_sequences,
                    msa_remove_insertions=args.msa_remove_insertions,
                )
            elif component['type'] == 'ligand':
                add_ligand(chain_id=component['id'], smiles=None, ccd=component['ccd'], source=component['source'])
            else:
                add_polymer(
                    molecule_type=component["type"],
                    chain_id=component["id"],
                    sequence=component["sequence"],
                    source=component.get("source") or "pdb_sequence_path",
                    modifications=component.get('modifications'),
                )

    if str(args.dna_sequence or "").strip():
        add_polymer(
            molecule_type="dna",
            chain_id=args.dna_chain_id,
            sequence=args.dna_sequence,
            source="dna_sequence",
        )
    if str(args.rna_sequence or "").strip():
        add_polymer(
            molecule_type="rna",
            chain_id=args.rna_chain_id,
            sequence=args.rna_sequence,
            source="rna_sequence",
        )
    if str(args.ligand_smiles or "").strip() or str(args.ligand_ccd or "").strip():
        add_ligand(
            chain_id=args.ligand_chain_id,
            smiles=args.ligand_smiles,
            ccd=args.ligand_ccd,
            source="ligand_fields",
        )

    json_components = []
    conditioning = {}
    documents = []
    if args.complex_components_file:
        documents.append(Path(args.complex_components_file).read_text())
    if args.complex_components_json:
        documents.append(args.complex_components_json)
    for text in documents:
        json_components.extend(parse_components_json(text))
        document = json.loads(text)
        if isinstance(document, dict):
            for key in ('covalent_bonds', 'distogram_conditioning'):
                if document.get(key) is not None:
                    conditioning.setdefault(key, []).extend(document[key])
    reserved_ids = set(used_ids)
    for component in json_components:
        authored_ids = component.get('copy_ids') or component.get('id') or component.get('chain_id')
        if authored_ids:
            reserved_ids.update(iter_chain_ids(parse_chain_ids(authored_ids)))
    for index, component in enumerate(json_components, start=1):
        mol_type = component["type"]
        chain_id = (component.get("copy_ids") or component.get("id") or component.get("chain_id")
                    or _next_chain_id(used_ids | reserved_ids, ""))
        ids = list(iter_chain_ids(parse_chain_ids(chain_id)))
        for _ in range(max(0, int(component.get('count', len(ids))) - len(ids))):
            copy_id = _next_chain_id(reserved_ids | used_ids | set(ids), '')
            ids.append(copy_id)
            reserved_ids.add(copy_id)
        chain_id = ids if len(ids) > 1 else ids[0]
        source = str(component.get("source") or "complex_components_json")
        if mol_type == "protein":
            add_protein(
                chain_id=chain_id,
                sequence=str(component.get("sequence") or ""),
                source=source,
                msa_path=component.get("msa_path"),
                msa_format=str(component.get("msa_format") or "auto"),
                msa_max_sequences=(
                    int(component["msa_max_sequences"])
                    if component.get("msa_max_sequences") not in (None, "")
                    else None
                ),
                msa_remove_insertions=parse_bool(component.get("msa_remove_insertions", True)),
                modifications=component.get('modifications'),
                inline_msa=component.get('msa'),
            )
        elif mol_type in {"dna", "rna"}:
            add_polymer(
                molecule_type=mol_type,
                chain_id=chain_id,
                sequence=str(component.get("sequence") or ""),
                source=source,
                modifications=component.get('modifications'),
            )
        elif mol_type == "ligand":
            add_ligand(
                chain_id=chain_id,
                smiles=component.get("smiles"),
                ccd=component.get("ccd"),
                source=source,
            )
        else:  # pragma: no cover; parse_components_json guards this
            raise ESMFold2InputError(f"unsupported component type at index {index}: {mol_type}")

    if not inputs:
        raise ESMFold2InputError(
            "provide a protein sequence, a PDB sequence source, or Components JSON"
        )
    if conditioning:
        from esm.utils.structure.input_builder import deserialize_structure_prediction_input
        native = deserialize_structure_prediction_input({'sequences': [], **conditioning})
        return StructurePredictionInput(sequences=inputs, covalent_bonds=native.covalent_bonds,
            distogram_conditioning=native.distogram_conditioning), manifest_components
    return StructurePredictionInput(sequences=inputs), manifest_components


def compile_workflow_request(request: dict, staged_paths: dict) -> tuple[list[str], dict]:
    """Compile the marked workflow into the actual runner argv, without model imports.

    File mappings are supplied only by Nextflow path inputs. Top-level MSA belongs
    to the primary sequence; it is never implicitly assigned to a component.
    """
    import hashlib
    marker = request.get('core_protein_scientific_contract')
    if type(marker) is not int or marker != 1:
        raise ValueError('core_protein_scientific_contract must be integer 1')
    json.dumps(request, allow_nan=False)
    parser = build_parser()
    defaults = vars(parser.parse_args(['--output-dir', 'esmfold2_results']))
    defaults.update(num_loops=1, num_sampling_steps=5, num_diffusion_samples=1,
                    device='cuda', local_files_only=True)
    normalized = dict(request)
    for key in defaults:
        alias = 'esmf_' + key
        if alias in normalized:
            # Shared Nextflow defaults use null; native optional paths use ''.
            # Only two absent paths are equivalent, never a supplied path or a
            # false/zero/container value. Keep every other alias type-strict.
            absent_paths = (key in ('msa_path', 'pdb_sequence_path')
                            and normalized.get(key) in (None, '')
                            and normalized[alias] in (None, ''))
            if key in normalized and not absent_paths and (type(normalized[key]) is not type(normalized[alias]) or normalized[key] != normalized[alias]):
                raise ValueError(f'conflicting aliases: {key}/{alias}')
            normalized[key] = normalized[alias]
    original = normalized
    if request.get('esmf_requested_settings_json'):
        original = json.loads(request['esmf_requested_settings_json'])
        if not isinstance(original, dict):
            raise ValueError('invalid requested settings transport')
        original = {key.removeprefix('esmf_'): value for key, value in original.items()}
    settings, sources, argv = {}, [], []
    prepared_sources = {}
    if request.get('esmf_msa_preparation_json'):
        preparation = json.loads(request['esmf_msa_preparation_json'])
        for source in preparation['sources']:
            scope = source['scope']
            if scope in prepared_sources:
                raise ValueError('duplicate prepared MSA scope')
            prepared_sources[scope] = source
    integer_bounds = {'seed': (0, 2147483647), 'msa_max_sequences': (1, 10000),
                      'num_loops': (1, 12), 'num_sampling_steps': (1, 1000),
                      'num_diffusion_samples': (1, 8)}

    def checked(key, value):
        if key in integer_bounds and value is not None:
            low, high = integer_bounds[key]
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{key} must be an integer in [{low}, {high}]')
        if key in ('msa_remove_insertions', 'local_files_only', 'pdb_include_dna_rna') and type(value) is not bool:
            raise ValueError(f'{key} must be boolean')
        if key == 'msa_format' and value not in ('auto', 'a3m', 'stockholm'):
            raise ValueError('invalid msa_format')
        return value

    def stage(value, scope):
        if not isinstance(value, str) or value not in staged_paths:
            raise ValueError(f'missing staged file: {value}')
        path = Path(staged_paths[value])
        if not path.is_file():
            raise ValueError(f'missing staged file: {value}')
        data = path.read_bytes()
        if scope in prepared_sources:
            expected = prepared_sources[scope]
            sequences = ([normalized.get('sequence')] if scope == 'primary' else
                         [c.get('sequence') for c in components if 'component:' + str(c.get('id', '')) == scope])
            if len(sequences) != 1 or not isinstance(sequences[0], str) or hashlib.sha256(sequences[0].encode()).hexdigest() != expected['sequence_sha256']:
                raise ValueError('prepared MSA sequence/scope differs from controller authority')
            if (hashlib.sha256(data).hexdigest(), len(data)) != (expected['sha256'], expected['size_bytes']):
                raise ValueError('prepared MSA bytes differ from controller authority')
        sources.append({'scope': scope, 'requested_path': value, 'used_path': str(path),
                        'sha256': hashlib.sha256(data).hexdigest(), 'size_bytes': len(data)})
        return str(path)

    components = normalized.get('esmf_complex_components', normalized.get('complex_components'))
    if components is None:
        raw = normalized.get('complex_components_json')
        components = json.loads(raw) if raw else []
    document = components if isinstance(components, dict) else {}
    components = document.get('components', document.get('sequences', components))
    conditioning = {key: document[key] for key in ('covalent_bonds', 'distogram_conditioning') if key in document}
    for key in ('covalent_bonds', 'distogram_conditioning'):
        if key in normalized:
            conditioning[key] = normalized[key]
        elif 'esmf_' + key in normalized:
            conditioning[key] = normalized['esmf_' + key]
    if not isinstance(components, list):
        raise ValueError('complex_components must be a list')
    components = json.loads(json.dumps(components, allow_nan=False))
    if normalized.get('msa_path') and not normalized.get('sequence') and not normalized.get('pdb_sequence_path'):
        raise ValueError('top-level MSA requires a primary sequence; component MSA must be scoped explicitly')
    for component in components:
        if not isinstance(component, dict):
            raise ValueError('component must be an object')
        scope = 'component:' + str(component.get('id', ''))
        for key in ('msa_format', 'msa_max_sequences', 'msa_remove_insertions'):
            value = checked(key, component.get(key, defaults[key]))
            settings[scope + '.' + key] = {'requested': component.get(key), 'effective': value,
                'origin': 'request' if key in component else 'runner_default', 'scope': scope}
            component[key] = value
        if component.get('msa_path'):
            component['msa_path'] = stage(component['msa_path'], scope)
    for key, default in defaults.items():
        if key == 'complex_components_json':
            value = json.dumps({'components': components, **conditioning}, allow_nan=False) if components or conditioning else ''
        else:
            value = checked(key, normalized.get(key, default))
        scope = 'primary' if key.startswith('msa_') else 'model'
        requested = original.get(key)
        if key in ('msa_path', 'pdb_sequence_path') and value:
            value = stage(value, scope)
        settings[key] = {'requested': requested, 'effective': value,
            'origin': 'request' if key in original else 'workflow_default', 'scope': scope}
        if value is not None:
            if type(value) not in (str, bool, int, float):
                raise ValueError(f'invalid scalar type: {key}')
            argv.extend(['--' + key.replace('_', '-'), str(value).lower() if type(value) is bool else str(value)])
    if not prepared_sources.keys() <= {source['scope'] for source in sources}:
        raise ValueError('missing prepared MSA source')
    # Verify exactly the command we will run, not an independent copy of the request.
    effective = vars(parser.parse_args(argv))
    for key in defaults:
        settings[key]['effective'] = effective[key]
    receipt = {'schema_version': 1, 'core_protein_scientific_contract': 1,
               'model': 'esmfold2', 'settings': settings, 'sources': sources, 'argv': argv}
    json.dumps(receipt, allow_nan=False)
    return argv, receipt


def main(argv: list[str] | None = None, *, model_cache: dict | None = None) -> int:
    args = build_parser().parse_args(argv)
    receipt_path = os.environ.get('BMS_ESMFOLD2_EFFECTIVE_SETTINGS')
    if receipt_path:
        receipt_bytes = Path(receipt_path).read_bytes()
        receipt = json.loads(receipt_bytes)
        if type(receipt.get('core_protein_scientific_contract')) is not int or receipt['core_protein_scientific_contract'] != 1:
            raise ESMFold2InputError('invalid effective settings contract')
        if receipt.get('argv') != list(sys.argv[1:] if argv is None else argv):
            raise ESMFold2InputError('effective settings argv mismatch')
        args._msa_expected_hashes = {source['used_path']: source['sha256'] for source in receipt['sources']}
    model_id_or_path = args.model_id_or_path or default_model_for_variant(args.model_variant)
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.num_loops < 1:
        raise SystemExit("num_loops must be >= 1")
    if args.num_sampling_steps < 1:
        raise SystemExit("num_sampling_steps must be >= 1")
    if args.num_diffusion_samples < 1:
        raise SystemExit("num_diffusion_samples must be >= 1")
    if args.msa_max_sequences is not None and args.msa_max_sequences < 1:
        raise SystemExit("msa_max_sequences must be >= 1")

    if args.local_files_only:
        # Make the offline intent visible to both transformers and huggingface_hub.
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_OFFLINE", "1")

    try:
        import torch
        from esm.models.esmfold2 import (
            DNAInput,
            ESMFold2InputBuilder,
            LigandInput,
            ProteinInput,
            RNAInput,
            StructurePredictionInput,
        )
        from esm.utils.msa import MSA
        from transformers.models.esmfold2.modeling_esmfold2 import ESMFold2Model
    except Exception as exc:  # pragma: no cover - depends on external runtime image
        raise SystemExit(
            "ESMFold2 runtime import failed. Build/install the pinned Biohub esm + Biohub transformers "
            f"runtime before launching this workflow: {exc}"
        ) from exc

    spi, manifest_components = build_structure_prediction_input(
        args,
        ProteinInput=ProteinInput,
        DNAInput=DNAInput,
        RNAInput=RNAInput,
        LigandInput=LigandInput,
        StructurePredictionInput=StructurePredictionInput,
        MSA=MSA,
    )
    primary_protein = next((c for c in manifest_components if c["type"] == "protein"), None)
    sequence_length = int(primary_protein["sequence_length"]) if primary_protein else 0
    total_polymer_residues = sum(
        int(component.get("sequence_length") or 0) * len(list(iter_chain_ids(component['id'])))
        for component in manifest_components
        if component.get("type") in {"protein", "dna", "rna"}
    )

    device = args.device
    if device == "auto":
        device = "cuda" if torch.cuda.is_available() else "cpu"
    device_event: dict[str, Any] = {
        "event": "device_selected",
        "requested_device": args.device,
        "device": device,
        "cuda_available": bool(torch.cuda.is_available()),
        "cuda_device_count": int(torch.cuda.device_count()),
    }
    if device == "cuda":
        device_event["cuda_device_name"] = torch.cuda.get_device_name(torch.cuda.current_device())
    print(json.dumps(device_event, sort_keys=True), flush=True)

    try:
        # Native fold seeds prep and diffusion independently for explicit seeds.
        # Unseeded callers retain fresh-process initialization/RNG semantics.
        cache_key = (model_id_or_path, args.local_files_only, device)
        model = model_cache.get(cache_key) if model_cache is not None and args.seed is not None else None
        if model is None:
            model = ESMFold2Model.from_pretrained(
                model_id_or_path, local_files_only=args.local_files_only,
            ).to(device).eval()
            if model_cache is not None and args.seed is not None:
                model_cache[cache_key] = model
    except Exception as exc:  # pragma: no cover - depends on local HF cache
        raise SystemExit(
            "ESMFold2 model load failed. No download was attempted when local_files_only=true; "
            f"cache or local path is missing/incomplete for {model_id_or_path!r}: {exc}"
        ) from exc

    mmcif_complex_id = sanitize_mmcif_data_block_id(args.sequence_name)
    fold_context = (
        torch.autocast(device_type="cpu", dtype=torch.bfloat16)
        if device == "cpu"
        else contextlib.nullcontext()
    )
    with fold_context:
        result = ESMFold2InputBuilder().fold(
            model,
            spi,
            num_loops=args.num_loops,
            num_sampling_steps=args.num_sampling_steps,
            num_diffusion_samples=args.num_diffusion_samples,
            seed=args.seed,
            complex_id=mmcif_complex_id,
        )

    samples = []
    for index, sample in enumerate(as_results_iter(result)):
        sample_id = f"{args.sequence_name}_{index:03d}"
        cif_name = f"{sample_id}.cif"
        cif_path = output_dir / cif_name
        cif_text = ensure_safe_mmcif_data_block(sample.complex.to_mmcif(), sample_id)
        cif_path.write_text(cif_text, encoding="utf-8")
        metrics = {
            "sample_id": sample_id,
            "sequence_name": args.sequence_name,
            "sequence_length": sequence_length,
            "total_polymer_residues": total_polymer_residues,
            "component_count": len(manifest_components),
            "components": manifest_components,
            "model_variant": args.model_variant,
            "model_id_or_path": model_id_or_path,
            "local_files_only": args.local_files_only,
            "num_loops": args.num_loops,
            "num_sampling_steps": args.num_sampling_steps,
            "num_diffusion_samples": args.num_diffusion_samples,
            "seed": args.seed,
            "device": device,
            "plddt_mean": tensor_mean(getattr(sample, "plddt", None)),
            "ptm": scalar(getattr(sample, "ptm", None)),
            "iptm": scalar(getattr(sample, "iptm", None)),
            "cif": cif_name,
        }
        metrics.update(native_scalar_metrics(sample))
        metrics.update(retain_native_confidence(sample, output_dir, sample_id))
        metrics_name = f"{sample_id}.metrics.json"
        (output_dir / metrics_name).write_text(json.dumps(metrics, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
        samples.append({"sample_id": sample_id, "cif": cif_name, "metrics": metrics_name, **metrics})

    summary_columns = [
        "sample_id",
        "sequence_name",
        "sequence_length",
        "plddt_mean",
        "ptm",
        "iptm",
        "cif",
        "metrics",
    ]
    summary_lines = ["\t".join(summary_columns)]
    for sample in samples:
        summary_lines.append(
            "\t".join(
                "" if sample.get(column) is None else str(sample.get(column, ""))
                for column in summary_columns
            )
        )
    (output_dir / "summary.tsv").write_text("\n".join(summary_lines) + "\n", encoding="utf-8")

    manifest = {
        "schema_version": 2,
        "workflow": "esmfold2",
        "sample_count": len(samples),
        "sequence_name": args.sequence_name,
        "sequence_length": sequence_length,
        "total_polymer_residues": total_polymer_residues,
        "component_count": len(manifest_components),
        "components": manifest_components,
        "model_variant": args.model_variant,
        "model_id_or_path": model_id_or_path,
        "local_files_only": args.local_files_only,
        "samples": samples,
    }
    (output_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    print(
        json.dumps(
            {
                "workflow": "esmfold2",
                "sample_count": len(samples),
                "component_count": len(manifest_components),
                "output_dir": str(output_dir),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except ESMFold2InputError as exc:
        print(f"ESMFold2 input error: {exc}", file=sys.stderr)
        raise SystemExit(2)
