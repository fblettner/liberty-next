// Markdown renderer for assistant replies — react-markdown + remark-gfm, with
// emotion-styled elements that match the app theme. (nomaubl renders AI output
// the same way.)
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import styled from '@emotion/styled'
import { colors, fontSize, fonts, radius } from '../theme'

const Body = styled.div`
  font-size: ${fontSize.md};
  line-height: 1.6;
  color: ${colors.text.secondary};
  word-break: break-word;

  & > :first-of-type { margin-top: 0; }
  & > :last-child { margin-bottom: 0; }

  h1, h2, h3, h4 { color: ${colors.text.primary}; font-weight: 700; line-height: 1.3; margin: 14px 0 8px; }
  h1 { font-size: ${fontSize.xl}; }
  h2 { font-size: ${fontSize.lg}; }
  h3 { font-size: ${fontSize.md}; }
  h4 { font-size: ${fontSize.base}; }

  p { margin: 0 0 8px; }
  ul, ol { margin: 0 0 8px; padding-left: 22px; }
  li { margin: 2px 0; }
  li > p { margin: 0; }

  a { color: ${colors.blue.main}; text-decoration: none; }
  a:hover { text-decoration: underline; }

  strong { color: ${colors.text.primary}; font-weight: 600; }
  hr { border: none; border-top: 1px solid ${colors.border}; margin: 12px 0; }

  blockquote {
    margin: 0 0 8px;
    padding: 2px 12px;
    border-left: 3px solid ${colors.border};
    color: ${colors.text.muted};
  }

  code {
    font-family: ${fonts.mono};
    font-size: 0.92em;
    padding: 1px 5px;
    border-radius: ${radius.sm};
    background: ${colors.bg.input};
    color: ${colors.blue.main};
  }
  pre {
    margin: 0 0 8px;
    padding: 10px 12px;
    border-radius: ${radius.md};
    border: 1px solid ${colors.border};
    background: ${colors.bg.input};
    overflow-x: auto;
  }
  pre code { padding: 0; background: transparent; color: ${colors.text.secondary}; }

  table { border-collapse: collapse; margin: 0 0 8px; font-size: ${fontSize.base}; }
  th, td { border: 1px solid ${colors.border}; padding: 4px 8px; text-align: left; }
  th { background: ${colors.bg.input}; color: ${colors.text.secondary}; font-weight: 600; }
`

export function Markdown({ children }: { children: string }) {
  return (
    <Body>
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={{
          a: ({ node: _node, ...props }) => <a {...props} target="_blank" rel="noreferrer" />,
        }}
      >
        {children}
      </ReactMarkdown>
    </Body>
  )
}

export default Markdown
