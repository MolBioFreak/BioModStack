# Renderer state bridge

`src/components/alignments/lazyViewers.tsx` exports `alignmentViewers` for the route owner. It uses three independent React.lazy imports; AlignmentWorkspace supplies Suspense. Do not also statically import adapters in the route. Each adapter retains a full-document sandbox, not a server-tiled or fourth matrix implementation.

## Supported native APIs

| Renderer | Read/restore | Selection and marks | Limits |
| --- | --- | --- | --- |
| JBrowseMSA 9.8.0 | onModel + MobX reaction; setScrollX/Y, setColWidth, setRowHeight, setColorSchemeName, setHideGaps, setAllowedGappyness | setSelection (file columns, 1-based); setHighlights (columns or row residues, 1-based) | Gap-threshold column filtering only; no arbitrary hidden-column API. |
| AV2 beta-23 | Exported reduxStore; pinned VirtualizationReducers.setWorldOffset with alignment UUID axis IDs; zoomLevel, aaColorScheme/ntColorScheme props | highlightPositionalMatches using upstream block generators | Highlights key on sequence strings, not row identity. Row-scoped marks/selection on duplicate sequences are omitted with a visible count. Column marks remain supported. No arbitrary hidden-column API or native mark labels. |
| BioJS 1.0.3 | g.zoomer, g.colorscheme, g.columns models and their change events | g.selcol.resetJSON native position/column selections | Marks share native selection outlines, without native labels. Labels remain in the host list. |

AV2 scroll restore waits for native axes to have the requested font-derived cell sizes; otherwise its zoom implementation rescales restored offsets. Its highlight cache key includes actual highlighted positions. Blob workers are allowed **only for AV2** because upstream block generation requires them; `connect-src 'none'` and the opaque sandbox remain. Letter DOM alone was not evidence of a working color/mark canvas.

Shared source marks are projected by explicit source member, occurrence and unambiguous verified source-position maps. Hidden/reordered rows keep their original aligned strings/maps; projection never strips columns or changes source coordinates. Frame messages check WindowProxy, channel and per-instance token. Control messages do not resend rows. The embedded code is trusted: an opaque sandbox is not a general exfiltration guarantee.

## Shared View wire contract (API owner)

The frontend saves `selected_marks` and an optional `renderer_state` object alongside existing View fields. The API's closed View model must accept/preserve that optional field before deploying this frontend. No backend/native-settings schema is changed by this renderer patch.

`renderer_state` fields: `scrollX`, `scrollY`, `columnWidth`, `rowHeight`, `colorScheme`, `hiddenColumns`, `hideGaps`, `allowedGappyness`, `zoomLevel`. Each renderer emits only its supported fields. Scroll offsets are native renderer pixels (JBrowse negative, AV2/BioJS positive); settings are restored only to the saved renderer, not interpreted as cross-renderer equivalents.

## Qualification

From `platform/frontend`:

```
pnpm --filter @bms/jbrowse-viewer build
pnpm --filter @bms/av2-viewer build
pnpm --filter @bms/biojs-viewer build
pnpm exec vitest run --environment jsdom tests/vitest/alignmentRendererState.test.tsx tests/vitest/alignmentLazyViewers.test.tsx tests/vitest/alignmentViewStateMounted.test.tsx tests/vitest/alignmentShared.test.tsx tests/vitest/alignmentWorkspace.test.tsx tests/vitest/biojsViewer.test.tsx
python tests/alignment_renderer_browser.py
pnpm exec tsc -b
```

The browser test requires Chrome/Chromium, Python websockets and TMPDIR. It uses private scratch artifacts, real built bundles, opaque frames and CDP; asserts nondefault state readback and remount restoration, and native pixel changes when marks/selection are cleared. Browser process site isolation is disabled only for this probe's context inspection, not in the application. It does not claim authenticated Project save/export/import acceptance or containment beyond the stated CSP. Mounted tests cover shared-view save/reopen with mocked transport, explicit source mapping, authenticated frame control, and selected-only import dispatch. Production build chunk inspection must additionally confirm adapters remain independent chunks.
