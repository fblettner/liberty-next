// The longer explanation of a generated form's field (the model's own description, behind
// "more"): written by developers with ``name`` or `name` around what is typed as it is — a field
// name, a value, a piece of SQL. Cut into text and code so the form shows the code as such
// instead of the backticks. No React here.

export interface HintPart {
  text: string
  code: boolean
}

/** The parts of *text*, in order: plain text, and what was between backticks (single or double). */
export function hintParts(text: string): HintPart[] {
  const parts: HintPart[] = []
  const re = /``([^`]+)``|`([^`]+)`/g
  let last = 0
  for (let m = re.exec(text); m; m = re.exec(text)) {
    if (m.index > last) parts.push({ text: text.slice(last, m.index), code: false })
    parts.push({ text: m[1] ?? m[2], code: true })
    last = m.index + m[0].length
  }
  if (last < text.length) parts.push({ text: text.slice(last), code: false })
  return parts.map((p) => (p.code ? p : { ...p, text: p.text.replace(/\*\*([^*]+)\*\*/g, '$1').replace(/\*([^*\s][^*]*)\*/g, '$1') }))
}
