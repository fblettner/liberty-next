// Liberty Next's overview — for whoever arrives: how an application is built (the path, step by step, with
// what this installation already holds of each step and the way in), the applications with what is loose in
// each, and how to start. Every step opens where the work is done; an application opens in the Studio.
import { lazy, Suspense, useEffect, useState, type ReactNode } from 'react'
import { useNavigate } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import {
  AlertTriangle, ArrowRight, BarChart3, BookOpen, Database, FlaskConical, Folder, LayoutGrid, Package, ShieldAlert, ShieldCheck, Users, Wand2, Workflow,
} from 'lucide-react'
import { Banner, Button, Centered } from '../../common'
import { api, ApiError } from '../../api/client'
import { colors, fontSize, fonts, radius, media } from '../../theme'

const ScreenAssistantModal = lazy(() => import('../ScreenAssistant'))

type Tone = 'blue' | 'green' | 'orange' | 'purple' | 'yellow'
interface AppLine { app: string; screens: number; forms: number; menu: number; data: number; services: number; dashboards: number; unreached: number; unused: number; missing: number }
interface Overview { counts: Record<string, number>; integrity: { errors: number; warnings: number }; apps: AppLine[]; version: string | null
  tests: { cases: number; passed: number; failed: number; changed: number; never: number } | null }

const Wrap = styled.div`display: flex; flex-direction: column; gap: 26px; max-width: 1500px;`
const Lead = styled.div`color: ${colors.text.secondary}; font-size: ${fontSize.md}; line-height: 1.5; max-width: 880px;`
const Title = styled.div`
  display: flex; align-items: baseline; gap: 10px; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary};
  & small { font-weight: 400; color: ${colors.text.muted}; font-size: ${fontSize.sm}; }
`
const Path = styled.div`
  display: grid; grid-template-columns: repeat(4, minmax(0, 1fr)); gap: 14px;
  ${media.narrow} { grid-template-columns: repeat(2, minmax(0, 1fr)); }
  ${media.tablet} { grid-template-columns: minmax(0, 1fr); }
`
const Step = styled.button<{ $tone: Tone }>`
  position: relative; display: flex; flex-direction: column; gap: 8px; text-align: left; cursor: pointer; padding: 14px; font-family: ${fonts.sans};
  border-radius: ${radius.lg}; border: 1px solid ${colors.border}; background: linear-gradient(180deg, ${(p) => colors[p.$tone].bg}, transparent 75%);
  transition: border-color 0.15s, transform 0.15s;
  &:hover { border-color: ${(p) => colors[p.$tone].border}; transform: translateY(-1px); }
  &:hover .go { opacity: 1; }
  & .top { display: flex; align-items: center; gap: 10px; }
  & .n { font-family: ${fonts.mono}; font-size: ${fontSize.sm}; font-weight: 600; color: ${(p) => colors[p.$tone].main}; }
  & .chip { display: inline-flex; align-items: center; justify-content: center; width: 32px; height: 32px; border-radius: ${radius.md};
    color: ${(p) => colors[p.$tone].main}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border}; }
  & .t { font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; }
  & .what { color: ${colors.text.muted}; font-size: ${fontSize.sm}; line-height: 1.45; min-height: 4.3em; }
  & .have { font-size: ${fontSize.sm}; color: ${colors.text.secondary}; font-variant-numeric: tabular-nums; }
  & .go { position: absolute; top: 14px; right: 14px; opacity: 0; color: ${colors.text.muted}; transition: opacity 0.15s; }
`
const Apps = styled.div`display: grid; grid-template-columns: repeat(auto-fill, minmax(300px, 1fr)); gap: 14px;`
const App = styled.button`
  display: flex; flex-direction: column; gap: 10px; text-align: left; cursor: pointer; padding: 14px 16px; font-family: ${fonts.sans};
  border-radius: ${radius.lg}; border: 1px solid ${colors.border}; background: var(--bg-modal); transition: border-color 0.15s, transform 0.15s;
  &:hover { border-color: ${colors.blue.border}; transform: translateY(-1px); }
  & .name { display: flex; align-items: center; gap: 8px; font-size: ${fontSize.md}; font-weight: 600; color: ${colors.text.primary}; font-family: ${fonts.mono}; }
  & .name span { margin-left: auto; display: inline-flex; align-items: center; gap: 4px; font-family: ${fonts.sans}; font-weight: 400; font-size: ${fontSize.sm}; color: ${colors.blue.main}; }
  & .figures { display: grid; grid-template-columns: repeat(4, 1fr); gap: 6px; }
  & .fig { display: flex; flex-direction: column; }
  & .fig b { font-size: ${fontSize.lg}; font-weight: 600; color: ${colors.text.primary}; font-variant-numeric: tabular-nums; }
  & .fig i { font-style: normal; font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
  & .loose { display: flex; align-items: center; gap: 6px; font-size: ${fontSize.sm}; color: ${colors.orange.main}; }
  & .fine { display: flex; align-items: center; gap: 6px; font-size: ${fontSize.sm}; color: ${colors.green.main}; }
`
const Row = styled.div`display: flex; flex-wrap: wrap; gap: 10px; align-items: center;`
const State = styled.button<{ $tone: 'green' | 'orange' | 'red' }>`
  display: inline-flex; align-items: center; gap: 8px; padding: 8px 14px; border-radius: 999px; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  color: ${(p) => colors[p.$tone].main}; background: ${(p) => colors[p.$tone].bg}; border: 1px solid ${(p) => colors[p.$tone].border};
`

