const MOLSTAR_CSS_REPLACEMENTS: ReadonlyArray<readonly [string, string]> = [
  ['display:block-inline', 'display:inline-block'],
  ['font-weight:light', 'font-weight:300'],
  ['background-color:tint(rgb(51, 43, 31), 60%)', 'background-color:#adaaa5'],
  ['border-color:tint(rgb(51, 43, 31), 50%)', 'border-color:#99958f'],
  ['filter:alpha(opacity=0);', ''],
]

const OBSOLETE_MOLSTAR_SELECTOR_RULES = [
  /[^{}]*::-moz-focus-inner[^{}]*\{[^{}]*\}/gu,
  /[^{}]*:-ms-input-placeholder[^{}]*\{[^{}]*\}/gu,
] as const

export function sanitizeMolstarCss(source: string): string {
  let sanitized = source
  for (const selectorRule of OBSOLETE_MOLSTAR_SELECTOR_RULES) {
    sanitized = sanitized.replace(selectorRule, '')
  }
  for (const [legacyCss, replacementCss] of MOLSTAR_CSS_REPLACEMENTS) {
    sanitized = sanitized.replaceAll(legacyCss, replacementCss)
  }
  return sanitized
}
