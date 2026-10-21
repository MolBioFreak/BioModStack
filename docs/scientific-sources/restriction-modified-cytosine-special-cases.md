# Modified-cytosine-dependent restriction enzymes: UI source ledger

## Scope and authority

The Special cases presentation covers enzymes **requiring modified cytosines**, not
ordinary cutters inhibited by methylation, and not methyladenine-dependent cutters.
This is a UI exception list, not a new recognition engine, substrate predictor,
admission gate, or catalog correction. Explicit mapping remains available; native
capability alone still determines digest availability. API records, coordinates,
cut counts, computed products, and catalog hashes are unchanged.

Audited catalog: `biopython-rebase-404-bms-v1`, **1,092 exact IDs**, content SHA-256
`e9a1e9ec8e5b1845f82fd613f7343722756c0ef8c5f487c704a151646317d73f`.
The source asset is `platform/api/config/molbio/restriction/restriction_enzyme_catalog_v1.json`.

The audit queried the REBASE enzyme page for **every catalog ID**, not just short
motifs, commercial products, or the previously reported offenders. Each retrieved
page's explicit Type II Methyl-directed / Type IV classification was compared
with its modified-base notation. It also intersected the experimentally studied
family lists below with exact catalog IDs. No prefix or motif heuristics are used.

Reconciliation:

- 1,087 source pages retrieved with enzyme type; 5 exact-name pages unresolved.
- 29 retrieved records explicitly methyl-directed: **27 cytosine-dependent** and
  **2 adenine-dependent** (`DpnI`, `MalI`; both show `G6mA^TC`, both stay ordinary).
- 1,058 other retrieved records were not classified as modification-dependent.
- 27 Special cases + 1,065 unchanged ordinary entries = 1,092.
- Of the 27, native capabilities remain **22 digest_simulation + 5 recognition_only**.
  Classification does not upgrade the five records with unavailable geometry.

REBASE is the source database and a primary repository of experimental submissions.
Some records have only **unpublished observations submitted to REBASE**, not an
accessible primary journal paper. Those are identified honestly below; their
explicit methyl-directed classification plus 5mC/5hmC notation is source evidence,
not an inference from the sequence. Patent and journal citations obtained from
REBASE reference lists are distinguished from papers whose full text was read.

## Exact-ID classification ledger (27)

Each REBASE link is specific to the exact catalog ID and explicitly marks the
record methyl-directed with modified-cytosine notation. Additional evidence is
listed separately. 5mC = 5-methylcytosine; 5hmC = 5-hydroxymethylcytosine.