export default function LibertyOverview() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [data, setData] = useState<Overview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [assistant, setAssistant] = useState(false)
  useEffect(() => {
    api.get<Overview>('/admin/studio/overview').then(setData).catch((e) => setError(e instanceof ApiError ? e.message : String(e)))
  }, [])
  if (error) return <Banner $tone="error">{error}</Banner>
  if (!data) return <Centered />
  const c = data.counts
  const step = (n: number, tone: Tone, icon: ReactNode, key: string, to: string, have: string) => (
    <Step type="button" $tone={tone} onClick={() => navigate(to)}>
      <div className="top"><span className="chip">{icon}</span><span className="n">{n}</span><span className="t">{t(`overview.${key}`)}</span></div>
      <div className="what">{t(`overview.${key}What`)}</div>
      <div className="have">{have}</div>
      <ArrowRight className="go" size={15} />
    </Step>
  )
  const issues = data.integrity.errors + data.integrity.warnings
  return (
    <Wrap>
      <Lead>{t('overview.lead')}</Lead>
      <Row>
        <Button $variant="primary" onClick={() => navigate('/liberty/studio')}><Workflow size={15} /> {t('overview.openStudio')}</Button>
        <Button $variant="ghost" onClick={() => setAssistant(true)}><Wand2 size={15} /> {t('overview.newScreen')}</Button>
        <span style={{ flex: 1 }} />
        <State type="button" $tone={data.integrity.errors ? 'red' : issues ? 'orange' : 'green'} onClick={() => navigate('/liberty/integrity')}>
          {issues ? <ShieldAlert size={15} /> : <ShieldCheck size={15} />}
          {issues ? t('studio.checkFound', { errors: data.integrity.errors, warnings: data.integrity.warnings }) : t('studio.checkClean')}
        </State>
        {data.tests && data.tests.cases > 0 && (
          <State type="button" $tone={data.tests.failed ? 'red' : data.tests.changed ? 'orange' : 'green'} onClick={() => navigate('/liberty/test-results')}>
            <FlaskConical size={15} /> {t('overview.tests', data.tests)}
          </State>
        )}
        {data.version && <span style={{ color: colors.text.muted, fontSize: fontSize.sm, fontFamily: fonts.mono }}>v{data.version}</span>}
      </Row>

      <Title>{t('overview.apps')}<small>{t('overview.appsHint')}</small></Title>
      <Apps>
        {data.apps.map((a) => {
          const loose = a.unreached + a.missing
          return (
            <App key={a.app} type="button" onClick={() => navigate(`/liberty/studio?app=${encodeURIComponent(a.app)}`)}>
              <div className="name">{a.app}<span>{t('overview.inStudio')} <ArrowRight size={13} /></span></div>
              <div className="figures">
                <span className="fig"><b>{a.screens}</b><i>{t('studio.screens')}</i></span>
                <span className="fig"><b>{a.menu}</b><i>{t('overview.menuEntries')}</i></span>
                <span className="fig"><b>{a.data}</b><i>{t('studio.data')}</i></span>
                <span className="fig"><b>{a.services}</b><i>{t('studio.services')}</i></span>
              </div>
              {loose > 0
                ? <span className="loose"><AlertTriangle size={14} /> {[a.unreached ? t('studio.unreachedCount', { n: a.unreached }) : '', a.missing ? t('overview.missing', { n: a.missing }) : ''].filter(Boolean).join(' · ')}</span>
                : <span className="fine"><ShieldCheck size={14} /> {t('overview.appFine')}</span>}
            </App>
          )
        })}
        {data.apps.length === 0 && <Lead>{t('studio.noApp')}</Lead>}
      </Apps>

      <Title>{t('overview.path')}<small>{t('overview.pathHint')}</small></Title>
      <Path>
        {step(1, 'blue', <Database size={17} />, 'connect', '/liberty/connectors', t('overview.connectHave', { n: c.connectors, queries: c.queries, endpoints: c.endpoints }))}
        {step(2, 'blue', <BookOpen size={17} />, 'name', '/liberty/dictionary', t('overview.nameHave', { n: c.dictionary }))}
        {step(3, 'green', <LayoutGrid size={17} />, 'screens', '/liberty/studio', t('overview.screensHave', { n: c.screens, forms: c.forms }))}
        {step(4, 'green', <Folder size={17} />, 'menu', '/liberty/menus', t('overview.menuHave', { n: c.menu_items }))}
        {step(5, 'purple', <Workflow size={17} />, 'logic', '/liberty/actions', t('overview.logicHave', { services: c.services, jobs: c.jobs }))}
        {step(6, 'orange', <BarChart3 size={17} />, 'insight', '/liberty/dashboards', t('overview.insightHave', { dashboards: c.dashboards, models: c.explorer }))}
        {step(7, 'yellow', <Users size={17} />, 'access', '/liberty/access', t('overview.accessHave'))}
        {step(8, 'yellow', <Package size={17} />, 'deliver', '/liberty/package', t('overview.deliverHave'))}
      </Path>
      {assistant && <Suspense fallback={null}><ScreenAssistantModal onClose={() => setAssistant(false)} /></Suspense>}
    </Wrap>
  )
}
