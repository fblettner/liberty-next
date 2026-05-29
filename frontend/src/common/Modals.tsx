// Themed replacement for the browser-native ``window.confirm`` / ``window.prompt`` /
// ``window.alert`` dialogs. The browser ones don't honour the app's theme, can't be styled
// for danger/warning, and look out of place against the liquid-glass shell. ``useModals``
// gives each call site an async API (``await modals.confirm({...})``) that resolves with the
// user's choice — same imperative-from-async pattern the ScreenDialog's prompt-before-fire
// already uses for action input dialogs.
//
// Usage:
//
//   const modals = useModals()
//   const ok = await modals.confirm({ title: 'Delete pool?', message: '…', variant: 'danger' })
//   if (!ok) return
//   const name = await modals.prompt({ title: 'New pool', message: 'Name?' })
//   await modals.alert({ title: 'Invalid', message: '…' })
//
// All three resolve when the user clicks a button (confirm: bool, prompt: str|null, alert: void).
// The promise resolves to the "cancel" outcome (``false`` / ``null`` / ``void``) when the user
// clicks the overlay or presses Escape — same as the browser dialogs.
//
// Mount ``<ModalsProvider>`` once near the top of the tree (``main.tsx``). The provider keeps
// at most one modal queued at a time; firing a second call while the first is open replaces it
// (the first promise resolves to the cancel value before the new modal opens).
import {
  createContext, useCallback, useContext, useEffect, useRef, useState,
  type ReactNode,
} from 'react'
import { createPortal } from 'react-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from './Modal'
import { Button } from './Button'
import { Input } from './Input'

// Provider-level modals always paint on top of every other modal in the app — the Screen
// Designer (portaled to document.body, z-index 400), the visual builder, dropdowns
// (z-index 1000), etc. ``useModals().confirm`` is the "global manager" that interrupts
// whatever's underneath, so its overlay raises above everything. Each modal is also
// ``createPortal``-ed to ``document.body`` so it doesn't get trapped inside another
// portal's stacking context.
const TopOverlay = styled(Overlay)`z-index: 2000;`

export interface ConfirmOptions {
  title: string
  /** Body content — a string, or any JSX node when the message wants emphasis / a list / etc. */
  message: ReactNode
  /** Confirm button colour. ``danger`` for destructive actions (delete, remove, drop). */
  variant?: 'primary' | 'danger'
  confirmLabel?: string
  cancelLabel?: string
}

export interface PromptOptions {
  title: string
  /** Optional description shown above the input. */
  message?: ReactNode
  /** Pre-fills the input (for rename flows; same role as window.prompt's second arg). */
  defaultValue?: string
  placeholder?: string
  submitLabel?: string
  cancelLabel?: string
  /** Returns ``null`` when the value is valid, else an error string to render under the input.
   *  Empty / whitespace-only values are rejected with the default "required" message unless
   *  the caller opts out by passing a custom validator that accepts them. */
  validate?: (value: string) => string | null
  /** Allow empty values to submit (default ``false`` — the prompt requires non-blank input,
   *  matching window.prompt's behaviour where the caller checks for non-empty anyway). */
  allowEmpty?: boolean
}

export interface AlertOptions {
  title: string
  message: ReactNode
  okLabel?: string
  /** Visual tone of the OK button (defaults to ``primary``). */
  variant?: 'primary' | 'danger'
}

export interface ChooseOption<V extends string = string> {
  /** Stable value returned to the caller when the operator picks this option. */
  value: V
  /** Display label on the button. */
  label: string
  /** Visual tone — ``primary`` for the recommended action, ``danger`` for destructive ones,
   *  ``ghost`` for the dismiss / "keep editing" path. Defaults to ``ghost``. */
  variant?: 'primary' | 'danger' | 'ghost'
  /** When true, focuses the button on open — usually the safest non-destructive option so
   *  Enter doesn't accidentally trigger a discard. */
  autoFocus?: boolean
}

export interface ChooseOptions<V extends string = string> {
  title: string
  message: ReactNode
  /** 2-5 button choices. Order = left-to-right in the footer. */
  options: ReadonlyArray<ChooseOption<V>>
  /** Value resolved when the operator dismisses the modal via overlay click or Escape.
   *  When unset (``null``), Escape / overlay click resolves ``null`` — the caller treats it
   *  as "keep editing" / no decision. */
  cancelValue?: V | null
  /** When set, render an explicit leftmost ghost Cancel button (resolves like Escape →
   *  ``cancelValue``). Omit for dialogs whose options already include a "stay/keep" choice. */
  cancelLabel?: string
}

