// The screen designer's head: the screen as a flow of six stages — what it is → its data → its grid → its record
// form → its actions → what it gives out — each card saying, from the screen's own configuration, what the
// stage holds (and what is missing). A click opens the stage; one with several sections shows them under it.
// The same picture as an endpoint's stages and a service's flow.
import type { ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Database, Download, FileInput, Table, Tag, Workflow } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../../theme'
import StageStrip, { type StageTone } from './StageStrip'
import { SCREEN_STAGES, type ScreenStageId, type ScreenTab, screenFigures, stageOf } from '../../services/screenStages'

const LOOK: Record<ScreenStageId, { tone: StageTone; icon: ReactNode }> = {
  general: { tone: 'blue', icon: <Tag size={15} /> }, data: { tone: 'blue', icon: <Database size={15} /> },
  grid: { tone: 'green', icon: <Table size={15} /> }, form: { tone: 'green', icon: <FileInput size={15} /> },
  actions: { tone: 'purple', icon: <Workflow size={15} /> }, output: { tone: 'orange', icon: <Download size={15} /> },
}

const Sections = styled.div`
  display: inline-flex; align-self: flex-start; flex-shrink: 0; margin-bottom: 12px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden;
  & button { padding: 5px 13px; border: none; cursor: pointer; background: transparent; color: ${colors.text.muted}; font-size: ${fontSize.sm}; font-family: ${fonts.sans}; }
  & button[data-on='true'] { background: ${colors.blue.bg}; color: ${colors.blue.main}; }
`

export default function ScreenStages({ screen, id, tab, onTab }: {
  screen: Record<string, unknown>; id: string; tab: ScreenTab; onTab: (tab: ScreenTab) => void
}) {
  const { t } = useTranslation()
  const f = screenFigures(screen, id)
  const current = stageOf(tab)
  // what each stage says: the main line, then a second one — in orange when it tells something is missing
  const lines: Record<ScreenStageId, { what: string; more: string; warn?: boolean }> = {
    general: { what: f.label, more: f.readOnly ? t('settings.screens.stage.readOnly') : '' },
    data: { what: f.read || t('settings.screens.stage.noRead'), warn: !f.read || (!f.readOnly && f.saves === 0),
      more: f.saves ? t('settings.screens.stage.saves', { n: f.saves }) + (f.saveConnector ? ` · ${f.saveConnector}` : '') : t('settings.screens.stage.noSave') },
    grid: { what: t('settings.screens.stage.columns', { n: f.columns }),
      more: [f.filters ? t('settings.screens.stage.filters', { n: f.filters }) : '', f.views ? t('settings.screens.stage.views', { n: f.views }) : '', f.summary ? t('settings.screens.stage.summary') : ''].filter(Boolean).join(' · ') },
    form: { what: f.hasForm ? t('settings.screens.stage.tabs', { n: f.tabs }) : t('settings.screens.stage.noForm'), more: f.hasForm ? t('settings.screens.stage.fields', { n: f.fields }) : '' },
    actions: { what: t('settings.screens.stage.toolbar', { n: f.toolbar }), more: `${t('settings.screens.stage.rowMenu', { n: f.rowMenu })} · ${t('settings.screens.stage.hooks', { n: f.hooks })}` },
    output: { what: f.sheets ? t('settings.screens.stage.sheets', { n: f.sheets }) : t('settings.screens.stage.noExport'), more: '' },
  }
  return (
    <>
      <StageStrip
        stages={SCREEN_STAGES.map((st) => ({ id: st.id, ...LOOK[st.id], title: t(`settings.screens.stage.${st.id}`), ...lines[st.id] }))}
        current={current.id}
        onPick={(sid) => { const st = SCREEN_STAGES.find((x) => x.id === sid)!; onTab(st.tabs.includes(tab) ? tab : st.tabs[0]) }}
      />
      {current.tabs.length > 1 && (
        <Sections>
          {current.tabs.map((k) => <button key={k} type="button" data-on={tab === k} onClick={() => onTab(k)}>{t(`settings.screens.editor.tabs.${k}`)}</button>)}
        </Sections>
      )}
    </>
  )
}
