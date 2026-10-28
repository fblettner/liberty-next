// A block of text (Markdown) on a dashboard — an overview's introduction. No query; the heavy
// Markdown renderer is a lazy chunk, loaded when the first text widget shows.
import { lazy, Suspense } from 'react'
import styled from '@emotion/styled'
import { SpinnerRing } from '../../common'
import type { TextWidgetWire } from '../../types/dashboards'
import { Card, Sub, Title } from './cardParts'
import { colors, fontSize, fonts } from '../../theme'

const Markdown = lazy(() => import('../../common/Markdown').then((m) => ({ default: m.Markdown })))

const Body = styled.div`
  font-family: ${fonts.sans}; font-size: ${fontSize.base}; color: ${colors.text.secondary}; line-height: 1.55; overflow: auto; min-height: 0;
  & > :first-child { margin-top: 0; } & > :last-child { margin-bottom: 0; }
  & h1, & h2, & h3 { color: ${colors.text.primary}; margin: 0.6em 0 0.3em; }
  & p { margin: 0.4em 0; }
  & ul, & ol { margin: 0.3em 0; padding-left: 1.3em; }
  & a { color: ${colors.blue.main}; }
`

export function TextWidget({ widget }: { widget: TextWidgetWire }) {
  return (
    <Card style={{ flex: '0 0 auto' }}>
      {(widget.label || widget.description) && (
        <div>
          {widget.label && <Title>{widget.label}</Title>}
          {widget.description && <Sub>{widget.description}</Sub>}
        </div>
      )}
      <Body>
        <Suspense fallback={<SpinnerRing size={16} thickness={2} />}>
          <Markdown>{widget.markdown}</Markdown>
        </Suspense>
      </Body>
    </Card>
  )
}
