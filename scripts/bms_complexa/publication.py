"""Observe native documents/CSVs without inventing Designs, scores or ancestry."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
from scripts.lib.complexa_contract import MODEL_ID, RESULT_CONTRACT, SOURCE_REVISION

AA = dict(zip("ALA ARG ASN ASP CYS GLN GLU GLY HIS ILE LEU LYS MET PHE PRO SER THR TRP TYR VAL".split(), "ARNDCQEGHILKMFPSTWYV"))


def structure_identity(path: Path) -> dict:
    atoms, residues = [], {}
    model = "1"
    for line in path.read_text(errors="replace").splitlines():
        if line.startswith("MODEL "):
            model = line[10:14].strip()
        if line[:6].strip() not in ("ATOM", "HETATM"):
            continue
        identity = {"model": model, "chain": line[21:22], "residue": line[22:26].strip(),
                    "insertion_code": line[26:27].strip(), "component": line[17:20].strip(),
                    "atom": line[12:16].strip(), "altloc": line[16:17].strip(),
                    "element": line[76:78].strip()}
        atoms.append(identity)
        if line.startswith("ATOM"):
            key = (model, identity["chain"], identity["residue"], identity["insertion_code"])
            residues.setdefault(key, identity["component"])
    sequences = {}
    for (model, chain, residue, insertion), component in residues.items():
        sequences.setdefault(f"{model}:{chain}", []).append({"residue": residue, "insertion_code": insertion, "component": component})
    return {"atoms": atoms, "chains": [{"identity": key, "sequence": "".join(AA.get(r["component"], "X") for r in rows),
                                          "residues": rows} for key, rows in sequences.items()]}


def publish(root: Path, receipt: dict) -> dict:
    artifacts, observations, documents = [], [], []
    native = root / "native"
    for path in sorted(native.rglob("*")):
        if not path.is_file() or path.is_symlink():
            continue
        relative = path.relative_to(root).as_posix()
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        artifacts.append({"path": relative, "sha256": digest, "size": path.stat().st_size})
        if path.suffix.lower() == ".csv":
            with path.open(newline="") as stream:
                for index, row in enumerate(csv.DictReader(stream)):
                    observations.append({"artifact": relative, "row": index, "values": row,
                        "missing_fields": [key for key, value in row.items() if value is None or value.strip().lower() in ("", "nan", "none")]})
        if path.suffix.lower() == ".pdb":
            role = "source" if relative.startswith("native/source/") else (
                "retained_generation" if relative.startswith("native/retained_generation/") else
                "generation" if relative.startswith("native/inference/") else "native_output")
            documents.append({"document_id": relative, "kind": "native_document", "role": role, "path": relative,
                              "sha256": digest, **structure_identity(path), "native_observations": []})
    by_path = {str((root / d["path"]).resolve()): d for d in documents}
    for observation in observations:
        value = observation["values"].get("pdb_path")
        if not value:
            continue
        p = Path(value)
        absolute = str((p if p.is_absolute() else native / p).resolve())
        if observation["artifact"].startswith("native/retained_generation/"):
            origin = native / "inference"
            if Path(absolute).is_relative_to(origin):
                absolute = str(native / "retained_generation" / Path(absolute).relative_to(origin))
        if absolute in by_path:
            document = by_path[absolute]
            document["native_observations"].append({"artifact": observation["artifact"], "row": observation["row"]})
            for key in ("metadata_tag", "sample_type", "aatype"):
                if key in observation["values"]:
                    document.setdefault("native_identity", {})[key] = observation["values"][key]
    result = {"contract": RESULT_CONTRACT, "model_id": MODEL_ID, "source_revision": SOURCE_REVISION,
              "receipt": receipt, "documents": documents, "observations": observations, "artifacts": artifacts,
              "missingness": {"documents": not bool(documents), "native_csvs": not bool(observations)}}
    (root / "complexa-results.json").write_text(json.dumps(result, indent=2))
    return result
