// AV2's alphabet/type detector, logos and palettes only recognize uppercase.
// Fold ASCII letters for display only: never change the host's retained rows or
// column coordinates (Unicode uppercasing can expand a character).
export const displaySequence = sequence => sequence.replace(/[a-z]/g, letter => letter.toUpperCase())

export function projectHighlights(rows, marks = [], selection) {
  const highlights = {}
  let omitted = 0
  const spans = [...marks, ...(selection ? [{index: selection.index, start: selection.column, end: selection.column}] : [])]
  for (const span of spans) {
    if (span.index != null && rows.filter(row => row.aligned === rows[span.index]?.aligned).length !== 1) { omitted++; continue }
    for (const row of span.index == null ? rows : [rows[span.index]]) {
      if (!row) continue
      const positions = highlights[row.aligned] ??= []
      for (let p = span.start; p <= Math.min(span.end, row.aligned.length - 1); p++) positions.push(p)
    }
  }
  return {omitted, highlightMatches: Object.values(highlights).some(positions => positions.length)
    ? {searchString: JSON.stringify(highlights), posidxsMatchedPerSequence: highlights, highlightColor: {red: 255, green: 165, blue: 0}}
    : undefined}
}
