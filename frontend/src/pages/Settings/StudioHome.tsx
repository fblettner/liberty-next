// The Studio — the home of whoever builds an application: what the installation is made of, in the order
// an application is built (its data, what the users see, what runs, what is analysed), with how much of each
// exists, what the integrity check finds, the last changes, and the tools around. Every tile opens its editor.
import { useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import {
  ArrowRight, BarChart3, Bell, BookOpen, Clock, Code2, Compass, Database, Folder, GitCompare, Grid3x3, History, LayoutDashboard,
  Package, Activity, ShieldAlert, ShieldCheck, Tag, Workflow,
} from 'lucide-react'
import { Banner, Centered } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius, media } from '../../theme'

type Tone = 'blue' | 'green' | 'orange' | 'purple' | 'yellow' | 'red'
interface Overview {
  counts: Record<string, number>
  integrity: { errors: number; warnings: number }
  recent: { file: string; who: string | null; when: string; source: string | null }[]
}

const Wrap = styled.div`display: flex; flex-direction: column; gap: 22px; max-width: 1500px;`
const Lead = styled.div`color: ${colors.text.secondary}; font-size: ${fontSize.md}; line-height: 1.5; max-width: 900px;`
const Stages = styled.div`
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 16px; align-items: start;
  ${media.narrow} { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  ${media.tablet} { grid-template-columns: minmax(0, 1fr); }
`
const Stage = styled.div<{ $tone: Tone }>`
  display: flex; flex-direction: column; gap: 10px; padding: 14px; border-radius: ${radius.lg};
  border: 1px solid ${colors.border}; background: linear-gradient(180deg, ${(p) => colors[p.$tone].bg}, transparent 70%);
  & > .head { display: flex; align-items: baseline; gap: 8px; }
  & > .head .n { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; color: ${(p) => colors[p.$tone].main}; font-weight: 600; }
  & > .head .t { font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; }
  & > .what { color: ${colors.text.muted}; font-size: ${fontSize.sm}; line-height: 1.4; min-height: 2.8em; }
`
const Tile = styled.button<{ $tone: Tone }>`
  display: flex; align-items: center; gap: 12px; width: 100%; text-align: left; cursor: pointer; padding: 11px 12px;
  background: var(--bg-modal); border: 1px solid ${colors.border}; border-radius: ${radius.md}; font-family: ${fonts.sans};
  transition: border-color 0.15s, transform 0.15s;
  &:hover { border-color: ${(p) => colors[p.$tone].border}; transform: translateY(-1px); }
  &:hover .go { opacity: 1; }
  & .chip { flex: 0 0 auto; display: inline-flex; align-items: center; justify-content: center; width: 36px; height: 36px; border-radius: ${radius.md};
    color: ${(p) => colors[p.$tone].main}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border}; }
  & .text { flex: 1 1 auto; min-width: 0; display: flex; flex-direction: column; gap: 1px; }
  & .name { font-size: ${fontSize.sm}; font-weight: 600; color: ${colors.text.primary}; }
  & .meta { font-size: ${fontSize.micro}; color: ${colors.text.muted}; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .count { flex: 0 0 auto; font-size: ${fontSize.lg}; font-weight: 600; color: ${colors.text.primary}; font-variant-numeric: tabular-nums; }
  & .go { flex: 0 0 auto; opacity: 0; color: ${colors.text.muted}; transition: opacity 0.15s; }
`
const Two = styled.div`display: grid; grid-template-columns: minmax(0, 1fr) minmax(0, 1.4fr); gap: 16px; ${media.tablet} { grid-template-columns: minmax(0, 1fr); }`
const Panel = styled.div`
  display: flex; flex-direction: column; gap: 10px; padding: 14px 16px; border: 1px solid ${colors.border}; border-radius: ${radius.lg};
  & > .t { font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.secondary}; }
`
const Health = styled.button<{ $tone: Tone }>`
  display: flex; align-items: center; gap: 12px; padding: 12px; cursor: pointer; text-align: left; font-family: ${fonts.sans};
  border-radius: ${radius.md}; border: 1px solid ${(p) => colors[p.$tone].border}; background: ${(p) => colors[p.$tone].bg}; color: ${colors.text.primary};
  & .big { font-size: ${fontSize.lg}; font-weight: 600; color: ${(p) => colors[p.$tone].main}; }
  & small { display: block; color: ${colors.text.muted}; font-size: ${fontSize.sm}; }
`
const Change = styled.div`
  display: flex; align-items: baseline; gap: 10px; font-size: ${fontSize.sm}; color: ${colors.text.secondary};
  & .f { font-family: ${fonts.mono}; color: ${colors.text.primary}; }
  & .w { margin-left: auto; color: ${colors.text.muted}; font-size: ${fontSize.micro}; white-space: nowrap; }
`
const Tools = styled.div`display: flex; flex-wrap: wrap; gap: 8px;`
const Tool = styled.button`
  display: inline-flex; align-items: center; gap: 8px; padding: 8px 12px; cursor: pointer; font-family: ${fonts.sans};
  border: 1px solid ${colors.border}; border-radius: 999px; background: transparent; color: ${colors.text.secondary}; font-size: ${fontSize.sm};
  &:hover { color: ${colors.text.primary}; border-color: ${colors.blue.border}; background: ${colors.blue.bg}; }
`

