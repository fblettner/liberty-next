// Render-error boundary — the one React API that still needs a class component.
//
// Until A4.2 there was no boundary anywhere: a render error in one tab (a bad cell renderer,
// a malformed screen config) unmounted the whole app to a blank page. Each tab in TabHost and
// the Layout's <Outlet/> now sit inside one, so a broken tab shows its error (through the
// shared ErrorDetails panel) with a "Reload" button, while every other tab keeps working.
import { Component, type ErrorInfo, type ReactNode } from 'react'
import styled from '@emotion/styled'
import { withTranslation, type WithTranslation } from 'react-i18next'
import { RefreshCw } from 'lucide-react'
import { ErrorDetails } from './ErrorDetails'
import { Button } from './Button'
import { fontSize } from '../theme'

const Box = styled.div`
  display: flex;
  flex-direction: column;
  gap: 10px;
  padding: 16px;
  align-items: flex-start;
  font-size: ${fontSize.sm};
`

type Props = WithTranslation & {
  children: ReactNode
  /** Where the boundary sits, for the console line (e.g. the tab id). */
  label?: string
  /** Called on "Reload" after the boundary resets, so the host can remount / refetch. */
  onReset?: () => void
}

type State = { error: Error | null }

class ErrorBoundaryInner extends Component<Props, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo): void {
    console.error(`render error${this.props.label ? ` in ${this.props.label}` : ''}:`, error, info.componentStack)
  }

  private reset = () => {
    this.setState({ error: null })
    this.props.onReset?.()
  }

  render() {
    const { error } = this.state
    if (!error) return this.props.children
    const { t } = this.props
    const message = `${t('errors.renderFailed')}: ${error.message || String(error)}`
    return (
      <Box role="alert">
        <ErrorDetails message={message} />
        <Button type="button" onClick={this.reset}>
          <RefreshCw size={14} /> {t('common.reload')}
        </Button>
      </Box>
    )
  }
}

export const ErrorBoundary = withTranslation()(ErrorBoundaryInner)
