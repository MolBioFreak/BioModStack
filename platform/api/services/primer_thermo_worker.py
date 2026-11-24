"""Isolated ViennaRNA DNA-parameter worker for primer_thermo.

Runs in its own interpreter because loading DNA parameters replaces ViennaRNA's
process-global energy parameters and default geometry; doing that in the API
process would corrupt the RNA secondary-structure tool that shares the library.
Uses ViennaRNA's built-in Mathews 2004 DNA parameter set, which (unlike loading
the .par file) also switches the salt model to DNA geometry.
Reads one JSON request on stdin, writes one JSON result on stdout.
"""
import json
import sys


def _pairs(structure, first_len):
    structure = structure.replace("&", "")
    stack, pairs = [], []
    for index, char in enumerate(structure):
        if char == "(":
            stack.append(index)
        elif char == ")":
            pairs.append((stack.pop(), index))
    cross = [(i, j) for i, j in pairs if i < first_len <= j]
    return structure, pairs, cross


def main() -> None:
    request = json.load(sys.stdin)
    import RNA

    if not RNA.params_load_DNA_Mathews2004():
        raise SystemExit("ViennaRNA could not load its built-in DNA Mathews 2004 parameters")
    md = RNA.md()
    md.temperature = float(request["temperature_c"])
    md.salt = float(request["salt_molar"])
    window = int(request["three_prime_window"])

    def fold(sequence):
        fc = RNA.fold_compound(sequence, md)
        structure, energy = fc.mfe()
        return structure, float(energy)

    def hairpin(sequence):
        structure, energy = fold(sequence)
        _, pairs, _ = _pairs(structure, len(sequence))
        three_prime = any(j >= len(sequence) - window for _, j in pairs)
        return {
            "dg_kcal_mol": round(min(energy, 0.0), 2),
            "structure": structure,
            "three_prime_paired": bool(pairs) and three_prime,
            "paired_bases": 2 * len(pairs),
        }

    def dimer(first, second):
        """Best intermolecular duplex: pairs inside one strand are forbidden."""
        fc = RNA.fold_compound(first + "&" + second, md)
        total = len(first) + len(second)
        forbid = RNA.CONSTRAINT_CONTEXT_NONE | RNA.CONSTRAINT_CONTEXT_NO_REMOVE
        for low, high in ((1, len(first)), (len(first) + 1, total)):
            for i in range(low, high + 1):
                for j in range(i + 1, high + 1):
                    fc.hc_add_bp(i, j, forbid)
        structure, energy = fc.mfe_dimer()
        structure, _, cross = _pairs(structure, len(first))
        if not cross:
            return {
                "dg_kcal_mol": 0.0,
                "structure": structure,
                "three_prime_paired": False,
                "intermolecular_pairs": 0,
            }
        three_prime = any(
            i >= len(first) - window or j >= total - window for i, j in cross
        )
        return {
            "dg_kcal_mol": round(min(float(energy), 0.0), 2),
            "structure": structure,
            "three_prime_paired": three_prime,
            "intermolecular_pairs": len(cross),
        }

    out = {"hairpins": {}, "self_dimers": {}, "pairs": [], "version": RNA.__version__}
    for key, sequence in request["sequences"].items():
        out["hairpins"][key] = hairpin(sequence)
        out["self_dimers"][key] = dimer(sequence, sequence)
    for left, right in request["pairs"]:
        sequences = request["sequences"]
        result = dimer(sequences[left], sequences[right])
        result.update({"left": left, "right": right})
        out["pairs"].append(result)
    json.dump(out, sys.stdout)


if __name__ == "__main__":
    main()
