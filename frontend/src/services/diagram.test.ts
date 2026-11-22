import { describe, expect, it } from 'vitest'
import { badgeLine, boxesOf, endLabel, evidenceRank, legendOf, linksOf, readDiagram, stateOf } from './diagram'

// what liberty/diagram.py (lay_out) answers, cut down to two tiers
const RAW = {
  width: 480, height: 200, note: 'Read at 10:42.',
  groups: [
    { id: 'web', label: 'Web', kind: 'tier', parent: null, declared: true, x: 10, y: 10, w: 168, h: 160 },
    { id: 'data', label: 'Database', kind: 'tier', parent: null, declared: true, x: 238, y: 10, w: 168, h: 160 },
    { id: 'h:web01', label: 'web01.example.com', kind: 'host', parent: 'web', declared: true, x: 18, y: 40, w: 152, h: 122 },
    { id: 'h:db01', label: 'db01.example.com', kind: 'host', parent: 'data', declared: false, x: 246, y: 40, w: 152, h: 72 },
  ],
  nodes: [
    { id: 'html1', group: 'h:web01', kind: 'web', label: 'html_1', detail: 'Web server', state: 'ok', declared: true,
      badges: [{ label: 'Version', value: '9.2.8' }, { label: 'Users', value: 12 }, { label: 'Home', value: '/u01' }], x: 24, y: 56, w: 140, h: 50 },
    { id: 'ais1', group: 'h:web01', kind: 'ais', label: 'ais_1', detail: 'AIS server', state: 'down', declared: true, badges: [], x: 24, y: 112, w: 140, h: 50 },
    { id: 'db', group: 'h:db01', kind: 'database', label: 'ORCL', detail: 'Oracle 19', state: 'unknown', declared: false, badges: [{ label: 'Sessions', value: '184' }], x: 252, y: 56, w: 140, h: 50 },
  ],
  links: [
    { from: 'h:web01', to: 'db', label: '40 sessions', evidence: 'sessions', path: 'M 170.0 101.0 C 208.0 101.0, 214.0 81.0, 252.0 81.0', arrow: 'M 252.0 81.0 L 247.0 78.2 L 247.0 83.8 Z', x: 211, y: 91 },
    { from: 'ais1', to: 'html1', label: '', evidence: 'same host', path: 'M 164.0 137.0 C 186.0 137.0, 186.0 81.0, 164.0 81.0', arrow: 'M 164.0 81.0 L 169.0 78.2 L 169.0 83.8 Z', x: 180.5, y: 109 },
  ],
  evidences: ['sessions', 'same host'],
}

describe('a diagram laid out by the server', () => {
  const d = readDiagram(RAW)!
  it('is read as it was sent: the places are the server\'s', () => {
    expect(d.width).toBe(480)
    expect(d.groups.map((g) => [g.id, g.parent, g.declared, g.x, g.w])).toEqual([
      ['web', null, true, 10, 168], ['data', null, true, 238, 168], ['h:web01', 'web', true, 18, 152], ['h:db01', 'data', false, 246, 152]])
    expect(d.nodes[0]).toMatchObject({ id: 'html1', state: 'ok', declared: true, x: 24, y: 56, w: 140, h: 50 })
    expect(d.nodes[0].badges[1]).toEqual({ label: 'Users', value: '12' })               // a number is written
    expect(d.links.map((k) => [k.from, k.to, k.label])).toEqual([['h:web01', 'db', '40 sessions'], ['ais1', 'html1', '']])
    expect(d.note).toBe('Read at 10:42.')
  })
  it('says no to what is not a diagram, and leaves out what cannot be drawn', () => {
    expect(readDiagram(null)).toBeNull()
    expect(readDiagram({ advice: [] })).toBeNull()
    expect(readDiagram([])).toBeNull()
    const odd = readDiagram({
      ...RAW,
      nodes: [...RAW.nodes, { id: 'lost', label: 'no place' }, { label: 'no id', x: 0, y: 0, w: 1, h: 1 }, 'text', { id: 'odd', state: 'exploded', x: '5', y: 0, w: 10, h: 10 }],
      links: [...RAW.links, { from: 'html1', to: 'gone', path: 'M 0 0' }, { from: 'html1', to: 'db', path: 'M 0 0" onload="x' }, { from: 'html1', to: 'db', path: '' },
        { from: 'html1', to: 'db', path: 'M 1.0 2.0 L 3.0 4.0', evidence: 'late' }],
      evidences: 'none',
    })!
    expect(odd.nodes.map((n) => n.id)).toEqual(['html1', 'ais1', 'db', 'odd'])
    expect(odd.nodes[3]).toMatchObject({ state: 'unknown', x: 5, declared: true, badges: [] })
    expect(odd.links).toHaveLength(3)                                                    // a link to nothing, a path that is not one, a link without a line: not drawn
    expect(odd.evidences).toEqual(['sessions', 'same host', 'late'])                     // an evidence the list forgot still has its rank
    expect(readDiagram({ groups: [], nodes: [], width: 'wide' })).toEqual({ width: 0, height: 0, groups: [], nodes: [], links: [], evidences: [], note: '' })
  })
  it('knows a state from anything else', () => {
    expect(['ok', 'warn', 'down', 'unknown', 'RUNNING', undefined].map(stateOf)).toEqual(['ok', 'warn', 'down', 'unknown', 'unknown', 'unknown'])
  })
  it('gives a card the links of the boxes it sits in', () => {
    const html = d.nodes[0], ais = d.nodes[1]
    expect(boxesOf(d, html).map((g) => g.id)).toEqual(['h:web01', 'web'])
    // the host's sessions are its cards'; the link between the two cards is theirs
    expect(linksOf(d, 'html1').map((k) => `${k.from}>${k.to}`)).toEqual(['h:web01>db', 'ais1>html1'])
    expect(linksOf(d, ais.id)).toHaveLength(2)
    expect(linksOf(d, 'db').map((k) => k.from)).toEqual(['h:web01'])
    expect(linksOf(d, 'nobody')).toEqual([])
    expect([endLabel(d, 'h:web01'), endLabel(d, 'db'), endLabel(d, 'gone')]).toEqual(['web01.example.com', 'ORCL', 'gone'])
  })
  it('ranks the evidences, writes the badges, lists the legend', () => {
    expect([evidenceRank(d, 'sessions'), evidenceRank(d, 'same host'), evidenceRank(d, ''), evidenceRank(d, 'other')]).toEqual([0, 1, -1, -1])
    expect(badgeLine(d.nodes[0])).toBe('Version 9.2.8 · Users 12')
    expect(badgeLine(d.nodes[0], 1)).toBe('Version 9.2.8')
    expect(badgeLine({ badges: [{ label: 'Empty', value: '' }, { label: '', value: 'alone' }] })).toBe('alone')
    expect(legendOf(d)).toEqual({ states: ['ok', 'down', 'unknown'], undeclared: true, evidences: ['sessions', 'same host'] })
    expect(legendOf(readDiagram({ groups: [], nodes: [] })!)).toEqual({ states: [], undeclared: false, evidences: [] })
  })
})
