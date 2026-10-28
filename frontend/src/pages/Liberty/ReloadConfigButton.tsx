// Deterministic "apply every TOML now": the editors save their file but don't re-read it into the
// running server (the opt-in file watcher is partial), so POST /admin/reload re-reads every config,
// rebuilds the connector registry and swaps app.state. In the header of every configuration editor.
import { useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { RefreshCw } from 'lucide-react'
import { Button } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts } from '../../theme'

const Wrap = styled.div`display: flex; align-items: center; gap: 10px;`
const Msg = styled.span<{ $tone: 'ok' | 'err' }>`
  font-family: ${fonts.sans}; font-size: ${fontSize.sm}; max-width: 360px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap;
  color: ${({ $tone }) => ($tone === 'ok' ? colors.green.main : colors.red.main)};
`

export default function ReloadConfigButton() {
  const { t } = useTranslation()
  const [reloading, setReloading] = useState(false)
  const [msg, setMsg] = useState<{ tone: 'ok' | 'err'; text: string } | null>(null)
  const reloadAll = async () => {
    setReloading(true); setMsg(null)
    try {
      const r = await api.post<{ connectors: string[]; dictionary_entries: number; screen_apps: string[] }>('/admin/reload')
      setMsg({ tone: 'ok', text: t('settings.reloadDone', { c: r.connectors.length, d: r.dictionary_entries, s: r.screen_apps.length }) })
    } catch (e) {
      setMsg({ tone: 'err', text: e instanceof ApiError ? e.message : String(e) })
    } finally {
      setReloading(false)
      window.setTimeout(() => setMsg(null), 6000)
    }
  }
  return (
    <Wrap>
      {msg && <Msg $tone={msg.tone} title={msg.text}>{msg.text}</Msg>}
      <Button $size="sm" $variant="ghost" onClick={reloadAll} disabled={reloading} title={t('settings.reloadHint')}>
        <RefreshCw size={13} /> {reloading ? t('settings.reloading') : t('settings.reloadConfig')}
      </Button>
    </Wrap>
  )
}