function when(iso: string): string {
  const d = new Date(iso)
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' })
}

export default function StudioHome() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    api.get<Overview>('/admin/studio/overview').then(setData).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  if (error) return <Banner $tone="error">{error}</Banner>
  if (!data) return <Centered />
  const c = data.counts
  const tile = (tone: Tone, icon: ReactNode, key: string, to: string, count: number, meta: string) => (
    <Tile type="button" $tone={tone} onClick={() => navigate(to)}>
      <span className="chip">{icon}</span>
      <span className="text"><span className="name">{t(`studio.${key}`)}</span><span className="meta">{meta}</span></span>
      <span className="count">{count}</span>
      <ArrowRight className="go" size={15} />
    </Tile>
  )
  const stage = (n: number, tone: Tone, key: string, tiles: ReactNode) => (
    <Stage $tone={tone}>
      <div className="head"><span className="n">{n}</span><span className="t">{t(`studio.stage.${key}`)}</span></div>
      <div className="what">{t(`studio.stage.${key}What`)}</div>
      {tiles}
    </Stage>
  )
  const issues = data.integrity.errors + data.integrity.warnings
  return (
    <Wrap>
      <Lead>{t('studio.lead')}</Lead>
      <Stages>
        {stage(1, 'blue', 'data', <>
          {tile('blue', <Database size={18} />, 'connectors', '/liberty/connectors', c.connectors, t('studio.connectorsMeta', { queries: c.queries, endpoints: c.endpoints }))}
          {tile('blue', <BookOpen size={18} />, 'dictionary', '/liberty/dictionary', c.dictionary, t('studio.dictionaryMeta'))}
        </>)}
        {stage(2, 'green', 'screens', <>
          {tile('green', <Grid3x3 size={18} />, 'screensTile', '/liberty/screens', c.screens, t('studio.screensMeta', { forms: c.forms }))}
          {tile('green', <Folder size={18} />, 'menus', '/liberty/menus', c.menu_items, t('studio.menusMeta', { apps: c.menus }))}
        </>)}
        {stage(3, 'purple', 'logic', <>
          {tile('purple', <Workflow size={18} />, 'services', '/liberty/actions', c.services, t('studio.servicesMeta', { exposed: c.exposed }))}
          {tile('purple', <Clock size={18} />, 'jobs', '/nomaflow', c.jobs, t('studio.jobsMeta'))}
          {tile('purple', <Bell size={18} />, 'notifications', '/liberty/notifications', c.notifications, t('studio.notificationsMeta'))}
        </>)}
        {stage(4, 'orange', 'insight', <>
          {tile('orange', <LayoutDashboard size={18} />, 'dashboards', '/liberty/dashboards', c.dashboards, t('studio.dashboardsMeta'))}
          {tile('orange', <BarChart3 size={18} />, 'charts', '/liberty/charts', c.charts, t('studio.chartsMeta'))}
          {tile('orange', <Compass size={18} />, 'explorer', '/liberty/explorer', c.explorer, t('studio.explorerMeta'))}
        </>)}
      </Stages>
      <Two>
        <Panel>
          <span className="t">{t('studio.check')}</span>
          <Health type="button" $tone={data.integrity.errors ? 'red' : issues ? 'orange' : 'green'} onClick={() => navigate('/liberty/integrity')}>
            {issues ? <ShieldAlert size={22} /> : <ShieldCheck size={22} />}
            <span>
              <span className="big">{issues ? t('studio.checkFound', { errors: data.integrity.errors, warnings: data.integrity.warnings }) : t('studio.checkClean')}</span>
              <small>{t('studio.checkHint')}</small>
            </span>
          </Health>
        </Panel>
        <Panel>
          <span className="t">{t('studio.recent')}</span>
          {data.recent.length === 0 && <Change>{t('studio.recentNone')}</Change>}
          {data.recent.map((r, i) => (
            <Change key={i}><span className="f">{r.file}</span>{r.who && <span>{r.who}</span>}<span className="w">{when(r.when)}</span></Change>
          ))}
        </Panel>
      </Two>
      <Panel>
        <span className="t">{t('studio.tools')}</span>
        <Tools>
          <Tool type="button" onClick={() => navigate('/liberty/integrity')}><ShieldCheck size={15} /> {t('settings.tabs.integrity')}</Tool>
          <Tool type="button" onClick={() => navigate('/liberty/versions')}><History size={15} /> {t('settings.tabs.versions')}</Tool>
          <Tool type="button" onClick={() => navigate('/liberty/package')}><Package size={15} /> {t('settings.tabs.package')}</Tool>
          <Tool type="button" onClick={() => navigate('/liberty/changes')}><GitCompare size={15} /> {t('settings.tabs.changes')}</Tool>
          <Tool type="button" onClick={() => navigate('/monitoring')}><Activity size={15} /> {t('nav.monitoring')}</Tool>
          <Tool type="button" onClick={() => navigate('/developer')}><Code2 size={15} /> {t('developer.title')}</Tool>
          <Tool type="button" onClick={() => navigate('/liberty/release-notes')}><Tag size={15} /> {t('settings.tabs.releasenotes')}</Tool>
        </Tools>
      </Panel>
    </Wrap>
  )
}
