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
import { useTranslation } from 'react-i18next'
import { Modal, ModalBody, ModalFooter, ModalHeader, Overlay } from './Modal'
import { Button } from './Button'
import { Input } from './Input'

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

export interface ModalsContextValue {
  confirm: (opts: ConfirmOptions) => Promise<boolean>
  prompt: (opts: PromptOptions) => Promise<string | null>
  alert: (opts: AlertOptions) => Promise<void>
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
  return (
    <Overlay onClick={onCancel}>
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
    </Overlay>
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
  return (
    <Overlay onClick={onCancel}>
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
    </Overlay>
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
  return (
    <Overlay onClick={onClose}>
      <Modal style={{ width: 440 }} onClick={(e) => e.stopPropagation()}>
        <ModalHeader>{opts.title}</ModalHeader>
        <ModalBody>{opts.message}</ModalBody>
        <ModalFooter>
          <Button $size="sm" $variant={opts.variant ?? 'primary'} onClick={onClose} autoFocus>
            {opts.okLabel ?? t('common.ok', 'OK')}
          </Button>
        </ModalFooter>
      </Modal>
    </Overlay>
  )
}
