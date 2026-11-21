// The Theme editor's preview: a small piece of the application — its menu, a page with figures, a grid with a
// picked row, a field in focus, the buttons — drawn twice, dark and light, with the colours, the font and the size
// being edited. Each pane carries the theme's variables itself (`theme-dark` / `theme-light` + the draft's on top),
// so it shows the result whatever mode the person editing is in.
import type { CSSProperties } from 'react'
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { BarChart3, LayoutGrid, Users } from 'lucide-react'
import { Button } from '../../common'
import { Tag } from '../../common/Tag'
import { colors, fontSize, fonts, radius } from '../../theme'

const Panes = styled.div`display: grid; grid-template-columns: repeat(auto-fit, minmax(380px, 1fr)); gap: 14px;`
const Pane = styled.div`
  border: 1px solid ${colors.border}; border-radius: ${radius.lg}; overflow: hidden; background: ${colors.bg.base};
  color: ${colors.text.primary}; font-family: ${fonts.sans}; display: flex; flex-direction: column;
`
const Mode = styled.div`
  padding: 6px 12px; font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.06em; text-transform: uppercase;
  color: ${colors.text.muted}; border-bottom: 1px solid ${colors.border}; background: ${colors.bg.input};
`
const App = styled.div`display: flex; min-height: 300px;`
const Menu = styled.div`
  flex: 0 0 132px; padding: 12px 8px; border-right: 1px solid ${colors.border}; background: ${colors.bg.card}; display: flex; flex-direction: column; gap: 4px;
  & .brand { display: flex; align-items: center; gap: 7px; padding: 0 6px 10px; font-weight: 700; font-size: ${fontSize.base}; overflow: hidden; }
  & .brand span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  & .logo { flex: 0 0 18px; height: 18px; border-radius: ${radius.sm}; background: ${colors.blue.main}; }
`
const Item = styled.div<{ $on?: boolean }>`
  display: flex; align-items: center; gap: 7px; padding: 6px 8px; border-radius: ${radius.md}; font-size: ${fontSize.sm};
  color: ${(p) => (p.$on ? colors.blue.main : colors.text.secondary)}; background: ${(p) => (p.$on ? colors.blue.bg : 'transparent')};
  border: 1px solid ${(p) => (p.$on ? colors.blue.border : 'transparent')};
`
const Page = styled.div`flex: 1; min-width: 0; padding: 12px 14px; display: flex; flex-direction: column; gap: 10px;`
const Title = styled.div`font-size: ${fontSize.lg}; font-weight: 600;`
const Figures = styled.div`display: flex; gap: 8px;`
const Figure = styled.div`
  flex: 1; padding: 8px 10px; border: 1px solid ${colors.border}; border-radius: ${radius.md}; background: ${colors.bg.card};
  & .n { font-size: ${fontSize.xl}; font-weight: 700; color: ${colors.blue.main}; }
  & .l { font-size: ${fontSize.micro}; color: ${colors.text.muted}; }
`
const Grid = styled.div`border: 1px solid ${colors.border}; border-radius: ${radius.md}; overflow: hidden; font-size: ${fontSize.sm};`
const Line = styled.div<{ $head?: boolean; $on?: boolean }>`
  display: grid; grid-template-columns: 1fr 1fr 96px; white-space: nowrap; gap: 8px; align-items: center; padding: 5px 9px;
  border-top: ${(p) => (p.$head ? 'none' : `1px solid ${colors.border}`)};
  background: ${(p) => (p.$head ? colors.bg.input : p.$on ? colors.blue.bg : 'transparent')};
  color: ${(p) => (p.$head ? colors.text.muted : colors.text.primary)};
  font-size: ${(p) => (p.$head ? fontSize.micro : fontSize.sm)}; text-transform: ${(p) => (p.$head ? 'uppercase' : 'none')};
  letter-spacing: ${(p) => (p.$head ? '0.05em' : 'normal')};
`
const FieldLine = styled.div`
  display: flex; flex-direction: column; gap: 3px;
  & .l { font-size: ${fontSize.micro}; font-weight: 600; letter-spacing: 0.05em; text-transform: uppercase; color: ${colors.text.muted}; }
  & .i { padding: 6px 9px; border-radius: ${radius.md}; border: 1px solid ${colors.blue.main}; background: ${colors.bg.input}; box-shadow: var(--shadow-focus); font-size: ${fontSize.base}; }
`
const Foot = styled.div`display: flex; align-items: center; gap: 8px; flex-wrap: wrap;`
const Link = styled.span`color: ${colors.blue.main}; font-size: ${fontSize.sm}; text-decoration: underline;`

const cssVars = (vars: Record<string, string>): CSSProperties =>
  Object.fromEntries(Object.entries(vars).map(([k, v]) => [`--${k}`, v])) as CSSProperties

export default function ThemePreview({ appName, vars }: { appName: string; vars: Record<string, string> }) {
  const { t } = useTranslation()
  const rows: [string, string, 'green' | 'orange'][] = [['Atlas', 'Lyon', 'green'], ['Borea', 'Nantes', 'orange'], ['Cyane', 'Lille', 'green']]
  return (
    <Panes>
      {(['dark', 'light'] as const).map((mode) => (
        <Pane key={mode} className={`theme-${mode}`} style={cssVars(vars)} data-preview={mode}>
          <Mode>{t(`settings.theme.${mode}`)}</Mode>
          <App>
            <Menu>
              <div className="brand"><div className="logo" /><span>{appName || 'Liberty'}</span></div>
              <Item $on><LayoutGrid size={13} /> {t('settings.theme.pvOverview')}</Item>
              <Item><Users size={13} /> {t('settings.theme.pvCustomers')}</Item>
              <Item><BarChart3 size={13} /> {t('settings.theme.pvFigures')}</Item>
            </Menu>
            <Page>
              <Title>{t('settings.theme.pvCustomers')}</Title>
              <Figures>
                <Figure><div className="n">128</div><div className="l">{t('settings.theme.pvCustomers')}</div></Figure>
                <Figure><div className="n">94 %</div><div className="l">{t('settings.theme.pvActive')}</div></Figure>
              </Figures>
              <Grid>
                <Line $head><span>{t('settings.theme.pvName')}</span><span>{t('settings.theme.pvCity')}</span><span>{t('settings.theme.pvStatus')}</span></Line>
                {rows.map(([name, city, tone], i) => (
                  <Line key={name} $on={i === 1}><span>{name}</span><span>{city}</span><span><Tag $tone={tone}>{t(tone === 'green' ? 'settings.theme.pvOpen' : 'settings.theme.pvHeld')}</Tag></span></Line>
                ))}
              </Grid>
              <FieldLine><span className="l">{t('settings.theme.pvName')}</span><div className="i">Borea</div></FieldLine>
              <Foot>
                <Button $variant="primary" $size="sm" type="button">{t('common.save')}</Button>
                <Button $variant="ghost" $size="sm" type="button">{t('common.cancel')}</Button>
                <Link>{t('settings.theme.sampleLink')}</Link>
              </Foot>
            </Page>
          </App>
        </Pane>
      ))}
    </Panes>
  )
}
