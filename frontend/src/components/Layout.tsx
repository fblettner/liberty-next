// App shell — sidebar + workspace title + a floating top-right utility pill
// (EN/FR, dark/light, profile, sign-out). Page content renders through <Outlet/>
// inside a <Suspense> boundary (routes are code-split). Adapted from nomaubl.
import { Suspense, useEffect, useState } from 'react'
import { Outlet, useMatch } from 'react-router-dom'
import styled from '@emotion/styled'
import { useTranslation } from 'react-i18next'
import { Sun, Moon, LogOut, User, Sparkles } from 'lucide-react'
import { colors, fontSize, fonts, radius, glass } from '../theme'
import { LANGUAGE_KEY, type Language } from '../i18n'
import { useAuth } from '../auth/AuthContext'
import { installAuthBuiltins } from '../pages/TableView/actionRunner'
import { useWorkspace } from '../workspace/WorkspaceContext'
import { Banner, Centered } from '../common'
import Sidebar from './Sidebar'
import ProfileModal from './ProfileModal'
import WorkspaceSelect from './WorkspaceSelect'
import TabStrip from './TabStrip'
import BackButton from './BackButton'
import TabHost from './TabHost'
import AiDrawer from './AiDrawer'

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

const UserName = styled.button`
  display: inline-flex;
  align-items: center;
  gap: 6px;
  height: 30px;
  padding: 0 8px;
  border: none;
  background: transparent;
  border-radius: ${radius.md};
  font-size: ${fontSize.sm};
  font-family: ${fonts.sans};
  color: ${colors.text.muted};
  max-width: 180px;
  cursor: pointer;
  transition: background 0.15s, color 0.15s;
  & > span { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  &:hover { background: var(--hover-subtle); color: ${colors.text.primary}; }
`

function readDark(): boolean {
  try {
    return localStorage.getItem(THEME_KEY) !== 'light' // default: dark
  } catch {
    return true
  }
}

export default function Layout() {
  const { t, i18n } = useTranslation()
  const { user, logout } = useAuth()
  const { license, refresh: refreshWorkspace } = useWorkspace()
  // a configured-but-broken key (expired / bad signature) is worth flagging; "no key" isn't (the open framework)
  const licenseBanner = license.mode === 'restricted' && license.error && !/no license key/i.test(license.error)
    ? license.error : null
  const [dark, setDark] = useState(readDark)
  const [lang, setLang] = useState<Language>(i18n.language === 'fr' ? 'fr' : 'en')
  const [profileOpen, setProfileOpen] = useState(false)
  // AI assistant drawer — open/close state for the right-side slide-in (replaces the
  // removed /chat full-page route + the sidebar entry). Toggled by the Sparkles button
  // in the utility bar below.
  const [aiOpen, setAiOpen] = useState(false)

  useEffect(() => {
    document.documentElement.classList.toggle('theme-light', !dark)
  }, [dark])

  // Install the auth + language built-ins for the action runner. ``ParamBind {source: '#LOGIN_USER#'}``
  // / ``#LOGIN_EMAIL#`` / ``#LANGUAGE#`` resolve to these values at run time; the date-bound
  // built-ins (``#SYSDATE#`` / ``#NOW#``) compute lazily from the JS clock. Re-installs whenever
  // the user logs in/out or the language changes — covers the post-login + post-switch races.
  useEffect(() => {
    installAuthBuiltins({
      username: user?.username ?? null,
      email: user?.email ?? null,
      language: i18n.language ?? null,
    })
  }, [user?.username, user?.email, i18n.language])

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
    // i18next's languageChanged event re-renders every useTranslation() consumer
    // for free, but server-rendered text (menu labels from /api/menus, column
    // labels + cell display rules baked into query results by the dictionary
    // layer) was fetched under the OLD Accept-Language. Without a refresh the
    // sidebar tree, tab titles, table headers, and lookup-resolved cells all
    // stay in the previous language until a full page reload — which is what
    // the operator was working around. Re-fetching the workspace state pulls
    // the menus/screens/dashboards under the new language; the TabHost listens
    // to the same i18next event + remounts its tabs so each TableView refetches
    // its query result with the new header too.
    refreshWorkspace()
  }

  // a `/sql/...`, `/http/...`, `/dashboard/...`, or `/nomaflow/runs/:id` route → show the tab
  // host (and the matching tab is active); a framework route → show its page through <Outlet/>,
  // with the tab host hidden underneath. (all useMatch calls must run unconditionally — Rules
  // of Hooks). nomaflow run detail is hosted as a workspace tab so a row-click from list_runs
  // adds a tab next to "Job Runs" instead of replacing the workspace view.
  const sqlMatch = useMatch('/sql/:connector/:target')
  const httpMatch = useMatch('/http/:connector/:target')
  const dashboardMatch = useMatch('/dashboard/:target')
  const nomaflowRunMatch = useMatch('/nomaflow/runs/:runId')
  const onTabRoute = !!(sqlMatch || httpMatch || dashboardMatch || nomaflowRunMatch)

  return (
    <Shell>
      <Sidebar />
      <MainArea>
        <TabStrip />
        {/* Back-affordance for drill-down navigation (row_click_route) — only renders when
            the operator got here via a row click that threaded ``location.state.from``.
            Clicking it closes the drill tab + navigates back to the source. */}
        <BackButton />
        <ContentArea>
          {licenseBanner && (
            <Banner $tone="error" style={{ margin: '12px 16px 0' }}>{t('license.banner', { error: licenseBanner })}</Banner>
          )}
          <Suspense fallback={<Centered />}>
            {/* Outlet renders the framework page (Connectors / Chat / Settings); on a tab route it
                renders the TabRoute marker (null) and TabHost below does the rendering. */}
            <Outlet />
            <TabHost hidden={!onTabRoute} />
          </Suspense>
        </ContentArea>
      </MainArea>

      <UtilityBar>
        <WorkspaceSelect />
        <UtilBtn $active={lang === 'en'} onClick={() => switchLang('en')} title="English">
          EN
        </UtilBtn>
        <UtilBtn $active={lang === 'fr'} onClick={() => switchLang('fr')} title="Français">
          FR
        </UtilBtn>
        <Sep />
        {/* AI assistant — toggles the right-side drawer (replaces /chat full-page route). */}
        <UtilBtn $active={aiOpen} onClick={() => setAiOpen((v) => !v)} title={t('nav.assistant', 'AI Assistant')}>
          <Sparkles size={14} />
        </UtilBtn>
        <Sep />
        <UtilBtn onClick={toggleTheme} title={dark ? t('common.lightMode') : t('common.darkMode')}>
          {dark ? <Moon size={14} /> : <Sun size={14} />}
        </UtilBtn>
        {user && (
          <>
            <Sep />
            <UserName onClick={() => setProfileOpen(true)} title={t('profile.title')}>
              <User size={14} />
              <span>
                {user.username}
                {user.is_superuser ? ' · superuser' : ''}
              </span>
            </UserName>
            <UtilBtn onClick={logout} title={t('common.signOut')}>
              <LogOut size={14} />
            </UtilBtn>
          </>
        )}
      </UtilityBar>

      {profileOpen && <ProfileModal onClose={() => setProfileOpen(false)} />}
      <AiDrawer open={aiOpen} onClose={() => setAiOpen(false)} />
    </Shell>
  )
}
