// What a ``url`` / ``email`` / ``color`` column shows (services/cells.formatLink): a link opening
// the address in a new tab, a ``mailto:``, or a swatch beside the code — in a grid cell, and beside
// the record form's input. A click on a link never selects / opens the row.
import styled from '@emotion/styled'
import { ExternalLink, Mail } from 'lucide-react'
import { colors, radius } from '../../theme'

export const FormatLink = styled.a`
  color: ${colors.blue.main}; text-decoration: none;
  &:hover { text-decoration: underline; }
`
export const Swatch = styled.span`
  display: inline-block; width: 12px; height: 12px; margin-right: 6px; vertical-align: -1px; flex-shrink: 0;
  border-radius: ${radius.sm}; border: 1px solid ${colors.border};
`

/** The grid cell's content for a formatted value. */
export function FormatValue({ text, kind, href }: { text: string; kind: 'url' | 'email' | 'color'; href: string }) {
  if (kind === 'color') return <><Swatch style={{ background: href }} />{text}</>
  return (
    <FormatLink href={href} target={kind === 'url' ? '_blank' : undefined} rel="noopener noreferrer"
      onClick={(e) => e.stopPropagation()} title={href}>
      {text}
    </FormatLink>
  )
}

const OpenBtn = styled.a`
  position: absolute; top: 50%; transform: translateY(-50%); right: 4px; width: 26px; height: 26px;
  display: inline-flex; align-items: center; justify-content: center; border-radius: ${radius.sm};
  color: ${colors.text.muted};
  &:hover { background: var(--hover-subtle); color: ${colors.blue.main}; }
`

/** The record form's open button for a ``url`` / ``email`` input (placed in a ``position: relative``
 *  wrapper, the input padded on the right). */
export function OpenFormatted({ kind, href, title }: { kind: 'url' | 'email'; href: string; title: string }) {
  return (
    <OpenBtn href={href} target={kind === 'url' ? '_blank' : undefined} rel="noopener noreferrer" title={title} aria-label={title}>
      {kind === 'url' ? <ExternalLink size={14} /> : <Mail size={14} />}
    </OpenBtn>
  )
}
