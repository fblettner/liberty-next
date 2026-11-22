// What a refused save shows in a builder: one line per problem — where it is, in the labels of
// what holds it, and what is wrong — with "Open" where the builder can take the user to the object.
// An error that lists no problem (a network failure, a rule a loader checks by hand) is the plain strip.
import { useTranslation } from 'react-i18next'
import styled from '@emotion/styled'
import { Banner } from './Banner'
import { fontSize } from '../theme'
import { messageKey, type SaveFailure, type SaveProblem } from '../services/saveProblems'

const List = styled.ul`
  list-style: none; margin: 6px 0 0; padding: 0; display: flex; flex-direction: column; gap: 4px;
`
const Line = styled.li`
  display: flex; align-items: baseline; gap: 8px; flex-wrap: wrap; font-size: ${fontSize.base};
`
const Where = styled.span`
  font-weight: 600;
`
const Open = styled.button`
  background: none; border: none; padding: 0; cursor: pointer; color: inherit; font: inherit;
  font-size: ${fontSize.sm}; text-decoration: underline; opacity: 0.85;
  &:hover { opacity: 1; }
`
const Plain = styled.span`
  white-space: pre-line;
`

export interface SaveProblemsProps {
  /** What the failed save left (``failureOf(e)``); nothing is drawn for null. */
  failure: SaveFailure | null
  /** What takes the user to the object a problem points at, or null when this builder cannot
   *  (the line then has no "Open"). */
  opener?: (problem: SaveProblem) => (() => void) | null
}

export function SaveProblems({ failure, opener }: SaveProblemsProps) {
  const { t } = useTranslation()
  if (!failure) return null
  if (failure.problems.length === 0) return <Banner $tone="error"><Plain>{failure.message}</Plain></Banner>
  const said = (p: SaveProblem): string => {
    const k = messageKey(p)
    return k ? t(k.key, { name: k.name }) : p.message
  }
  return (
    <Banner $tone="error" role="alert" data-save-problems>
      <div>{t('saveProblems.title', { count: failure.problems.length })}</div>
      <List>
        {failure.problems.map((p, i) => {
          const open = opener?.(p) ?? null
          return (
            <Line key={i}>
              {p.where && <Where>{p.where}</Where>}
              <span>{said(p)}</span>
              {open && <Open type="button" onClick={open}>{t('saveProblems.open')}</Open>}
            </Line>
          )
        })}
      </List>
    </Banner>
  )
}
