# Pinned upstream test source attribution

These byte-identical `.py.txt` snapshots are TEST DATA ONLY. They are not a
replacement runtime or modified scientific implementation. `provenance.json`
records upstream repositories, exact commits, paths, and SHA-256 hashes.

- ConforNets: AQ Laboratory / AlQuraishi Laboratory,
  https://github.com/aqlaboratory/confornets at
  `4df561a1fbd0fd2b9c7a230fa62957a837d9f72d`.
  Its pinned `pyproject.toml` declares `license = { text = "Apache-2.0" }`
  and `authors = [{ name = "AQ Laboratory" }]`. No root LICENSE or NOTICE
  file exists in that pinned Git tree.
- OpenFold3: Copyright 2026 AlQuraishi Laboratory,
  https://github.com/aqlaboratory/openfold-3 at
  `cc8bf9dd3f162fed45cc1189a130877996b425d3`.
  `OpenFold3-LICENSE` is copied verbatim from that checkout and contains the
  Apache License 2.0 applicable to these snapshots. No root NOTICE exists in
  that pinned Git tree. Existing file-level copyright/attribution headers,
  including those crediting DeepMind Technologies and the Apache license,
  remain intact in the byte-identical snapshots.

The test harness extracts named AST definitions without modifying them and
supplies explicitly marked heavyweight infrastructure/storage doubles. It
never claims to execute a full scientific prediction. No snapshot is modified.
