// Settings page = a tab switcher over the config editors: a structured builder per config section
// (Pools first — Phase 7) and the raw `connectors.toml` Monaco editor as the escape hatch.
import { lazy, Suspense, useState } from 'react'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { SlidersHorizontal } from 'lucide-react'
import { PageLayout, Centered } from '../../common'
import { colors, fontSize, fonts, radius } from '../../theme'

const PoolsBuilder = lazy(() => import('./PoolsBuilder'))
const ConnectorsBuilder = lazy(() => import('./ConnectorsBuilder'))
const RawEditor = lazy(() => import('./RawEditor'))

type Tab = 'pools' | 'connectors' | 'raw'

const Tabs = styled.div`display: flex; gap: 4px; margin-bottom: 14px;`
const TabBtn = styled.button<{ $active?: boolean }>`
  height: 30px; padding: 0 12px; border-radius: ${radius.md}; cursor: pointer;
  border: 1px solid ${({ $active }) => ($active ? colors.blue.border : colors.border)};
  background: ${({ $active }) => ($active ? colors.blue.bg : colors.bg.input)};
  color: ${({ $active }) => ($active ? colors.blue.main : colors.text.secondary)};
  font-size: ${fontSize.sm}; font-family: ${fonts.sans};
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`

export default function Settings() {
  const { t } = useTranslation()
  const [tab, setTab] = useState<Tab>('pools')
  return (
    <PageLayout icon={<SlidersHorizontal size={18} />} title={t('settings.title')}>
      <Tabs>
        <TabBtn $active={tab === 'pools'} onClick={() => setTab('pools')}>{t('settings.tabs.pools')}</TabBtn>
        <TabBtn $active={tab === 'connectors'} onClick={() => setTab('connectors')}>{t('settings.tabs.connectors')}</TabBtn>
        <TabBtn $active={tab === 'raw'} onClick={() => setTab('raw')}>{t('settings.tabs.raw')}</TabBtn>
      </Tabs>
      <Suspense fallback={<Centered />}>
        {tab === 'pools' ? <PoolsBuilder /> : tab === 'connectors' ? <ConnectorsBuilder /> : <RawEditor />}
      </Suspense>
    </PageLayout>
  )
}