| Exact catalog ID | Required modification / qualification | Primary source record | Additional primary evidence |
| --- | --- | --- | --- |
| `AbaSI` | Modified cytosine; strong preference for 5hmC-related substrates; see glucosylation caveats below | [REBASE](https://rebase.neb.com/rebase/enz/AbaSI.html) | [Borgaro & Zhu 2013](https://pmc.ncbi.nlm.nih.gov/articles/PMC3627594/) |
| `AbaUMB2I` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/AbaUMB2I.html) | REBASE primary record (direct experimental submission; not a claim of a peer-reviewed paper) |
| `AoxI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/AoxI.html) | [Primary patent RU2399663C1](https://patents.google.com/patent/RU2399663C1/en), identified by REBASE reference 12089 |
| `AspBHI` | 5mC or 5hmC; glucosylated 5hmC is not an equivalent substrate | [REBASE](https://rebase.neb.com/rebase/enz/AspBHI.html) | [Cohen-Karni et al. 2011](https://doi.org/10.1073/pnas.1018448108) |
| `BisI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/BisI.html) | [Xu et al. 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC4926085/) |
| `BlsI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/BlsI.html) | [Tarasova et al. 2008](https://pmc.ncbi.nlm.nih.gov/articles/PMC2257971/) |
| `BmeDI` | Modified cytosine; strong preference for 5hmC-related substrates; see glucosylation caveats below | [REBASE](https://rebase.neb.com/rebase/enz/BmeDI.html) | [Borgaro & Zhu 2013](https://pmc.ncbi.nlm.nih.gov/articles/PMC3627594/); [base-flipping experiments 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4259335/) |
| `Dde51507I` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/Dde51507I.html) | REBASE primary record (direct experimental submission; not a claim of a peer-reviewed paper) |
| `EcoBLMcrX` | 5mC or 5hmC; density and context matter | [REBASE](https://rebase.neb.com/rebase/enz/EcoBLMcrX.html) | [EcoBLMcrX characterization 2017](https://pmc.ncbi.nlm.nih.gov/articles/PMC5487053/) |
| `EcoO157SI` | 5hmC and also 5mC (explicit REBASE enzyme comment) | [REBASE](https://rebase.neb.com/rebase/enz/EcoO157SI.html) | REBASE primary record (direct experimental submission; not a claim of a peer-reviewed paper) |
| `FspEI` | 5mC or 5hmC; glucosylated 5hmC is not an equivalent substrate | [REBASE](https://rebase.neb.com/rebase/enz/FspEI.html) | [Cohen-Karni et al. 2011](https://doi.org/10.1073/pnas.1018448108) |
| `GlaI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/GlaI.html) | [Tarasova et al. 2008](https://pmc.ncbi.nlm.nih.gov/articles/PMC2257971/) |
| `GluI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/GluI.html) | [Xu et al. 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC4926085/) |
| `KroI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/KroI.html) | REBASE primary record (direct experimental submission; not a claim of a peer-reviewed paper) |
| `LpnPI` | 5mC or 5hmC; glucosylated 5hmC is not an equivalent substrate | [REBASE](https://rebase.neb.com/rebase/enz/LpnPI.html) | [Cohen-Karni et al. 2011](https://doi.org/10.1073/pnas.1018448108); [base-flipping experiments 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4259335/) |
| `MspJI` | 5mC or 5hmC; glucosylated 5hmC is not an equivalent substrate | [REBASE](https://rebase.neb.com/rebase/enz/MspJI.html) | [Cohen-Karni et al. 2011](https://doi.org/10.1073/pnas.1018448108) |
| `MteI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/MteI.html) | [Primary reference 21538](https://rebase.neb.com/cgi-bin/erefget?140117): Chernukhin et al., Ovchinnikov Bull. 8:16–26 (2012) |
| `NhoI` | 5mC or 5hmC; density and context matter | [REBASE](https://rebase.neb.com/rebase/enz/NhoI.html) | [EcoBLMcrX characterization 2017](https://pmc.ncbi.nlm.nih.gov/articles/PMC5487053/) |
| `PcsI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/PcsI.html) | [Primary patent RU2377294C1](https://patents.google.com/patent/RU2377294C1/en), identified by REBASE reference 11488 |
| `Pfl8569I` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/Pfl8569I.html) | REBASE primary record (direct experimental submission; not a claim of a peer-reviewed paper) |
| `PkrI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/PkrI.html) | [Xu et al. 2016](https://pmc.ncbi.nlm.nih.gov/articles/PMC4926085/) |
| `PsuGI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/PsuGI.html) | REBASE primary record (direct experimental submission; not a claim of a peer-reviewed paper) |
| `RlaI` | 5mC or 5hmC; glucosylated 5hmC is not an equivalent substrate | [REBASE](https://rebase.neb.com/rebase/enz/RlaI.html) | [Cohen-Karni et al. 2011](https://doi.org/10.1073/pnas.1018448108) |
| `SgeI` | 5-methylcytosine (5mC) | [REBASE](https://rebase.neb.com/rebase/enz/SgeI.html) | [Primary patent US20110207139A1](https://patents.google.com/patent/US20110207139A1/en), identified by REBASE reference 13511 |
| `SgrTI` | 5mC or 5hmC; glucosylated 5hmC is not an equivalent substrate | [REBASE](https://rebase.neb.com/rebase/enz/SgrTI.html) | [Cohen-Karni et al. 2011](https://doi.org/10.1073/pnas.1018448108) |
| `TagI` | 5mC or 5hmC, not glucosylated 5hmC; two modified sites enhance cleavage | [REBASE](https://rebase.neb.com/rebase/enz/TagI.html) | [Kisiala et al. 2018](https://doi.org/10.1093/nar/gky781) |
| `YkrI` | Modified cytosine; strong preference for 5hmC-related substrates; see glucosylation caveats below | [REBASE](https://rebase.neb.com/rebase/enz/YkrI.html) | [Borgaro & Zhu 2013](https://pmc.ncbi.nlm.nih.gov/articles/PMC3627594/); [base-flipping experiments 2014](https://pmc.ncbi.nlm.nih.gov/articles/PMC4259335/) |

## Family-list coverage, including absent members

- **MspJI family**, Cohen-Karni et al., PNAS 2011,
  <https://doi.org/10.1073/pnas.1018448108> (primary abstract at
  <https://pubmed.ncbi.nlm.nih.gov/21690366/>): all six characterized names are in
  the catalog and classified: MspJI, FspEI, LpnPI, AspBHI, RlaI, SgrTI.
  SgeI is separately supported by its own REBASE record and primary patent.
- **PvuRts1I family**, Borgaro & Zhu 2013 Table 1,
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC3627594/>: the 20-name table intersects
  the catalog at exactly AbaSI, BmeDI, YkrI. The other 17 are absent:
  AbaAI, AbaBGI, AbaCI, AbaDI, AbaHI, AbaTI, AbaUI, AcaPI, BbiDI, EsaMMI, EsaNI,
  Mte37I, PatTI, PfrCI, PpeHI, PvuRts1I, PxyI. Similar prefixes (e.g. AbaCIII,
  AbaUMB2I) do not establish membership. AbaUMB2I is independently classified
  by its own methyl-directed REBASE record, not as an AbaSI homolog.
- **BisI family**, Xu et al. 2016 Tables 1–3,
  <https://pmc.ncbi.nlm.nih.gov/articles/PMC4926085/>: the modification-dependent
  active table entries present in this catalog are BisI and NhoI. Absent are
  Rfl17I, BceYI, Bce95I, Vsp586I, KasKI, CbuDI, Pps170I, VspHI, MspAK21I,
  AspTB23I, LsaM18I, Eco15I, Sde240I, Pan13I, Pru4541I, AlaI, Sve396I, SqiI,
  Dsp20IU, SmaAUI, MbaR4I, Esp638I. Importantly, the paper's Bce1273I and
  Bth171I cleave unmodified DNA: family membership alone is **not** sufficient.
  GluI and PkrI are supported in the paper's introductory characterization;
  BlsI and GlaI are also supported by Tarasova et al. 2008.
- **EcoBLMcrX**, <https://pmc.ncbi.nlm.nih.gov/articles/PMC5487053/>:
  EcoBLMcrX and NhoI are present; BceYI is absent. Do not confuse `McrI` with
  McrA/McrBC or EcoBLMcrX. `McrI` retains its ordinary classification.
- The review <https://pmc.ncbi.nlm.nih.gov/articles/PMC3874153/> Tables 2–3
  was used as a **candidate index**, not final authority. In particular, its
  ambiguous MalI placement does not override the exact MalI source record's
  methyladenine chemistry. The exhaustive REBASE scan additionally finds
  AbaUMB2I, Dde51507I, EcoO157SI, Pfl8569I, PsuGI and TagI.

## Chemistry and major artifact contributors

- **BmeDI and YkrI are not generic C cutters.** The 2013 paper and the direct
  2014 experiments (<https://pmc.ncbi.nlm.nih.gov/articles/PMC4259335/>) use
  modified substrates. BmeDI's unusually close cuts (predominantly C+2–3/+0–1)
  are not evidence that an unmodified C motif will cleave. Geometry remains
  exactly as supplied by the native catalog; this change only labels and groups.
- **EcoO157SI is resolved**, not guessed from its single-C motif. Its exact
  [source record](https://rebase.neb.com/rebase/enz/EcoO157SI.html), REBASE 101311,
  says Type II Methyl-directed, gives a 5hmC recognition base, calls it a
  PvuRts1I neoschizomer, and explicitly says it also cuts m5C-containing
  sequences. Its [primary submission references](https://rebase.neb.com/cgi-bin/erefget?101311)
  are Cheng et al. (18893) and Roberts (5639), unpublished observations.
  No peer-reviewed EcoO157SI paper or glucosylation profile is asserted.
- **LpnPI** is an experimentally characterized MspJI-family member, not an
  ordinary cutter of unmodified CC-containing motifs.
- **AbaSI**: 5hmC and beta-glucosylated 5hmC are substrates; alpha-glucosylation
  strongly inhibits relative to beta-glucosylation (2013 paper: about 500-fold).
  It is incorrect to describe all glucosylated forms as equally accepted.
- **BmeDI/YkrI**: hydroxymethylcytosine preference is supported; the UI makes
  no universal alpha/beta-glucosylation promise. The 2013 paper reports differing
  substrate selectivities across homologs. Low residual activity under particular
  assay conditions is not ruled out by a categorical UI label, so the shared note
  says sequence matches alone do not establish cleavage, rather than making an
  absolute no-cleavage claim at every concentration and on every substrate.
- **MspJI family**: 5mC/5hmC recognition does not extend to glucosylated 5hmC
  (also summarized in the review's Table 2). **TagI** independently accepts 5mC
  and 5hmC but not glucosylated 5hmC; two modified sites enhance cleavage.
- Other 5mC entries do not implicitly gain 5hmC or glucosyl-5hmC activity.
  Sequence context, modification number/strand arrangement, density and reaction
  conditions remain relevant; the UI does not simulate those chemical properties.

## Explicit unresolved items and exclusions

The five exact catalog IDs whose REBASE name URLs returned 404 are
`CviKI_1`, `Hpy99XIV_mut1`, `HpyAXVI_mut1`, `HpyAXVI_mut2`, and
`HpyUM032XIII_mut1`. Their catalog provenance has no REBASE record ID/URI.
They remain **unresolved source lookups and unchanged ordinary entries**, not
newly declared unmodified-DNA cutters and not inferred modification-dependent
from a base motif. Exact IDs were not silently repaired or conflated with another
source name. These are the only unresolved whole-catalog page lookups; all known
methyl-directed cytosine candidates found by the retrieved source records are
classified. Future catalog additions require source review, not a name heuristic.

For AbaUMB2I, Dde51507I, Pfl8569I, PsuGI, EcoO157SI and KroI, the retrieved
REBASE related-reference lists contain unpublished observations; the primary
source record supports classification, but publication-level experimental detail
remains unavailable. AoxI/PcsI/SgeI patent references and MteI's paper were
identified from REBASE's reference lists, not represented as independently
reproduced experiments. These limitations are evidence, not workflow gates.

Methylation-sensitive ordinary cutters such as HpaII, MspI, EcoRI and BamHI,
adenine-dependent DpnI and MalI, and similarly named GsaI, McrI, AbaCIII and
RlaII are not promoted into Special cases.

## Reproducibility and receiving tests

The audit's complete per-ID records and retrieved primary texts are retained
outside the repository under the task evidence directory
`pl931-desktop-check/science-classification` (`rebase-catalog-audit.jsonl`,
`rebase-<ID>.txt`, `references-<ID>.txt`, and PMC text extracts). The committed
ledger documents the source scope without committing transient network logs.
Some PMC HTML endpoints returned CAPTCHA pages; Europe PMC's fullTextXML service
provided the 2013/2014 and BisI/GlaI/EcoBLMcrX full texts. REBASE's certificate
chain could not be verified in this tool environment; read-only public page
retrieval used an unverified TLS context, not credentials or private data.

`molBioTrafficSpecialCases.test.tsx` independently enumerates all 27 names,
reconciles them against every catalog ID, checks negative controls and chemical
qualifications, exercises all five ordinary quick groups, and checks explicit
manual mapping/digest access and unchanged native annotation coordinates.
`molBioTrafficPairingMounted.test.tsx` uses an independent exact-ID expectation
for both full and compact inventory, Special cases enumeration/bulk mapping,
and native digest capability preservation. Neither inventory expectation is
computed from the production helper.
