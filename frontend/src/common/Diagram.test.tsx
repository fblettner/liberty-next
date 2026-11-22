// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { Diagram } from './Diagram'

// (the canvas's zoom buttons take their titles from the app's texts: not what is tested here)
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }))

// what liberty/diagram.py (lay_out) answers: two tiers, a host in each, a link between a host and a card
const LAYOUT = {
  width: 416, height: 130, note: 'Read at 10:42.', evidences: ['sessions'],
  groups: [
    { id: 'web', label: 'Web', kind: 'tier', parent: null, declared: true, x: 10, y: 10, w: 168, h: 110 },
    { id: 'data', label: 'Database', kind: 'tier', parent: null, declared: true, x: 238, y: 10, w: 168, h: 110 },
    { id: 'h:web01', label: 'web01.example.com', kind: 'host', parent: 'web', declared: true, x: 18, y: 40, w: 152, h: 72 },
    { id: 'h:db01', label: 'db01.example.com', kind: 'host', parent: 'data', declared: false, x: 246, y: 40, w: 152, h: 72 },
  ],
  nodes: [
    { id: 'html1', group: 'h:web01', kind: 'web', label: 'html_1', detail: 'Web server', state: 'ok', declared: true, badges: [{ label: 'Version', value: '9.2.8' }], x: 24, y: 56, w: 140, h: 50 },
    { id: 'db', group: 'h:db01', kind: 'database', label: 'ORCL', detail: 'Oracle 19', state: 'unknown', declared: false, badges: [], x: 252, y: 56, w: 140, h: 50 },
  ],
  links: [{ from: 'h:web01', to: 'db', label: '40 sessions', evidence: 'sessions', path: 'M 170.0 76.0 C 211.0 76.0, 211.0 81.0, 252.0 81.0', arrow: 'M 252.0 81.0 L 247.0 78.2 L 247.0 83.8 Z', x: 211, y: 78.5 }],
}

;(globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean }).IS_REACT_ACT_ENVIRONMENT = true
let root: Root | null = null
function draw(node: React.ReactNode): HTMLElement {
  const box = document.createElement('div')
  document.body.appendChild(box)
  root = createRoot(box)
  act(() => { root!.render(node) })
  return box
}
afterEach(() => { act(() => { root?.unmount() }); root = null; document.body.innerHTML = '' })

describe('the diagram, drawn', () => {
  it('draws every box and card where the server placed it', () => {
    const box = draw(<Diagram layout={LAYOUT} bare />)
    const board = box.querySelector<HTMLElement>('[data-diagram]')!
    expect([board.style.width, board.style.height, board.dataset.diagram]).toEqual(['416px', '130px', '2'])
    const card = box.querySelector<HTMLElement>('[data-diagram-node="html1"]')!
    expect([card.style.left, card.style.top, card.style.width, card.style.height]).toEqual(['24px', '56px', '140px', '50px'])
    expect(card.textContent).toBe('html_1Web serverOK · Version 9.2.8')                     // its state as a word, beside the mark
    const host = box.querySelector<HTMLElement>('[data-diagram-group="h:db01"]')!
    expect([host.style.left, host.style.width, host.dataset.declared, host.textContent]).toEqual(['246px', '152px', 'false', 'db01.example.com'])
    expect(box.querySelector<HTMLElement>('[data-diagram-node="db"]')!.dataset.declared).toBe('false')
    expect([...box.querySelectorAll('svg path')].map((p) => p.getAttribute('d'))).toEqual([LAYOUT.links[0].path, LAYOUT.links[0].arrow])
    const chip = box.querySelector<HTMLElement>('[data-diagram-link="h:web01>db"]')!
    expect([chip.textContent, chip.style.left, chip.style.top]).toEqual(['40 sessions', '211px', '78.5px'])
    expect(box.querySelector('[data-diagram-legend]')).toBeNull()                           // bare: the drawing alone
  })
  it('calls onPick with the card, and writes the caller\'s words in the card and the legend', () => {
    const onPick = vi.fn()
    const box = draw(<Diagram layout={LAYOUT} picked="db" onPick={onPick} words={{ ok: 'En marche', undeclared: 'Non déclaré', unknown: '' }} />)
    act(() => { box.querySelector<HTMLElement>('[data-diagram-node="html1"]')!.click() })
    expect(onPick).toHaveBeenCalledTimes(1)
    expect(onPick.mock.calls[0][0]).toMatchObject({ id: 'html1', label: 'html_1', state: 'ok', x: 24 })
    expect(box.querySelector('[data-diagram-node="html1"]')!.textContent).toContain('En marche')
    // the legend: the states that occur, what is not declared, each evidence, the note — an empty word keeps the default
    expect([...box.querySelectorAll('[data-diagram-legend] > span')].map((s) => s.textContent)).toEqual(['En marche', 'Not known', 'Non déclaré', 'sessions', 'Read at 10:42.'])
  })
  it('draws nothing of what is not a diagram', () => {
    expect(draw(<Diagram layout={{ advice: [] }} />).innerHTML).toBe('')
  })
})
