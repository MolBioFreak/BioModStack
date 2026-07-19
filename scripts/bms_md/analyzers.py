from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Protocol, cast


MAX_SPECIALIZED_POINTS = 2_000


@dataclass(frozen=True)
class AnalyzerContext:
    replica: int
    chemistry_profile_id: str | None
    system_class: str | None
    drt4_parameterization_approved: bool


class PreparedAnalyzer(Protocol):
    analyzer_id: str

    def sample(self, source_frame: int, time_ps: float) -> dict[str, Any]: ...

    def result(self) -> dict[str, Any]: ...


class PairDistanceAnalyzer:
    def __init__(
        self,
        *,
        analyzer_id: str,
        group_a: Any,
        group_b: Any,
        cutoff_angstrom: float,
        definition: str,
        selection_a: str,
        selection_b: str,
    ) -> None:
        self.analyzer_id = analyzer_id
        self.group_a = group_a
        self.group_b = group_b
        self.cutoff_angstrom = cutoff_angstrom
        self.definition = definition
        self.selection_a = selection_a
        self.selection_b = selection_b
        self._points: list[dict[str, Any]] = []

    def sample(self, source_frame: int, time_ps: float) -> dict[str, Any]:
        from MDAnalysis.lib.distances import distance_array  # pyright: ignore[reportMissingImports]

        distances = distance_array(
            self.group_a.positions,
            self.group_b.positions,
            box=self.group_a.universe.dimensions,
        )
        minimum = float(distances.min())
        contacts = int((distances <= self.cutoff_angstrom).sum())
        point = {
            "source_frame": int(source_frame),
            "time_ps": float(time_ps),
            "minimum_distance_angstrom": minimum,
            "contact_count": contacts,
        }
        if len(self._points) < MAX_SPECIALIZED_POINTS:
            self._points.append(point)
        return point

    def result(self) -> dict[str, Any]:
        return {
            "analyzer_id": self.analyzer_id,
            "status": "completed",
            "definition": self.definition,
            "cutoff_angstrom": self.cutoff_angstrom,
            "selection_a": self.selection_a,
            "selection_b": self.selection_b,
            "points": self._points,
        }


def _selection(universe: Any, expression: str) -> Any:
    return universe.select_atoms(expression)


def prepare_specialized_analyzers(universe: Any, manifest: Mapping[str, Any]) -> tuple[list[PreparedAnalyzer], list[dict[str, Any]]]:
    raw_config = manifest.get("config")
    config = cast(Mapping[str, Any], raw_config) if isinstance(raw_config, Mapping) else {}
    raw_chemistry = config.get("chemistry")
    chemistry = cast(Mapping[str, Any], raw_chemistry) if isinstance(raw_chemistry, Mapping) else {}
    context = AnalyzerContext(
        replica=int(manifest.get("replica_index", 0)),
        chemistry_profile_id=str(chemistry.get("profile_id")) if chemistry.get("profile_id") else None,
        system_class=str(chemistry.get("system_class")) if chemistry.get("system_class") else None,
        drt4_parameterization_approved=chemistry.get("drt4_parameterization_approved") is True,
    )
    prepared: list[PreparedAnalyzer] = []
    states: list[dict[str, Any]] = []

    protein = _selection(universe, "protein and not name H*")
    protein_segments = [segment.atoms.select_atoms("protein and not name H*") for segment in protein.segments if segment.atoms.select_atoms("protein and not name H*").n_atoms]
    if len(protein_segments) >= 2:
        remaining_indices = [int(index) for segment in protein_segments[1:] for index in segment.indices]
        prepared.append(PairDistanceAnalyzer(
            analyzer_id="protein_interface_contacts_v1",
            group_a=protein_segments[0],
            group_b=universe.atoms[remaining_indices],
            cutoff_angstrom=4.5,
            definition="heavy-atom inter-segment contacts; first protein segment versus remaining protein segments",
            selection_a="protein segment 0",
            selection_b="protein segments 1..n",
        ))
    else:
        states.append({"analyzer_id": "protein_interface_contacts_v1", "status": "not_applicable", "reason": "fewer than two protein segments"})

    nucleic = _selection(universe, "nucleic and not name H*")
    if protein.n_atoms and nucleic.n_atoms:
        prepared.append(PairDistanceAnalyzer(
            analyzer_id="protein_nucleic_contacts_v1",
            group_a=protein,
            group_b=nucleic,
            cutoff_angstrom=4.5,
            definition="protein-nucleic heavy-atom contacts; geometry packages such as Curves+/3DNA are not implied",
            selection_a="protein",
            selection_b="nucleic",
        ))
    else:
        states.append({"analyzer_id": "protein_nucleic_contacts_v1", "status": "not_applicable", "reason": "protein and nucleic selections are both required"})

    ligand = _selection(universe, "not name H* and not protein and not nucleic and not resname SOL WAT HOH TIP3 NA CL K MG MN ZN CA")
    if protein.n_atoms and ligand.n_atoms:
        prepared.append(PairDistanceAnalyzer(
            analyzer_id="ligand_nucleotide_contacts_v1",
            group_a=protein,
            group_b=ligand,
            cutoff_angstrom=4.5,
            definition="protein to non-polymer, non-solvent, non-common-ion heavy-atom contacts",
            selection_a="protein",
            selection_b="not protein and not nucleic and not common solvent/ion",
        ))
    else:
        states.append({"analyzer_id": "ligand_nucleotide_contacts_v1", "status": "not_applicable", "reason": "no admitted non-polymer ligand selection"})

    metals = _selection(universe, "resname MG MN ZN FE CO NI CU or name MG MN ZN FE CO NI CU")
    coordinating = _selection(universe, "protein and (name O* or name N* or name S*)")
    if metals.n_atoms and coordinating.n_atoms:
        prepared.append(PairDistanceAnalyzer(
            analyzer_id="metal_coordination_v1",
            group_a=metals,
            group_b=coordinating,
            cutoff_angstrom=3.0,
            definition="common transition/divalent metal to protein O/N/S coordination candidates",
            selection_a="common transition/divalent metals",
            selection_b="protein O/N/S atoms",
        ))
    else:
        states.append({"analyzer_id": "metal_coordination_v1", "status": "not_applicable", "reason": "no admitted metal and coordinating-atom selections"})

    states.append({
        "analyzer_id": "drt4_observables_v1",
        "status": "blocked" if not context.drt4_parameterization_approved else "available_not_requested",
        "reason": "DRT4 chemistry and covalent/metal parameterization are not approved" if not context.drt4_parameterization_approved else "explicit DRT4 observable definition is required",
        "chemistry_profile_id": context.chemistry_profile_id,
    })
    return prepared, states
