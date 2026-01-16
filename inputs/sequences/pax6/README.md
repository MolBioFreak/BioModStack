# PAX6 Sequence Presets

Cross-species PAX6 protein sequences for antibody design experiments.

## Sets

### Set 1: Short (Paired Domain Only)
- **File:** `pax6_short_paired_domain.fasta`
- **Length:** ~133 aa
- **Optimal for:** RFdiffusion structural rigidity, high pLDDT scores
- **Risk:** May bind a face hidden in the full protein or Western blot

### Set 2: Intermediate (Paired Domain + Linker)
- **File:** `pax6_intermediate_with_linker.fasta`
- **Length:** ~210 aa
- **Optimal for:** Western Blot targeting (Linker is solvent exposed and linear)
- **Risk:** Linker is disordered; folding models may hallucinate a rigid structure

### Set 3: Long (Paired Domain + Linker + Homeodomain)
- **File:** `pax6_long_with_homeodomain.fasta`
- **Length:** ~275 aa
- **Optimal for:** Full structural context (two rigid "beads" separated by a string)
- **Note:** Algorithms may struggle to predict relative orientation of domains

## Species

1. Human/Salamander (identical)
2. Chicken
3. Zebrafish (Pax6a)
4. Elephant Shark
5. Drosophila (Eyeless ortholog)