export interface ModalsContextValue {
  confirm: (opts: ConfirmOptions) => Promise<boolean>
  prompt: (opts: PromptOptions) => Promise<string | null>
  alert: (opts: AlertOptions) => Promise<void>
  /** Multi-button choice — for "unsaved changes: Save / Discard / Keep editing" and similar
   *  three-way dialogs that don't fit the binary ``confirm``. Returns the picked option's
   *  ``value``, or ``cancelValue`` (default ``null``) on Escape / overlay click. */
  choose: <V extends string>(opts: ChooseOptions<V>) => Promise<V | null>
}

const ModalsContext = createContext<ModalsContextValue | null>(null)

/** The shared modals interface. Throws if used outside ``<ModalsProvider>``. */
export function useModals(): ModalsContextValue {
  const ctx = useContext(ModalsContext)
  if (!ctx) throw new Error('useModals must be used within <ModalsProvider>')
  return ctx
}

// Internal state — one queued modal at a time. A second call while one is open replaces it
// (the first promise resolves to its cancel value before the new modal renders).
type Queued =
  | { kind: 'confirm'; opts: ConfirmOptions; resolve: (v: boolean) => void }
  | { kind: 'prompt'; opts: PromptOptions; resolve: (v: string | null) => void }
  | { kind: 'alert'; opts: AlertOptions; resolve: () => void }
  | { kind: 'choose'; opts: ChooseOptions<string>; resolve: (v: string | null) => void }

export function ModalsProvider({ children }: { children: ReactNode }) {
  const { t } = useTranslation()
  const [queued, setQueued] = useState<Queued | null>(null)

  // The latest queued modal's resolver — kept in a ref so the API callbacks below (which the
  // provider exposes via context, so they're stable closures) can read it even after state
  // updates. ``open(kind, opts)`` returns a promise + sets state in one step.
  const queuedRef = useRef<Queued | null>(null)
  queuedRef.current = queued

  const close = useCallback((value: boolean | string | null | void) => {
    const cur = queuedRef.current
    if (!cur) return
    if (cur.kind === 'confirm') cur.resolve(Boolean(value))
    else if (cur.kind === 'prompt') cur.resolve(value as string | null)
    else if (cur.kind === 'choose') cur.resolve(value as string | null)
    else cur.resolve()
    setQueued(null)
  }, [])

  // Replace any in-flight modal with the new one. The displaced modal's promise resolves to its
  // cancel value (false / null / void) so the caller doesn't hang.
  const open = useCallback(<Q extends Queued>(next: Q): void => {
    const cur = queuedRef.current
    if (cur) {
      if (cur.kind === 'confirm') cur.resolve(false)
      else if (cur.kind === 'prompt') cur.resolve(null)
      else if (cur.kind === 'choose') cur.resolve(null)
      else cur.resolve()
    }
    setQueued(next)
  }, [])

  const api: ModalsContextValue = {
    confirm: (opts) => new Promise<boolean>((resolve) => {
      open({ kind: 'confirm', opts, resolve })
    }),
    prompt: (opts) => new Promise<string | null>((resolve) => {
      open({ kind: 'prompt', opts, resolve })
    }),
    alert: (opts) => new Promise<void>((resolve) => {
      open({ kind: 'alert', opts, resolve })
    }),
    choose: <V extends string>(opts: ChooseOptions<V>) => new Promise<V | null>((resolve) => {
      // Internal Queued kind erases the value-type parameter; the cast back to ``V`` at the
      // caller is safe because the only values that can land in ``close()`` are the ones the
      // operator clicks (each ``ChooseOption.value`` is V) or the ``cancelValue`` we forward
      // verbatim when Escape / overlay closes.
      const erased: ChooseOptions<string> = opts as ChooseOptions<string>
      open({
        kind: 'choose',
        opts: erased,
        resolve: (v) => resolve(v as V | null),
      })
    }),
  }

  return (
    <ModalsContext.Provider value={api}>
      {children}
      {queued?.kind === 'confirm' && (
        <ConfirmModalContent
          opts={queued.opts}
          onConfirm={() => close(true)}
          onCancel={() => close(false)}
        />
      )}
      {queued?.kind === 'prompt' && (
        <PromptModalContent
          opts={queued.opts}
          onSubmit={(v) => close(v)}
          onCancel={() => close(null)}
          t={t}
        />
      )}
      {queued?.kind === 'alert' && (
        <AlertModalContent
          opts={queued.opts}
          onClose={() => close(undefined)}
        />
      )}
      {queued?.kind === 'choose' && (
        <ChooseModalContent
          opts={queued.opts}
          onPick={(v) => close(v)}
          onCancel={() => close(queued.opts.cancelValue ?? null)}
        />
      )}
    </ModalsContext.Provider>
  )
}

