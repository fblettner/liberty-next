// A docstring rendered for reading (Developer reference): paragraphs with the hard wrapping undone,
// ``code`` as inline code, *emphasis* in italics, - / * lists as lists (services/devref.docBlocks).
import styled from '@emotion/styled'
import { docBlocks, type DocPart } from '../../services/devref'
import { colors, fontSize, fonts, radius } from '../../theme'

const Wrap = styled.div`
  color: ${colors.text.secondary}; font-size: ${fontSize.sm}; line-height: 1.55;
  & p { margin: 0 0 8px; }
  & ul { margin: 0 0 8px; padding-left: 18px; }
  & li { margin: 2px 0; }
  & code { font-family: ${fonts.mono}; font-size: 0.92em; color: ${colors.text.primary}; background: ${colors.bg.input};
    border: 1px solid ${colors.border}; border-radius: ${radius.sm}; padding: 0 4px; }
`

function Parts({ parts }: { parts: DocPart[] }) {
  return <>{parts.map((p, i) => (p.code ? <code key={i}>{p.text}</code> : p.em ? <em key={i}>{p.text}</em> : <span key={i}>{p.text}</span>))}</>
}

export function Doc({ text }: { text: string }) {
  const blocks = docBlocks(text)
  if (!blocks.length) return null
  return (
    <Wrap>
      {blocks.map((b, i) => (b.kind === 'p'
        ? <p key={i}><Parts parts={b.parts} /></p>
        : <ul key={i}>{b.items.map((it, j) => <li key={j}><Parts parts={it} /></li>)}</ul>))}
    </Wrap>
  )
}
