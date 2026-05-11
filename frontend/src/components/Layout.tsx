// App shell — sidebar + workspace title + a floating top-right utility pill
// (EN/FR, dark/light, user, sign-out). Page content renders through <Outlet/>.
// Adapted from nomaubl's App layout.
import { useEffect, useState } from 'react'
import { Outlet } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Sun, Moon, LogOut } from 'lucide-react'
import { colors, fontSize, fonts, radius, glass } from '../theme'
import { LANGUAGE_KEY, type Language } from '../i18n'
import { useAuth } from '../auth'
import Sidebar from './Sidebar'

const THEME_KEY = 'liberty.theme'

const Shell = styled.div`
  display: flex;
  height: 100vh;
  overflow: hidden;
  color: ${colors.text.primary};
  font-family: ${fonts.sans};
`

const MainArea = styled.main`
  flex: 1;
  min-width: 0;
  min-height: 0;
  display: flex;
  flex-direction: column;
  overflow: hidden;
`

const WorkspaceHeader = styled.div`
  display: flex;
  align-items: baseline;
  gap: 10px;
  padding: 14px 20px 8px;
  flex-shrink: 0;
`

const WorkspaceTitle = styled.h1`
  font-size: ${fontSize['2xl']};
  font-weight: 700;
  letter-spacing: -0.3px;
  line-height: 1;
  margin: 0;
`
const Accent = styled.span`color: ${colors.blue.main};`
const Sub = styled.span`
  font-size: ${fontSize.base};
  font-weight: 400;
  color: ${colors.text.muted};
`

const ContentArea = styled.div`
  flex: 1;
  min-height: 0;
  min-width: 0;
  overflow: hidden;
  display: flex;
  flex-direction: column;
`

const UtilityBar = styled.div`
  position: fixed;
  top: 12px;
  right: 16px;
  z-index: 90;
  display: flex;
  align-items: center;
  gap: 2px;
  background: var(--ghost-bg, ${colors.bg.card});
  border: 1px solid ${colors.border};
  border-radius: ${radius.lg};
  padding: 3px;
  ${glass.surface}
`

const UtilBtn = styled.button<{ $active?: boolean }>`
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 5px;
  height: 30px;
  min-width: 30px;
  padding: 0 9px;
  border-radius: ${radius.md};
  border: none;
  background: ${({ $active }) => ($active ? colors.blue.bg : 'transparent')};
  color: ${({ $active }) => ($active ? colors.text.primary : colors.text.muted)};
  font-size: ${fontSize.sm};
  font-family: ${fonts.sans};
  font-weight: 500;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`

const Sep = styled.div`
  width: 1px;
  height: 16px;
  background: ${colors.border};
  margin: 0 2px;
`

const UserName = styled.span`
  font-size: ${fontSize.sm};
  color: ${colors.text.muted};
  max-width: 150px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  padding: 0 4px;
`

function readDark(): boolean {
  try {
    return localStorage.getItem(THEME_KEY) !== 'light' // default: dark
  } catch {
    return true
  }
}

export function Layout() {
  const { t, i18n } = useTranslation()
  const { user, logout } = useAuth()
  const [dark, setDark] = useState(readDark)
  const [lang, setLang] = useState<Language>(i18n.language === 'fr' ? 'fr' : 'en')

  useEffect(() => {
    document.documentElement.classList.toggle('theme-light', !dark)
  }, [dark])

  const toggleTheme = () => {
    setDark((d) => {
      const next = !d
      try {
        localStorage.setItem(THEME_KEY, next ? 'dark' : 'light')
      } catch {
        /* ignore */
      }
      return next
    })
  }

  const switchLang = (l: Language) => {
    void i18n.changeLanguage(l)
    try {
      localStorage.setItem(LANGUAGE_KEY, l)
    } catch {
      /* ignore */
    }
    setLang(l)
  }

  return (
    <Shell>
      <Sidebar />
      <MainArea>
        <WorkspaceHeader>
          <WorkspaceTitle>
            <Accent>{t('app.title')}</Accent> <Sub>{t('app.subtitle')}</Sub>
          </WorkspaceTitle>
        </WorkspaceHeader>
        <ContentArea>
          <Outlet />
        </ContentArea>
      </MainArea>

      <UtilityBar>
        <UtilBtn $active={lang === 'en'} onClick={() => switchLang('en')} title="English">
          EN
        </UtilBtn>
        <UtilBtn $active={lang === 'fr'} onClick={() => switchLang('fr')} title="Français">
          FR
        </UtilBtn>
        <Sep />
        <UtilBtn onClick={toggleTheme} title={dark ? t('common.lightMode') : t('common.darkMode')}>
          {dark ? <Moon size={14} /> : <Sun size={14} />}
        </UtilBtn>
        {user && (
          <>
            <Sep />
            <UserName title={user.username}>
              {user.username}
              {user.is_superuser ? ' · superuser' : ''}
            </UserName>
            <UtilBtn onClick={logout} title={t('common.signOut')}>
              <LogOut size={14} />
            </UtilBtn>
          </>
        )}
      </UtilityBar>
    </Shell>
  )
}