function ConfirmModalContent({
  opts, onConfirm, onCancel,
}: {
  opts: ConfirmOptions; onConfirm: () => void; onCancel: () => void
}) {
  const { t } = useTranslation()
  // Escape closes (cancel), Enter confirms — same conventions browser confirm() has by default.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
      else if (e.key === 'Enter' && (e.target as HTMLElement | null)?.tagName !== 'BUTTON') onConfirm()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onConfirm, onCancel])
  return createPortal(
    <TopOverlay onClick={onCancel}>
      <Modal style={{ width: 440 }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{opts.title}</ModalHeader>
        <ModalBody>{opts.message}</ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel}>
            {opts.cancelLabel ?? t('common.cancel')}
          </Button>
          <Button $size="sm" $variant={opts.variant ?? 'primary'} onClick={onConfirm} autoFocus>
            {opts.confirmLabel ?? t('common.confirm')}
          </Button>
        </ModalFooter>
      </Modal>
    </TopOverlay>,
    document.body,
  )
}

function PromptModalContent({
  opts, onSubmit, onCancel, t,
}: {
  opts: PromptOptions
  onSubmit: (value: string) => void
  onCancel: () => void
  t: ReturnType<typeof useTranslation>['t']
}) {
  const [value, setValue] = useState<string>(opts.defaultValue ?? '')
  const [error, setError] = useState<string | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  useEffect(() => {
    // Auto-focus + select-all so a rename flow lets the user type a fresh name without first
    // clearing the old one (window.prompt did this implicitly).
    inputRef.current?.focus()
    inputRef.current?.select()
  }, [])
  const submit = useCallback(() => {
    const trimmed = value.trim()
    if (!opts.allowEmpty && trimmed === '') {
      setError(t('settings.rename.empty', 'Name cannot be empty.'))
      return
    }
    if (opts.validate) {
      const err = opts.validate(trimmed)
      if (err) { setError(err); return }
    }
    onSubmit(trimmed)
  }, [value, opts, onSubmit, t])
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') onCancel()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])
  return createPortal(
    <TopOverlay onClick={onCancel}>
      <Modal style={{ width: 460 }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{opts.title}</ModalHeader>
        <ModalBody>
          {opts.message != null && <div>{opts.message}</div>}
          <Input
            ref={inputRef}
            value={value}
            placeholder={opts.placeholder}
            onChange={(e) => { setValue(e.target.value); if (error) setError(null) }}
            onKeyDown={(e) => { if (e.key === 'Enter') { e.preventDefault(); submit() } }}
          />
          {error && (
            <div style={{ color: 'var(--text-error, #c0392b)', fontSize: 12 }}>{error}</div>
          )}
        </ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant="ghost" onClick={onCancel}>
            {opts.cancelLabel ?? t('common.cancel')}
          </Button>
          <Button $size="sm" $variant="primary" onClick={submit}>
            {opts.submitLabel ?? t('common.ok', 'OK')}
          </Button>
        </ModalFooter>
      </Modal>
    </TopOverlay>,
    document.body,
  )
}

function AlertModalContent({
  opts, onClose,
}: {
  opts: AlertOptions; onClose: () => void
}) {
  const { t } = useTranslation()
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape' || e.key === 'Enter') onClose()
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onClose])
  return createPortal(
    <TopOverlay onClick={onClose}>
      <Modal style={{ width: 440 }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{opts.title}</ModalHeader>
        <ModalBody>{opts.message}</ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant={opts.variant ?? 'primary'} onClick={onClose} autoFocus>
            {opts.okLabel ?? t('common.ok', 'OK')}
          </Button>
        </ModalFooter>
      </Modal>
    </TopOverlay>,
    document.body,
  )
}

function ChooseModalContent({
  opts, onPick, onCancel,
}: {
  opts: ChooseOptions<string>
  onPick: (value: string) => void
  onCancel: () => void
}) {
  // Escape = cancel (resolves to ``cancelValue ?? null`` upstream — the "keep editing" path
  // for an unsaved-changes prompt). Enter is intentionally NOT bound to any choice — the
  // operator must explicitly click one of the buttons, which avoids accidentally discarding
  // changes by hitting Enter when their focus is somewhere else.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === 'Escape') onCancel() }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [onCancel])
  return createPortal(
    <TopOverlay onClick={onCancel}>
      <Modal style={{ width: 480 }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{opts.title}</ModalHeader>
        <ModalBody>{opts.message}</ModalBody>
        <ModalFooter>
          {opts.cancelLabel && (
            <Button $size="sm" $variant="ghost" onClick={onCancel}>
              {opts.cancelLabel}
            </Button>
          )}
          {opts.options.map((opt) => (
            <Button
              key={opt.value}
              $size="sm"
              $variant={opt.variant ?? 'ghost'}
              onClick={() => onPick(opt.value)}
              autoFocus={opt.autoFocus}
            >
              {opt.label}
            </Button>
          ))}
        </ModalFooter>
      </Modal>
    </TopOverlay>,
    document.body,
  )
}
