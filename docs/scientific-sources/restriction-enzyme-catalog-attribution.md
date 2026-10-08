# Restriction-enzyme catalog source and attribution

## Frozen release source

BioModStack catalog `biopython-rebase-404-bms-v1` is generated offline from the installed **Biopython 1.87** module `Bio.Restriction.Restriction_Dictionary`. Its header states `Used REBASE emboss files version 404 (2024)`, i.e. **REBASE EMBOSS release 404**. The exact installed dictionary source is pinned by SHA-256 `2a79099295dbad6061ea67a11e053787c591fcb2eb10fc8c0f89ead908dfa02b`.

Biopython data are REBASE-derived. Biopython's refresh process is manual; this release is not a live REBASE mirror. The base source contains 1,088 records: 754 have complete primary top/bottom geometry, 334 remain recognition-only, and 623 of the 754 geometry-ready records carry historical commercial-source codes. Those historical codes are provenance and are **not current supplier availability**.

The release also contains four separately sourced, BMS-curated nickase records. Each record binds an official REBASE enzyme-page URL, REBASE Enz ID, retrieval date `2026-08-31`, page SHA-256, and page record-modified date. These exact single-strand records support recognition/nick analysis only and are not digestible double-strand cutters.

## REBASE attribution and update boundary

REBASE is maintained by New England Biolabs and publishes restriction-enzyme data files for no charge. That no-charge publication condition does not, by itself, establish every right for hosted APIs or redistribution of a derived database. The Biopython-derived release and the four reviewed page receipts are the only approved inputs to this catalog version. A bulk or otherwise direct future REBASE import is a **separate reviewed gate** requiring source-terms review, exact source-file receipts, schema/digest regeneration, deterministic comparison, scientific change review, and explicit activation. No runtime or request path downloads REBASE or supplier content.

Updates are source-controlled releases, never automatic activation. The generator must run twice to exact-byte equality; additions, removals, geometry changes, relationship changes, and historical supplier-code changes require review. Supplier products, stock, price, buffers, and reaction conditions are a separate catalog and policy lane.

Official source: [REBASE](https://rebase.neb.com/rebase/rebase.html).

## Biopython notice

Biopython 1.87 is distributed under the **Biopython License Agreement** unless an individual file states otherwise. The notice retained for this derived artifact is:

> Permission to use, copy, modify, and distribute this software and its
> documentation with or without modifications and for any purpose and
> without fee is hereby granted, provided that any copyright notices
> appear in all copies and that both those copyright notices and this
> permission notice appear in supporting documentation, and that the
> names of the contributors or copyright holders not be used in
> advertising or publicity pertaining to distribution of the software
> without specific prior permission.
>
> THE CONTRIBUTORS AND COPYRIGHT HOLDERS OF THIS SOFTWARE DISCLAIM ALL
> WARRANTIES WITH REGARD TO THIS SOFTWARE, INCLUDING ALL IMPLIED
> WARRANTIES OF MERCHANTABILITY AND FITNESS, IN NO EVENT SHALL THE
> CONTRIBUTORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY SPECIAL, INDIRECT
> OR CONSEQUENTIAL DAMAGES OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS
> OF USE, DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE
> OR OTHER TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE
> OR PERFORMANCE OF THIS SOFTWARE.

The canonical upstream notice is `biopython-1.87.dist-info/licenses/LICENSE.rst` in the pinned package and `LICENSE.rst` in the Biopython source distribution.
