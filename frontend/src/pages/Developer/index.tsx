// Developer reference (C12) — what the running code is made of, as documentation (nothing is edited
// here): Configuration (every app.toml key), API (every route and the access it requires) — read from
// the running code, so they can't drift from it. Modelled on nomaubl's Developer reference page.
import { lazy, Suspense, useState } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Code2 } from 'lucide-react'
import { Centered, PageLayout } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

const ConfigView = lazy(() => import('./ConfigView'))
const ApiView = lazy(() => import('./ApiView'))
const TAB_KEY = 'liberty.developer.tab'
type Tab = 'config' | 'api'

const Tabs = styled.div`display: inline-flex; gap: 2px; padding: 3px; border: 1px solid ${colors.border}; border-radius: ${radius.lg}; background: ${colors.bg.input};`
const TabBtn = styled.button<{ $on: boolean }>`
  padding: 5px 14px; border: none; border-radius: ${radius.md}; cursor: pointer; font-family: ${fonts.sans}; font-size: ${fontSize.sm};
  background: ${({ $on }) => ($on ? colors.blue.bg : 'transparent')}; color: ${({ $on }) => ($on ? colors.blue.main : colors.text.secondary)};
  &:hover { color: ${colors.text.primary}; }
`

export default function DeveloperPage() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>(() => { try { return localStorage.getItem(TAB_KEY) === 'api' ? 'api' : 'config' } catch { return 'config' } })
  const pick = (v: Tab) => { setTab(v); try { localStorage.setItem(TAB_KEY, v) } catch { /* ignore */ } }
  return (
    <PageLayout icon={<Code2 size={18} />} title={t('developer.title')}
      description={tab === 'api' ? t('developer.apiIntro') : t('developer.configIntro')}
      headerRight={(
        <Tabs role="tablist">
          <TabBtn type="button" role="tab" aria-selected={tab === 'config'} $on={tab === 'config'} onClick={() => pick('config')}>{t('developer.configTitle')}</TabBtn>
          <TabBtn type="button" role="tab" aria-selected={tab === 'api'} $on={tab === 'api'} onClick={() => pick('api')}>{t('developer.apiTitle')}</TabBtn>
        </Tabs>
      )}>
      <Suspense fallback={<Centered />}>{tab === 'api' ? <ApiView /> : <ConfigView />}</Suspense>
    </PageLayout>
  )
}
