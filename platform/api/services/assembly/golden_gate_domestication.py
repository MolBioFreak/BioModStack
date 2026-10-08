"""Explicit, substitution-only domestication proposals; never mutates source material.

Coordinates are zero-based, half-open, on the source's forward strand. Wrapped
regions require circular topology. CDS frame is the number of bases skipped at
its biological 5' end (after strand orientation); incomplete boundary codons are
preserved exactly. The deterministic solver enumerates increasing Hamming shells
and uses DNA Chisel constraints, not codon optimization or a stochastic fallback.
"""
from __future__ import annotations

from itertools import combinations, product
from types import SimpleNamespace
from typing import Literal

from Bio.Data import CodonTable
from pydantic import BaseModel, ConfigDict, Field, model_validator

from services.restriction_analysis import _scan, reverse_complement


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class EditRegion(_Model):
    start: int = Field(ge=0, description="Forward source start boundary, bp (zero-based).")
    end: int = Field(ge=0, description="Forward source end boundary, bp (exclusive).")
    wraps_origin: bool = False

    def positions(self, length: int, circular: bool) -> tuple[int, ...]:
        if self.start >= length or self.end > length:
            raise ValueError("Region lies outside source sequence")
        if self.wraps_origin:
            if not circular or self.end > self.start:
                raise ValueError("Wrapped region requires circular topology and end <= start")
            return tuple(range(self.start, length)) + tuple(range(self.end))
        if self.end <= self.start:
            raise ValueError("Nonwrapped region must have end > start")
        return tuple(range(self.start, self.end))


class CDSConstraint(_Model):
    feature_id: str = Field(min_length=1)
    region: EditRegion
    strand: Literal[1, -1] = 1
    frame: Literal[0, 1, 2] = Field(default=0, description="Bases skipped at biological 5' end; v1 preserves incomplete 5'/3' boundary codons exactly and translates complete codons only.")
    genetic_code: int = Field(default=1, description="NCBI genetic code table ID.")
    initiation: Literal["ordinary", "preserve", "allowed"] = Field(
        default="preserve", description="ordinary: partial CDS/no initiation; preserve: exact start; allowed: selected start codons, translated as Met.")
    allowed_start_codons: tuple[str, ...] = ()
    stop_policy: Literal["preserve", "synonymous"] = Field(
        default="preserve", description="Preserve exact stop codons or permit synonymous stops; stop positions always remain unchanged.")

    @model_validator(mode="after")
    def check_policy(self):
        if self.genetic_code not in CodonTable.unambiguous_dna_by_id:
            raise ValueError("Unknown NCBI genetic code")
        table = CodonTable.unambiguous_dna_by_id[self.genetic_code]
        if self.initiation == "allowed":
            if not self.allowed_start_codons or any(c not in table.start_codons for c in self.allowed_start_codons):
                raise ValueError("Select valid initiation codons for the genetic code")
        elif self.allowed_start_codons:
            raise ValueError("allowed_start_codons only applies to allowed initiation")
        if self.frame and self.initiation != "ordinary":
            raise ValueError("A partial 5' codon has no initiation codon; select ordinary")
        return self


class UnwantedSite(_Model):
    enzyme_id: str = Field(min_length=1, description="Identity resolved by the existing catalog owner.")
    recognition_sequence: str = Field(pattern=r"^[ACGTRYSWKMBDHVN]+$", description="Catalog-resolved IUPAC recognition motif, not enzyme cleavage geometry.")
    starts: tuple[int, ...] | None = Field(default=None, description="Selected forward-strand motif start indices, on either strand; null removes every occurrence. New occurrences elsewhere are never introduced.")


class DomesticationSettings(_Model):
    schema_version: Literal["golden-gate-domestication/v1"] = "golden-gate-domestication/v1"
    enabled: bool = False
    editable_regions: tuple[EditRegion, ...] = ()
    protected_regions: tuple[EditRegion, ...] = ()
    cds: tuple[CDSConstraint, ...] = ()
    unwanted_sites: tuple[UnwantedSite, ...] = ()
    algorithm: Literal["minimal_substitutions"] = "minimal_substitutions"
    candidate_budget: int = Field(default=100000, ge=1, description="Maximum candidate evaluations; deterministic, no random seed or restart state.")
    max_edits: int | None = Field(default=None, ge=0, description="Optional operator-selected substitution limit; null permits every editable base.")


class BaseEdit(_Model):
    position: int
    original: str
    proposed: str
    affected_feature_ids: tuple[str, ...]


class TranslationEvidence(_Model):
    feature_id: str
    original: str
    proposed: str | None
    original_start_codon: str | None
    proposed_start_codon: str | None


