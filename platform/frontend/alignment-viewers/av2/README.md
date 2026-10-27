# AlignmentViewer 2 adapter

AV2 beta-23 and React 18 are built separately with the pnpm workspace lockfile. Frontend `dev` and `build` build this bundle automatically. The build transforms AV2's storage access to a volatile map, fixes its SCSS export for Vite, and instruments its virtualized matrix click handler. The transform checks the pinned upstream source shape; inspect it again when upgrading AV2.

The opaque sandbox receives synthetic row identifiers and aligned strings once in its initial srcdoc; the host maps validated click indices to BMS row IDs and coordinates. Duplicate sequences are retained in input order. It loads the entire alignment. Existing shared selections, marks and saved views are not drawn or restored by AV2. Scroll/zoom, large alignments and all symbol conventions are not comprehensively qualified.

The sandbox CSP blocks connect requests, forms, workers and external images; it does **not** prevent a compromised viewer from navigating its own frame to an external URL with alignment data. The observed network audit is not a proof against exfiltration or all interactions. Do not treat this isolation as a complete network security boundary.

## Synthetic Chromium probe

From `platform/frontend`, with Node 22+, Chrome, workspace dependencies and AV2 bundle installed:

1. Run `pnpm --filter @bms/av2-viewer build`.
2. Start `pnpm exec vite --host 127.0.0.1 --port 4178` in one terminal.
3. Start a dedicated Chrome with `google-chrome --headless=new --no-sandbox --disable-gpu --remote-debugging-port=19093 --user-data-dir="$TMPDIR/av2-chrome" about:blank`.
4. Run `node tests/browser/av2-probe.cjs`. It loads only synthetic data, exercises the actual host, clicks a gap, checks the shared anchor and audits CDP network requests including the isolated frame and WebSockets. The local Vite dev socket is expected. This probe uses a fixed initial zoom/viewport and does not establish restored selection or exhaustive privacy coverage.
