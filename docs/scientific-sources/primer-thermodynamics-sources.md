# Primer thermodynamics: sources and attribution

Endpoint `POST /api/molbio/primer-thermo` and the "Hairpin and dimer ΔG" section
of the Primer panel's QC tab report hairpin, self-dimer and forward/reverse
pair-dimer free energies (kcal/mol). Results are informational. Nothing blocks,
rejects or changes a primer.

## Flags

| ΔG (kcal/mol) | Flag |
| --- | --- |
| above −5 (including positive) | none |
| −5 or lower | yellow |
| −10 or lower | orange |

Positive ΔG means no structure forms and is reported as "none" (0). A separate
note marks structures where one of the last 4 bases at a 3' end is paired.

## What is calculated

- **Hairpin**: lowest-energy fold of one primer.
- **Self-dimer and pair dimer**: lowest-energy intermolecular duplex of two
  strands. Pairs inside either strand are forbidden, so a primer's own hairpin
  is never counted as a dimer.
- A forward/reverse pair is used as a pair; primers are not pooled.

## Engine

**ViennaRNA** (RNAfold / RNAcofold) with its built-in Mathews 2004 DNA
parameter set (`RNA.params_load_DNA_Mathews2004()`). It runs in a separate
interpreter because loading DNA parameters replaces ViennaRNA's process-global
parameters and would otherwise alter the RNA structure tool that shares the
library. The built-in loader also switches ViennaRNA's salt model to DNA
geometry; loading the `dna_mathews2004.par` file alone does not, so that file
is not shipped.

Licence text, copied from the ViennaRNA repository (`license.txt`):

> Permission is granted for research, educational, and commercial use
> and modification so long as 1) the package and any derived works are not
> redistributed for any fee, other than media costs, 2) proper credit is
> given to the authors and the Institute for Theoretical Chemistry of the
> University of Vienna.
>
> If you want to include this software in a commercial product, please contact
> the authors.

BioModStack is free and open source and is not redistributed for a fee. Credit
is given here, in the Primer panel ("Method, credit and limits") and in every
API response (`credit`, `citations`). ViennaRNA's README lists the Mathews 1999
and 2004 DNA sets as shipped with the package; it does not name a paper for
them, so none is cited as their source.

## Salt

Monovalent-equivalent salt = Na⁺ + K⁺ + Tris/2 + 120·√(max(Mg²⁺ − dNTP, 0)) mM
(the form used by von Ahsen 2001), computed from the primer Tm settings and
passed to ViennaRNA's monovalent salt correction (Yao 2023). ViennaRNA documents
10 mM to 1.021 M; values outside are clamped and the response says so in
`salt_note`.

Checked limits of that correction for DNA (60 random perfect duplexes against a
SantaLucia 2004 calculation, 10–20 bp, 37 °C):

- At 1.021 M the duplex ΔG is on average 1.0 kcal/mol more stable than
  SantaLucia 2004 (spread 0.1).
- Going from 1.021 M to 164 mM, ViennaRNA weakens the duplex by about half as
  much as the SantaLucia 1998 salt correction (1.5 against 2.8 kcal/mol); at
  50 mM about three quarters as much.

So at PCR-like salt, dimer ΔG from this engine reads more negative than
SantaLucia-based tools. Treat the yellow flag at −5 as a prompt to look, not a
verdict.

## Agreement with IDT (hairpins only)

34 Bionova primers with an IDT hairpin ΔG (IDT reports 27 of them folding):

| Salt | correlation | mean abs. difference | primers folding |
| --- | --- | --- | --- |
| 1.021 M | 0.77 | 0.36 kcal/mol | 17 |
| 164 mM | 0.67 | 0.57 kcal/mol | 4 |
| 50 mM | 0.55 | 0.63 kcal/mol | 3 |

The IDT buffer settings are not known, and there is no IDT dimer data, so
dimers are not benchmarked. At the Primer panel's default Tm settings (164 mM
equivalent) the engine finds fewer hairpins than IDT does.

## References (each checked against Crossref)

- Lorenz R. et al. ViennaRNA Package 2.0. Algorithms Mol Biol 2011;6:26.
  doi:10.1186/1748-7188-6-26
- Bernhart S.H. et al. Partition function and base pairing probabilities of RNA
  heterodimers. Algorithms Mol Biol 2006;1:3. doi:10.1186/1748-7188-1-3
- Yao H.-T. et al. Mono-valent salt corrections for RNA secondary structures in
  the ViennaRNA package. Algorithms Mol Biol 2023;18:8.
  doi:10.1186/s13015-023-00236-0
- von Ahsen N. et al. Oligonucleotide melting temperatures under PCR
  conditions: nearest-neighbor corrections for Mg2+, deoxynucleotide
  triphosphate, and dimethyl sulfoxide concentrations. Clin Chem
  2001;47:1956-1961. doi:10.1093/clinchem/47.11.1956
- SantaLucia J. A unified view of polymer, dumbbell, and oligonucleotide DNA
  nearest-neighbor thermodynamics. PNAS 1998;95:1460-1465.
  doi:10.1073/pnas.95.4.1460 (comparison calculations)
- SantaLucia J., Hicks D. The thermodynamics of DNA structural motifs. Annu Rev
  Biophys Biomol Struct 2004;33:415-440.
  doi:10.1146/annurev.biophys.32.110601.141800 (primer Tm and comparison
  calculations)
- Owczarzy R. et al. Predicting stability of DNA duplexes in solutions
  containing magnesium and monovalent cations. Biochemistry 2008;47:5336-5353.
  doi:10.1021/bi702363u (primer Tm)
- Cock P.J.A. et al. Biopython. Bioinformatics 2009;25:1422-1423.
  doi:10.1093/bioinformatics/btp163 (primer Tm)

## Known limits

- Predictions from one nearest-neighbour model; IDT OligoAnalyzer and other
  tools use different parameters and will differ.
- Single lowest-energy structure, not an ensemble.
- Salt behaviour for DNA is approximate (see above).
