// The screen designer's head: the screen as a flow of six stages — what it is → its data → its grid → its record
// form → its actions → what it gives out — each card saying, from the screen's own configuration, what the
// stage holds (and what is missing). A click opens the stage; one with several sections shows them under it.
// The same picture as an endpoint's stages and a service's flow.
import { Fragment, type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Database, Download, FileInput, Table, Tag, Workflow } from 'lucide-react'
import { colors, fontSize, fonts, radius } from '../../theme'
import { SCREEN_STAGES, type ScreenStageId, type ScreenTab, screenFigures, stageOf } from '../../services/screenStages'

type Tone = 'blue' | 'green' | 'purple' | 'orange'
const LOOK: Record<ScreenStageId, { tone: Tone; icon: ReactNode }> = {
  general: { tone: 'blue', icon: <Tag size={15} /> }, data: { tone: 'blue', icon: <Database size={15} /> },
  grid: { tone: 'green', icon: <Table size={15} /> }, form: { tone: 'green', icon: <FileInput size={15} /> },
  actions: { tone: 'purple', icon: <Workflow size={15} /> }, output: { tone: 'orange', icon: <Download size={15} /> },
}

const Strip = styled.div`display: flex; align-items: stretch; overflow-x: auto; padding-bottom: 2px; margin-bottom: 12px; flex-shrink: 0;`
const Wire = styled.div`
  flex: 0 0 22px; align-self: center; height: 2px; background: ${colors.border}; position: relative;
  &::after { content: ''; position: absolute; right: 0; top: -3px; border-left: 6px solid ${colors.border}; border-top: 4px solid transparent; border-bottom: 4px solid transparent; }
`
const Card = styled.button<{ $tone: Tone; $on: boolean }>`
  flex: 1 1 0; min-width: 170px; text-align: left; cursor: pointer; padding: 9px 11px; display: flex; flex-direction: column; gap: 3px;
  font-family: ${fonts.sans}; background: ${(p) => (p.$on ? colors[p.$tone].bg : 'var(--bg-modal)')}; border-radius: ${radius.lg};
  border: 1px solid ${(p) => (p.$on ? colors[p.$tone].main : colors.border)}; transition: border-color 0.15s;
  &:hover { border-color: ${(p) => colors[p.$tone].border}; }
  & .kind { display: flex; align-items: center; gap: 6px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.04em; text-transform: uppercase; color: ${(p) => colors[p.$tone].main}; }
  & .chip { display: inline-flex; align-items: center; justify-content: center; width: 24px; height: 24px; border-radius: ${radius.md}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border}; }
  & .what { font-size: ${fontSize.sm}; color: ${colors.text.primary}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .more { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; min-height: 1.3em; }
  & .warn { color: ${colors.orange.main}; }
`
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
      <Strip>
        {SCREEN_STAGES.map((s, i) => (
          <Fragment key={s.id}>
            {i > 0 && <Wire />}
            <Card type="button" data-stage={s.id} $tone={LOOK[s.id].tone} $on={current.id === s.id} onClick={() => onTab(s.tabs.includes(tab) ? tab : s.tabs[0])}>
              <span className="kind"><span className="chip">{LOOK[s.id].icon}</span>{t(`settings.screens.stage.${s.id}`)}</span>
              <span className="what">{lines[s.id].what}</span>
              <span className={`more${lines[s.id].warn ? ' warn' : ''}`}>{lines[s.id].more}</span>
            </Card>
          </Fragment>
        ))}
      </Strip>
      {current.tabs.length > 1 && (
        <Sections>
          {current.tabs.map((k) => <button key={k} type="button" data-on={tab === k} onClick={() => onTab(k)}>{t(`settings.screens.editor.tabs.${k}`)}</button>)}
        </Sections>
      )}
    </>
  )
}
