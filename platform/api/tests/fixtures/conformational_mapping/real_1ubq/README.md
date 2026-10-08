# Real 1UBQ conformational-mapping fixtures

These bounded fixtures retain the real-file evidence used to validate the external-mmCIF conformational-mapping path.

- `1UBQ.protein-only-authoritative.cif` contains the standard-protein polymer and atom authority for PDB entry **1UBQ** used by the admission and mandatory-normalization replay. Atom-row trailing whitespace was removed for repository hygiene; no mmCIF tokens were changed. SHA-256 before whitespace normalization: `9b6214292be73bce10c12eaf92eed34e1c24c7c18323400c140f9dd9423def4b`. Retained canonical SHA-256: `5674064f6f64c87da2e1f564979c83220d68d3c126dddb19aad229d83b67dc0b`.
- `frustrampnn.csv` is retained output from the verified real FrustraMPNN invocation against normalized 1UBQ. It contains 1,520 substitution rows (76 residues × 20 amino acids). SHA-256: `2084353640cbe5f06847bc78c0787f1062edb2c891d3808adfe2d6aa57b0fa36`.

Tests bind these hashes and replay admission → PDB normalization → authoritative structure-map join → Frustra landscape finalization. The expected result is 76 mapped residues, 20 substitution slots per residue, and no input issues.
