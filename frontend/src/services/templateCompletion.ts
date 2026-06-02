// Column- and param-aware completion for the custom-report template editor.
// Same shape as services/sqlCompletion.ts: the completion provider is
// registered ONCE per Monaco instance; each editor attaches its (columns,
// params) context to its model via `attachTemplateContext`, and the provider
// looks the model up in a WeakMap (so a closed editor's context is GC'd).
//
// Triggers — Monaco fires us on `.` and `[`, plus Ctrl-Space:
//   1. After `row.` / `rows[N].` (any name resembling a row variable) →
//      suggest column names from the bound query.
//   2. After `ctx.data.rows[N].` → same as above.
//   3. After `ctx.params.` → suggest the declared param names.
//   4. After `ctx.data.` → suggest `rows` / `columns`.
//   5. After `ctx.` → suggest `data` / `params`.
//
// Pattern matching is intentionally loose ("a word ending in 'row' or 'rows'")
// so the operator's loop variable conventions (`r`, `row`, `entry`, etc.)
// still get column suggestions — we fall back to suggesting columns when the
// dot follows ANY single identifier inside a `{% for %}` loop body. Net
// result: more suggestions than strictly correct, but no false positives that
// would mislead the operator (the suggestion list always reflects real
// columns or real params).
import type * as Monaco from 'monaco-editor/esm/vs/editor/editor.api'

export interface TemplateColumn {
  name: string
  label: string | null
  type: string | null
}
export interface TemplateContext {
  columns: TemplateColumn[]
  paramNames: string[]
}

const modelContexts = new WeakMap<Monaco.editor.ITextModel, TemplateContext>()
let providerRegistered = false

/** Attach a (columns, params) context to a Monaco model so the completion
 *  provider can find it. Re-attaching for the same model is a plain overwrite
 *  — the editor's `onMount` handler calls this once on mount, then again
 *  whenever the bound query's columns refresh. */
export function attachTemplateContext(
  monaco: typeof Monaco,
  model: Monaco.editor.ITextModel,
  ctx: TemplateContext,
): void {
  modelContexts.set(model, ctx)
  ensureProvider(monaco)
}

function ensureProvider(monaco: typeof Monaco): void {
  if (providerRegistered) return
  providerRegistered = true
  monaco.languages.registerCompletionItemProvider('markdown', {
    triggerCharacters: ['.', '['],
    provideCompletionItems(model, position) {
      const ctx = modelContexts.get(model)
      if (!ctx) return { suggestions: [] }
      const textBefore = model.getValueInRange({
        startLineNumber: 1, startColumn: 1,
        endLineNumber: position.lineNumber, endColumn: position.column,
      })
      const word = model.getWordUntilPosition(position)
      const range: Monaco.IRange = {
        startLineNumber: position.lineNumber, endLineNumber: position.lineNumber,
        startColumn: word.startColumn, endColumn: word.endColumn,
      }
      // Only fire inside a Jinja expression `{{ ... }}` or statement `{% ... %}`.
      // Crude scoping: cheaper than parsing the template, and false positives
      // (suggesting columns in plain markdown) would just add noise.
      if (!insideJinjaTag(textBefore)) return { suggestions: [] }

      // Find what's immediately before the trigger position — `<token>.` or `]`.
      // We strip the currently-typed word so `row.cpt|` resolves "after `row.`",
      // not "after `row.cpt`".
      const head = textBefore.replace(/[\w]*$/, '').trimEnd()
      const dotMatch = head.match(/([\w\]]+)\.$/)
      if (!dotMatch) return { suggestions: [] }
      const target = dotMatch[1]

      // `ctx.` → data / params
      if (/^ctx$/.test(target)) {
        return {
          suggestions: [
            attrSuggestion(monaco, 'data', 'mapping (rows, columns)', range),
            attrSuggestion(monaco, 'params', 'operator inputs', range),
          ],
        }
      }
      // `ctx.data.` → rows / columns
      if (/data$/i.test(target) && /\bctx\.data$/i.test(head.slice(0, -1))) {
        return {
          suggestions: [
            attrSuggestion(monaco, 'rows', 'list of row dicts', range),
            attrSuggestion(monaco, 'columns', 'list of column metadata', range),
          ],
        }
      }
      // `ctx.params.` → declared params
      if (/^params$/i.test(target) && /\bctx\.params$/i.test(head.slice(0, -1))) {
        return {
          suggestions: ctx.paramNames.map((n) => paramSuggestion(monaco, n, range)),
        }
      }
      // `rows[N].` / `row.` / any loop variable inside a {% for row in ... %} →
      // columns. We detect this by either an indexed access (`]` before the dot)
      // or a single identifier (1-30 word chars). To keep false positives down,
      // also require we're inside a `{% for ... %}` block — meaning a `{% for`
      // appears earlier in the textBefore than the latest `{% endfor %}`.
      const insideForLoop = (() => {
        const lastFor = textBefore.lastIndexOf('{% for ')
        const lastEnd = textBefore.lastIndexOf('{% endfor')
        return lastFor > -1 && lastFor > lastEnd
      })()
      const isIndexed = /\]$/.test(target)
      const isLoopVar = /^[a-zA-Z_]\w{0,29}$/.test(target) && insideForLoop
      if ((isIndexed || isLoopVar) && ctx.columns.length > 0) {
        return {
          suggestions: ctx.columns.map((c) => columnSuggestion(monaco, c, range)),
        }
      }
      return { suggestions: [] }
    },
  })
}

/** True iff the cursor sits inside an unclosed Jinja tag (`{{ … `, `{% … `).
 *  We don't try to handle the literal `{% raw %}` block — operators in this
 *  editor never disable Jinja for a custom report's template. */
function insideJinjaTag(textBefore: string): boolean {
  const lastOpenExpr = textBefore.lastIndexOf('{{')
  const lastCloseExpr = textBefore.lastIndexOf('}}')
  if (lastOpenExpr > lastCloseExpr) return true
  const lastOpenStmt = textBefore.lastIndexOf('{%')
  const lastCloseStmt = textBefore.lastIndexOf('%}')
  if (lastOpenStmt > lastCloseStmt) return true
  return false
}

function attrSuggestion(
  monaco: typeof Monaco, name: string, detail: string, range: Monaco.IRange,
): Monaco.languages.CompletionItem {
  return {
    label: { label: name, description: detail },
    kind: monaco.languages.CompletionItemKind.Property,
    insertText: name,
    range,
    sortText: `0_${name}`,
  }
}

function columnSuggestion(
  monaco: typeof Monaco, c: TemplateColumn, range: Monaco.IRange,
): Monaco.languages.CompletionItem {
  return {
    label: { label: c.name, description: c.type ?? '' },
    kind: monaco.languages.CompletionItemKind.Field,
    insertText: c.name,
    range,
    detail: c.label ?? undefined,
    sortText: `1_${c.name}`,
  }
}

function paramSuggestion(
  monaco: typeof Monaco, name: string, range: Monaco.IRange,
): Monaco.languages.CompletionItem {
  return {
    label: { label: name, description: 'operator param' },
    kind: monaco.languages.CompletionItemKind.Variable,
    insertText: name,
    range,
    sortText: `2_${name}`,
  }
}
