// A themed file picker: a button that opens the native dialog, the chosen name next to it, and a
// clear action. The native <input type="file"> stays in the DOM (hidden) so the browser's dialog
// and keyboard access work; only the visible part is ours.
import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { FileUp, X } from 'lucide-react'
import { Button } from './Button'
import { colors, fontSize, fonts } from '../theme'

const Wrap = styled.div`
  display: flex; align-items: center; gap: 8px; min-width: 0;
  & input { position: absolute; width: 1px; height: 1px; opacity: 0; overflow: hidden; pointer-events: none; }
  & .name { flex: 1; min-width: 0; font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .none { color: ${colors.text.muted}; font-family: ${fonts.sans}; }
  & .clear { border: 0; background: transparent; color: ${colors.text.muted}; cursor: pointer; display: inline-flex; padding: 2px; }
  & .clear:hover { color: ${colors.red.main}; }
`

export function FileInput({ value, onChange, accept, disabled, placeholder }: {
  value: File | null
  onChange: (file: File | null) => void
  accept?: string
  disabled?: boolean
  placeholder?: string
}) {
  const { t } = useTranslation()
  const ref = useRef<HTMLInputElement>(null)
  return (
    <Wrap>
      <input ref={ref} type="file" accept={accept} disabled={disabled} tabIndex={-1}
        onChange={(e) => { onChange(e.target.files?.[0] ?? null); e.target.value = '' }} />
      <Button type="button" $size="sm" $variant="ghost" disabled={disabled} onClick={() => ref.current?.click()}>
        <FileUp size={13} /> {value ? t('common.changeFile', 'Change…') : t('common.chooseFile', 'Choose a file…')}
      </Button>
      <span className={value ? 'name' : 'name none'} title={value?.name}>{value ? value.name : (placeholder ?? t('common.noFile', 'No file chosen'))}</span>
      {value && !disabled && (
        <button type="button" className="clear" title={t('common.clear', 'Clear')} onClick={() => onChange(null)}><X size={13} /></button>
      )}
    </Wrap>
  )
}
