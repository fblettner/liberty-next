// The action prompt (``prompt_fields``): an action that asks for values before it runs pauses the
// runner on ``requestPrompt``; the returned ``promptDialog`` (an ActionPromptDialog) collects them.
// Shared by the grid (row menu, toolbar actions, batch hooks) and the record form (its hooks).
import { useCallback, useRef, useState, type ReactNode } from 'react'
import type { PromptField } from '../../types/screens'
import { ActionPromptDialog } from './ActionPromptDialog'
import type { Row } from './dialogHelpers'

export function useActionPrompt(): {
  requestPrompt: (spec: { fields: PromptField[]; title: string | null; cols: number | null; submitLabel: string | null }, fallbackTitle: string) => Promise<Row | null>
  promptDialog: ReactNode
} {
  // Imperative-from-async prompt plumbing. The chain runner pauses on actions with
  // ``prompt_fields`` and awaits ``requestPrompt`` — which sets ``pendingPrompt`` state +
  // stows a resolver in the ref. The mounted ActionPromptDialog reads the state; its onSubmit
  // resolves with the entered values, its onCancel resolves with ``null`` (chain aborts soft —
  // no error banner). The ref pattern keeps ``requestPrompt`` referentially stable so the
  // runner's useCallback deps don't churn on every state change.
  const [pendingPrompt, setPendingPrompt] = useState<
    | { fields: PromptField[]; title: string; cols: number | null; submitLabel: string | null }
    | null
  >(null)
  const promptResolveRef = useRef<((v: Row | null) => void) | null>(null)
  const requestPrompt = useCallback((spec: { fields: PromptField[]; title: string | null; cols: number | null; submitLabel: string | null }, fallbackTitle: string): Promise<Row | null> => {
    return new Promise<Row | null>((resolve) => {
      promptResolveRef.current = resolve
      setPendingPrompt({
        fields: spec.fields,
        title: spec.title || fallbackTitle,
        cols: spec.cols,
        submitLabel: spec.submitLabel,
      })
    })
  }, [])
  const handlePromptSubmit = useCallback((v: Row) => {
    const r = promptResolveRef.current; promptResolveRef.current = null
    setPendingPrompt(null)
    r?.(v)
  }, [])
  const handlePromptCancel = useCallback(() => {
    const r = promptResolveRef.current; promptResolveRef.current = null
    setPendingPrompt(null)
    r?.(null)
  }, [])

  const promptDialog = pendingPrompt && (
    <ActionPromptDialog
      open
      title={pendingPrompt.title}
      fields={pendingPrompt.fields}
      cols={pendingPrompt.cols}
      submitLabel={pendingPrompt.submitLabel}
      onSubmit={handlePromptSubmit}
      onCancel={handlePromptCancel}
    />
  )

  return { requestPrompt, promptDialog }
}