class DomesticationResult(_Model):
    settings: DomesticationSettings
    status: Literal["disabled", "unchanged", "proposal", "no_proposal_found"]
    proposed_sequence: str | None = None
    edits: tuple[BaseEdit, ...] = ()
    translations: tuple[TranslationEvidence, ...] = ()
    candidates_evaluated: int = 0
    search_complete: bool = True
    minimal_edits_proven: bool = False
    diagnostics: tuple[str, ...] = ()
    engine: str | None = None


def _concrete(sequence: str) -> None:
    if not sequence or any(b not in "ACGT" for b in sequence):
        raise ValueError("Domestication requires explicit uppercase A/C/G/T DNA; source is not normalized or changed")


def propose_domestication(
    sequence: str, *, settings: DomesticationSettings | None = None,
    circular: bool = False,
) -> DomesticationResult:
    """Return one minimum-edit proposal, or an honest bounded-search outcome.

    No catalog is replaced: callers supply motifs resolved by the catalog owner.
    Site matching delegates to the existing restriction-analysis scanner. Selected
    sites can disappear, but remaining original sites cannot be moved/recreated.
    No sequence is accepted or persisted here. Use explicit acceptance separately.
    """
    settings = settings or DomesticationSettings()
    if not settings.enabled:
        return DomesticationResult(settings=settings, status="disabled")
    _concrete(sequence)
    # DNA Chisel is intentionally lazy: unedited planning needs no solver import.
    from dnachisel import AvoidChanges, DnaOptimizationProblem, EnforceTranslation, Specification, SpecEvaluation

    length = len(sequence)
    editable = {p for r in settings.editable_regions for p in r.positions(length, circular)}
    protected = {p for r in settings.protected_regions for p in r.positions(length, circular)}
    editable -= protected
    topology = "circular" if circular else "linear"
    if len({c.feature_id for c in settings.cds}) != len(settings.cds):
        raise ValueError("CDS feature IDs must be unique")

    class Sites(Specification):
        def __init__(self, site):
            self.site = site
            original = self.hits(sequence)
            if site.starts is not None:
                if any(p < 0 or p >= length for p in site.starts):
                    raise ValueError("Selected site starts lie outside source")
                if not set(site.starts) <= {p for p, _ in original}:
                    raise ValueError("Selected site start does not match the catalog motif")
            self.allowed = set() if site.starts is None else {hit for hit in original if hit[0] not in site.starts}

        def hits(self, dna):
            motif = self.site.recognition_sequence
            return {(p, strand) for strand, pattern in ((1, motif), (-1, reverse_complement(motif)))
                    for p, _, _ in _scan(dna, pattern, topology)}

        def evaluate(self, problem):
            breaches = self.hits(problem.sequence) - self.allowed
            return SpecEvaluation(self, problem, score=-len(breaches), locations=[])

    class Coding(Specification):
        def __init__(self, cds):
            self.cds = cds
            self.positions = cds.region.positions(length, circular)
            self.original = self.extract(sequence)
            self.end = cds.frame + max(0, (len(self.original) - cds.frame) // 3) * 3
            coding = self.original[cds.frame:self.end]
            if not coding:
                if cds.initiation != "ordinary":
                    raise ValueError("A CDS without a complete codon has no initiation codon; select ordinary")
                self.native = None
                self.codon_choices = []
                self.stop_indices = []
                return
            table = CodonTable.unambiguous_dna_by_id[cds.genetic_code]
            if cds.initiation != "ordinary" and coding[:3] not in table.start_codons:
                raise ValueError("Source first codon is not an initiation codon in the selected code")
            policy = None if cds.initiation == "ordinary" else ("keep" if cds.initiation == "preserve" else list(cds.allowed_start_codons))
            self.native = EnforceTranslation(genetic_table=table.names[0], start_codon=policy).initialized_on_problem(SimpleNamespace(sequence=coding), "constraint")
            # Native evaluate assumes any first codon is Met when initiation is
            # selected; its nucleotide restrictions, not AA equality, enforce
            # actual alternative-start semantics. Check both explicitly.
            self.codon_choices = self.native.restrict_nucleotides(coding)
            self.stop_indices = [i for i, aa in enumerate(self.native.translation) if aa == "*"]

        def extract(self, dna):
            letters = "".join(dna[p] for p in self.positions)
            return letters if self.cds.strand == 1 else reverse_complement(letters)

        def translated(self, dna):
            from Bio.Seq import Seq
            coding = self.extract(dna)[self.cds.frame:self.end]
            value = str(Seq(coding).translate(table=self.cds.genetic_code))
            return value if self.cds.initiation == "ordinary" else "M" + value[1:]

        def evaluate(self, problem):
            oriented = self.extract(problem.sequence)
            if self.native is None:
                return SpecEvaluation(self, problem, score=0 if oriented == self.original else -1, locations=[])
            coding = oriented[self.cds.frame:self.end]
            original = self.original[self.cds.frame:self.end]
            ok = oriented[:self.cds.frame] == self.original[:self.cds.frame] and oriented[self.end:] == self.original[self.end:]
            ok = ok and self.native.evaluate(SimpleNamespace(sequence=coding)).passes
            ok = ok and all(coding[a:b] in choices for (a, b), choices in self.codon_choices)
            if self.cds.stop_policy == "preserve":
                ok = ok and all(coding[3*i:3*i+3] == original[3*i:3*i+3] for i in self.stop_indices)
            return SpecEvaluation(self, problem, score=0 if ok else -1, locations=[])

    coding_specs = [Coding(c) for c in settings.cds]
    locked = sorted(set(range(length)) - editable)
    constraints = [Sites(s) for s in settings.unwanted_sites] + coding_specs
    if locked:
        constraints.append(AvoidChanges(indices=locked))
    problem = DnaOptimizationProblem(sequence=sequence, constraints=constraints, logger=None)
    evaluated = 0

    def result(candidate, status, complete, diagnostics=()):
        edits = tuple(BaseEdit(position=i, original=a, proposed=b, affected_feature_ids=tuple(c.cds.feature_id for c in coding_specs if i in c.positions)) for i, (a, b) in enumerate(zip(sequence, candidate or sequence)) if a != b)
        translations = tuple(TranslationEvidence(
            feature_id=c.cds.feature_id, original=c.translated(sequence),
            proposed=c.translated(candidate) if candidate else None,
            original_start_codon=c.original[c.cds.frame:c.cds.frame+3] if c.cds.initiation != "ordinary" else None,
            proposed_start_codon=c.extract(candidate)[c.cds.frame:c.cds.frame+3] if candidate and c.cds.initiation != "ordinary" else None,
        ) for c in coding_specs)
        return DomesticationResult(settings=settings, status=status, proposed_sequence=candidate,
            edits=edits, translations=translations, candidates_evaluated=evaluated,
            search_complete=complete, minimal_edits_proven=candidate is not None,
            diagnostics=diagnostics, engine="DNA Chisel 3.2.16; minimal_substitutions/v1")

    # Explicitly evaluate nucleotide-restricted specs: native autopass=True is
    # unsafe when testing candidate strings outside its random mutation solver.
    if problem.all_constraints_pass(autopass=False):
        return result(sequence, "unchanged", True)
    positions = sorted(editable)
    max_edits = len(positions) if settings.max_edits is None else min(settings.max_edits, len(positions))
    for count in range(1, max_edits + 1):
        for indices in combinations(positions, count):
            for bases in product(*[tuple(b for b in "ACGT" if b != sequence[i]) for i in indices]):
                if evaluated >= settings.candidate_budget:
                    return result(None, "no_proposal_found", False, ("Candidate budget exhausted; no infeasibility claim. Unedited planning remains available.",))
                dna = list(sequence)
                for i, base in zip(indices, bases):
                    dna[i] = base
                problem.sequence = "".join(dna)
                evaluated += 1
                if problem.all_constraints_pass(autopass=False):
                    return result(problem.sequence, "proposal", False, ("Minimum substitution count proven; equivalent proposals not exhaustively enumerated.",))
    return result(None, "no_proposal_found", True, ("No proposal within the explicit editable regions, CDS constraints and edit limit; source unchanged.",))


def apply_domestication_proposal(
    sequence: str, proposal: DomesticationResult, *, accepted: bool = False,
) -> str:
    """Materialize only an explicitly accepted proposal; no persistence side effects.

    This is a local helper, not an untrusted HTTP payload authenticator. The
    parent save owner must recompute/retain authoritative proposals as usual.
    """
    if not accepted:
        return sequence
    if proposal.status not in {"proposal", "unchanged"} or proposal.proposed_sequence is None:
        raise ValueError("No domestication proposal to accept")
    proposed = proposal.proposed_sequence
    if len(sequence) != len(proposed):
        raise ValueError("Proposal does not match source length")
    reconstructed = list(proposed)
    for edit in proposal.edits:
        if not 0 <= edit.position < len(sequence) or proposed[edit.position] != edit.proposed:
            raise ValueError("Malformed proposal edits")
        reconstructed[edit.position] = edit.original
    if "".join(reconstructed) != sequence:
        raise ValueError("Proposal is for a different source")
    return proposed


def domestication_settings_schema() -> dict:
    """Closed native schema for the parent's shared API/UI/persistence inventory."""
    return DomesticationSettings.model_json_schema()
